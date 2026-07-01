"""Реальный SIP-звонок с полным голосовым диалогом (Стадия 1 доработок).

Использует pyVoIP для дозвона, dialog.py для логики разговора,
tts.py для голоса бота, stt.py для распознавания ответов кандидата.

ВАЖНО про формат аудио pyVoIP:
  write_audio()/read_audio() работают с 8-bit UNSIGNED linear PCM (0..255,
  тишина=128) — это внутренний формат pyVoIP до/после G.711 (PCMU/PCMA)
  кодирования (стандартное «телефонное» качество звука, то же самое что
  обычный звонок по городской линии — не баг, так работает вся телефония).
  Наши TTS/STT работают с обычным 16-bit signed PCM (стандарт WAV/Vosk).
  На границе конвертируем через audioop:
    16-bit → 8-bit unsigned: lin2lin(16→8) + bias(+128)
    8-bit unsigned → 16-bit: bias(-128) + lin2lin(8→16)

Запуск (одиночный тестовый звонок, ручная проверка):
    python voicecall/phone_call.py <НОМЕР> [scenario_id]
    python voicecall/phone_call.py +79991234567 tander-sterlitamak-pack
"""
import audioop
import ctypes
import json
import os
import queue
import re
import select
import socket
import sys
import time
from typing import Optional

import call_api

if sys.platform == "win32":
    # Windows по умолчанию будит спящие потоки с точностью системного
    # таймера ~15.6мс. Фоновый поток pyVoIP, который шлёт RTP-пакеты раз в
    # 20мс (trans()), из-за этого получает реальную паузу то короче, то
    # заметно длиннее расчётной — и это накапливается в щелчки/склейки в
    # голосе бота (объективно измерено: десятки разрывов сигнала за фразу).
    # timeBeginPeriod(1) — стандартный для Windows-приложений с реальным
    # временем (аудио/видео) способ поднять точность таймера до ~1мс на
    # время работы процесса. Действует глобально, поэтому вызываем один
    # раз при импорте модуля и не откатываем до конца процесса.
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass

# Диагностические принты каждого исходящего SIP-пакета и каждого 100-го
# входящего RTP-пакета — были нужны только чтобы отследить баг с ACK при
# прямом исходящем звонке (решено, см. run_call_via_bridge). В обычном
# режиме держим их выключенными: лишние print() в фоновых потоках
# recv/trans создают конкуренцию за GIL и портят точность таймингов
# отправки аудио (реальная причина «шипения» в звонках с полным DEBUG).
VERBOSE_NETWORK_LOG = os.environ.get("VOICECALL_VERBOSE_NET") == "1"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from _sip_config import get_local_ip, load_env, require
from dialog import DialogSession, load_scenario, DEFAULT_SCENARIO, vocab_for_step, render_name, all_reask_texts, is_voicemail_phrase
from tts import synthesize_telephony_pcm, prewarm_scenario, DEFAULT_VOICE
from stt import StreamingRecognizer, warmup as stt_warmup

try:
    try:
        from pyVoIP.VoIP import VoIPPhone  # type: ignore
    except ImportError:
        from pyVoIP.VoIP.phone import VoIPPhone  # type: ignore
except ImportError as e:
    print(f"❌ pyVoIP не установлена: {e}", file=sys.stderr)
    sys.exit(1)

import pyVoIP  # type: ignore
from pyVoIP.SIP import SIPClient, SIPMessage, SIPStatus  # type: ignore

# pyVoIP предупреждает в исходниках (__init__.py) про дребезг таймера
# RTP-передатчика на Windows и рекомендует TRANSMIT_DELAY_REDUCTION=0.75
# как фикс. На практике это сделало звук ЕЩЁ хуже (см. тест 2026-07-01) —
# похоже, наша проблема другой природы. Оставляем библиотеку в дефолте.


def _patch_pyvoip_proxy_auth() -> None:
    """pyVoIP 1.6.8 умеет отвечать только на вызов-аутентификацию 401
    (WWW-Authenticate). Novofon на INVITE присылает 407 Proxy Authentication
    Required (Proxy-Authenticate) — по сути тот же digest-challenge, но
    другой код статуса и другое имя заголовка. Без патча invite() не
    распознаёт 407 как повод переспросить с авторизацией и просто зависает
    в ожидании следующего (уже не придёт) ответа сервера.
    Патчим два места:
      1. SIPMessage.parse_header — чтобы Proxy-Authenticate парсился в
         self.authentication так же, как WWW-Authenticate.
      2. SIPClient.invite — чтобы 407 останавливал цикл ожидания так же,
         как 401, и повторный INVITE слался с Proxy-Authorization вместо
         Authorization (как требует RFC 3261 §22.3 для proxy-авторизации)."""
    orig_parse_header = SIPMessage.parse_header

    def patched_parse_header(self, header, data):
        if header == "Proxy-Authenticate":
            clean = data.replace("Digest ", "")
            row_data = self.auth_match.findall(clean)
            header_data = {var: val.strip('"') for var, val in row_data}
            self.headers[header] = header_data
            self.authentication = header_data
            return
        return orig_parse_header(self, header, data)

    SIPMessage.parse_header = patched_parse_header

    def patched_invite(self, number, ms, sendtype):
        branch = "z9hG4bK" + self.gen_call_id()[0:25]
        call_id = self.gen_call_id()
        sess_id = self.sessID.next()
        invite = self.gen_invite(number, str(sess_id), ms, sendtype, branch, call_id)
        with self.recvLock:
            self.out.sendto(invite.encode("utf8"), (self.server, self.port))
            pyVoIP.debug("Invited")
            response = SIPMessage(self.s.recv(8192))

            while (
                response.status != SIPStatus(401)
                and response.status != SIPStatus(407)
                and response.status != SIPStatus(100)
                and response.status != SIPStatus(180)
            ) or response.headers["Call-ID"] != call_id:
                if not self.NSD:
                    break
                self.parse_message(response)
                response = SIPMessage(self.s.recv(8192))

            if response.status == SIPStatus(100) or response.status == SIPStatus(180):
                return SIPMessage(invite.encode("utf8")), call_id, sess_id
            pyVoIP.debug(f"Received Response: {response.summary()}")
            ack = self.gen_ack(response)
            self.out.sendto(ack.encode("utf8"), (self.server, self.port))
            pyVoIP.debug("Acknowledged")
            authhash = self.gen_authorization(response)
            nonce = response.authentication["nonce"]
            realm = response.authentication["realm"]
            auth_header = (
                "Proxy-Authorization" if response.status == SIPStatus(407) else "Authorization"
            )
            auth = (
                f'{auth_header}: Digest username="{self.username}",realm='
                + f'"{realm}",nonce="{nonce}",uri="sip:{self.server};'
                + f'transport=UDP",response="{str(authhash, "utf8")}",'
                + "algorithm=MD5\r\n"
            )

            invite = self.gen_invite(number, str(sess_id), ms, sendtype, branch, call_id)
            invite = invite.replace("\r\nContent-Length", f"\r\n{auth}Content-Length")

            self.out.sendto(invite.encode("utf8"), (self.server, self.port))

            return SIPMessage(invite.encode("utf8")), call_id, sess_id

    SIPClient.invite = patched_invite


def _patch_pyvoip_ack_tag() -> None:
    """pyVoIP 1.6.8: gen_ack() ставит в заголовок To СЛУЧАЙНЫЙ новый тег
    (self.gen_tag()) вместо тега, который реально прислал сервер в своём
    200 OK (request.headers['To']['tag']). По RFC 3261 ACK на 2xx-ответ
    обязан повторить именно этот тег — иначе сервер не может сопоставить
    ACK с установленным диалогом.

    Второй баг того же метода: Via-branch для ACK всегда берётся из
    полученного ответа (то есть равен branch исходного INVITE). Это верно
    для ACK на 401/407 (запрос повторяется в рамках той же транзакции), но
    НЕВЕРНО для ACK на 200 OK — по RFC 3261 §13.2.2.4 такой ACK образует
    отдельную транзакцию и обязан иметь новый уникальный branch. Если он
    совпадает с branch INVITE, сервер может не распознать ACK как
    подтверждение установленного диалога и продолжает ретранслировать
    200 OK, пока не сдастся и не оборвёт звонок — это и есть причина
    тишины на линии: без принятого ACK Novofon не поднимает голосовой мост."""
    def patched_gen_ack(self, request):
        tag = self.tagLibrary[request.headers["Call-ID"]]
        t = request.headers["To"]["raw"].strip("<").strip(">")
        to_tag = request.headers["To"]["tag"] or self.gen_tag()
        is_final_ok = request.status == SIPStatus(200)

        via = self._gen_response_via_header(request)
        if is_final_ok:
            new_branch = self.gen_branch()
            via = re.sub(r"branch=[^;\r\n]+", f"branch={new_branch}", via)

        ackMessage = f"ACK {t} SIP/2.0\r\n"
        ackMessage += via
        ackMessage += "Max-Forwards: 70\r\n"
        ackMessage += f"To: {request.headers['To']['raw']};tag={to_tag}\r\n"
        ackMessage += f"From: {request.headers['From']['raw']};tag={tag}\r\n"
        ackMessage += f"Call-ID: {request.headers['Call-ID']}\r\n"
        ackMessage += f"CSeq: {request.headers['CSeq']['check']} ACK\r\n"
        ackMessage += f"User-Agent: pyVoIP {pyVoIP.__version__}\r\n"
        ackMessage += "Content-Length: 0\r\n\r\n"
        return ackMessage

    SIPClient.gen_ack = patched_gen_ack


def _patch_rtp_logging() -> None:
    """Диагностика: печатаем факт получения любого RTP-пакета от сервера,
    чтобы понять, доходит ли вообще медиа-поток от Novofon до нас (даже
    если это просто «комфортный шум»/тишина — важен сам факт получения
    пакетов, а не их содержимое)."""
    from pyVoIP import RTP  # type: ignore

    orig_parse_packet = RTP.RTPClient.parse_packet
    counters = {}

    def patched_parse_packet(self, packet):
        if VERBOSE_NETWORK_LOG:
            key = id(self)
            n = counters.get(key, 0) + 1
            counters[key] = n
            if n <= 3 or n % 100 == 0:
                print(f"<<< RTP packet #{n} received, {len(packet)} bytes", flush=True)
        return orig_parse_packet(self, packet)

    RTP.RTPClient.parse_packet = patched_parse_packet


def _patch_pcma_encode_bug() -> None:
    """pyVoIP 1.6.8: RTPClient.encode_packet() при preference == PCMA
    ошибочно вызывает encode_pcmu() (μ-law) вместо encode_pcma() (A-law) —
    хотя корректный encode_pcma() в библиотеке есть, просто не вызывается
    (копипаст-баг). Если для конкретного звонка договорился кодек PCMA
    (не PCMU), мы бы слали μ-law данные с RTP-заголовком, утверждающим что
    это A-law — на приёмной стороне это декодируется неправильным
    алгоритмом компандирования и звучит как резкое хриплое искажение.
    Заодно один раз логируем реально выбранный кодек — раньше это было
    видно только через полный pyVoIP.DEBUG, который сам по себе портит
    тайминг отправки."""
    from pyVoIP import RTP  # type: ignore

    logged = set()

    def patched_encode_packet(self, payload):
        key = id(self)
        if key not in logged:
            logged.add(key)
            print(f"[audio] исходящий кодек: {self.preference}", flush=True)
        if self.preference == RTP.PayloadType.PCMU:
            return self.encode_pcmu(payload)
        elif self.preference == RTP.PayloadType.PCMA:
            return self.encode_pcma(payload)
        else:
            raise RTP.RTPParseError(
                "Unsupported codec (encode): " + str(self.preference)
            )

    RTP.RTPClient.encode_packet = patched_encode_packet


_patch_pyvoip_proxy_auth()
_patch_pyvoip_ack_tag()
_patch_rtp_logging()
_patch_pcma_encode_bug()

class _JunkFilteringSocket:
    """Прокси вокруг UDP-сокета SIPClient. pyVoIP 1.6.8 держит один сокет
    (`sip.s`) на двоих — его читает и фоновый recv_loop(), и синхронный
    invite()/register()/bye(). Два защитных механизма поверх сырого recv():

    1. Фильтр мусора: recv_loop() умеет молча пропускать «пустые» keep-alive
       пакеты (4 нулевых байта), а синхронный код (invite() и т.п.) — нет:
       если такой пакет достаётся ему, вызов падает с SIPParseError вместо
       того чтобы дождаться настоящего ответа.

    2. Таймаут на блокирующее ожидание: invite() вызывает recv() без
       какого-либо таймаута — если сервер вообще не пришлёт валидный ответ,
       вызов зависнет навсегда. recv_loop() же временно переключает сокет
       в неблокирующий режим (gettimeout() == 0) на время своего опроса и
       держит self.recvLock только на этот короткий момент; синхронные
       методы (invite/register/bye) захватывают тот же self.recvLock на
       всё время ожидания, поэтому пока один из них работает, recv_loop
       гарантированно ждёт лока и НЕ переключает режим сокета — значит
       gettimeout() надёжно отличает «это блокирующий синхронный вызов»
       от «это неблокирующий опрос recv_loop». Для первого случая ждём
       через select() с общим бюджетом времени, не трогая сам сокет —
       это не ломает setblocking()/recvLock-логику библиотеки.

    `socket.recv` — read-only атрибут на самом объекте сокета, поэтому
    подменяем не метод, а сам объект целиком."""

    def __init__(self, real_socket, blocking_timeout_sec: float = 25.0):
        self._real = real_socket
        self._blocking_timeout = blocking_timeout_sec

    def recv(self, bufsize, *args, **kwargs):
        deadline = None
        while True:
            if self._real.gettimeout() is None:
                if deadline is None:
                    deadline = time.time() + self._blocking_timeout
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise socket.timeout(
                        f"Нет ответа от SIP-сервера за {self._blocking_timeout} сек")
                ready, _, _ = select.select([self._real], [], [], remaining)
                if not ready:
                    raise socket.timeout(
                        f"Нет ответа от SIP-сервера за {self._blocking_timeout} сек")
            data = self._real.recv(bufsize, *args, **kwargs)
            if data == b"\x00\x00\x00\x00" or not data.strip(b"\x00"):
                continue
            return data

    def sendto(self, data, addr, *args, **kwargs):
        if VERBOSE_NETWORK_LOG:
            try:
                first_line = data.split(b"\r\n", 1)[0].decode("utf8", "replace")
                print(f">>> SENT to {addr}: {first_line}", flush=True)
            except Exception:
                pass
        return self._real.sendto(data, addr, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _patch_sip_socket(phone) -> None:
    wrapped = _JunkFilteringSocket(phone.sip.s)
    phone.sip.s = wrapped
    phone.sip.out = wrapped


def normalize_number(raw: str) -> str:
    """Чистим номер до формата 7XXXXXXXXXX (Novofon принимает без +)."""
    digits = "".join(c for c in str(raw) if c.isdigit())
    if digits.startswith("8") and len(digits) == 11:
        return "7" + digits[1:]
    if digits.startswith("7") and len(digits) == 11:
        return digits
    if len(digits) == 10:
        return "7" + digits
    return digits


def _pcm16_to_pyvoip(pcm16: bytes) -> bytes:
    """16-bit signed PCM → 8-bit unsigned linear (формат write_audio pyVoIP).

    Три штриха против щелчков на стыках фраз:
    1. Довыравниваем длину до кратной 160 байтам (один RTP-пакет = 20мс) —
       иначе последний пакет фразы получается «рваным»: trans() читает
       ровно 160 байт за раз, и на неполном хвосте недостающие байты
       подтягиваются уже из СЛЕДУЮЩЕЙ фразы или из тишины.
    2. Плавно гасим последние ~5мс перед этим хвостом до тишины (линейный
       фейд), а не обрываем резко — иначе даже при точном выравнивании
       мгновенный скачок амплитуды до 128 (тишина) сам по себе звучит как
       маленький щелчок на границе каждой фразы.
    3. Симметрично — плавно поднимаем первые ~5мс от тишины (после
       предыдущей фразы/паузы бот начинает говорить не мгновенным
       скачком громкости, а коротким нарастанием)."""
    if not pcm16:
        return b""
    signed8 = audioop.lin2lin(pcm16, 2, 1)
    raw8 = bytearray(audioop.bias(signed8, 1, 128))

    fade_in_len = min(40, len(raw8))  # ~5мс при 8kHz
    for i in range(fade_in_len):
        frac = i / fade_in_len  # 0 (тишина) → 1 (полная громкость)
        val = raw8[i]
        raw8[i] = round(128 + (val - 128) * frac)

    fade_len = min(40, len(raw8))  # ~5мс при 8kHz
    for i in range(fade_len):
        frac = i / fade_len  # 0 (начало фейда) → 1 (полная тишина)
        idx = len(raw8) - fade_len + i
        val = raw8[idx]
        raw8[idx] = round(val + (128 - val) * frac)

    remainder = len(raw8) % 160
    if remainder:
        raw8 += b"\x80" * (160 - remainder)
    return bytes(raw8)


def _pyvoip_to_pcm16(raw8: bytes) -> bytes:
    """8-bit unsigned linear (формат read_audio pyVoIP) → 16-bit signed PCM."""
    if not raw8:
        return b""
    signed8 = audioop.bias(raw8, 1, -128)
    return audioop.lin2lin(signed8, 1, 2)


def _call_state_name(call) -> str:
    state = getattr(call, "state", None)
    return getattr(state, "name", str(state)).upper()


BARGE_IN_THRESHOLD_MULT = 5.0   # во сколько раз громче фонового шума линии
BARGE_IN_MIN_RMS = 900.0        # абсолютный пол (защита от тихого baseline)
BARGE_IN_SUSTAIN_MS = 500       # столько подряд должно быть громко, чтобы засчитать
BARGE_IN_WARMUP_MS = 300        # первые N мс фразы только копим baseline шума линии
# Раньше (3.0x / 400 / 250мс) перебивание ловило фоновый шум и одиночные
# щелчки линии как речь — бот не мог договорить ни фразы. Задрали планку:
# нужно не только заметно (в 5 раз) громче фона, но и держать эту
# громкость 500мс подряд — короткий щелчок/всплеск шума столько не длится,
# а осознанная попытка перебить бота — да.


def speak(call, text: str, allow_interrupt: bool = True,
          interrupt_threshold_mult: float = BARGE_IN_THRESHOLD_MULT,
          interrupt_min_rms: float = BARGE_IN_MIN_RMS,
          interrupt_sustain_ms: int = BARGE_IN_SUSTAIN_MS) -> Optional[bytes]:
    """Озвучивает фразу в трубку небольшими кусками (по 100мс), между
    которыми проверяет входящее аудио от кандидата на признаки речи —
    barge-in. Эхо колонок/микрофона тут не проблема (в отличие от
    local_bot.py): каждое направление разговора по телефону — отдельный
    RTP-поток, наш голос физически не попадает во входящий канал.

    Порог адаптивный: телефонная линия (особенно через мост Novofon)
    может иметь заметный фоновый шум/шипение сам по себе — фиксированный
    порог громкости либо ловит этот шум как «речь» на каждой фразе, либо
    (если задрать порог) не ловит тихую речь. Поэтому первые
    BARGE_IN_WARMUP_MS каждой фразы просто измеряем фоновый уровень линии,
    а дальше требуем, чтобы громкость была ощутимо (interrupt_threshold_mult
    раз) выше этого конкретного фона — так же, как сделано для
    браузерного бота в local_bot.py.

    Если кандидат начал говорить — прерывает воспроизведение досрочно и
    возвращает PCM16 уже пойманного начала его ответа (чтобы передать в
    listen() и не потерять первые ~200-300мс речи). Если прерывания не
    было — возвращает None (буфер входящего аудио уже вычищен от «эха
    задержки», накопленного за время речи бота — см. _flush_incoming_audio).

    Когда allow_interrupt=False, пишем ВЕСЬ звук ОДНИМ write_audio() —
    измерения на реальном звонке (2026-07-01) показали, что запись
    кусками по 100мс с отдельным time.sleep() на каждый кусок создаёт
    десятки щелчков/склеек за фразу (грубая гранулярность time.sleep()
    на Windows, ~15.6мс, даёт буферу pmout периодически пересыхать между
    записями). Один большой write_audio() отдаёт trans() сразу весь буфер,
    и она потребляет его без риска пересыхания."""
    pcm16 = synthesize_telephony_pcm(text)
    raw8 = _pcm16_to_pyvoip(pcm16)

    if not allow_interrupt:
        call.write_audio(raw8)
        time.sleep(len(raw8) / 8000.0 + 0.15)
        _flush_incoming_audio(call)
        return None

    # 250мс/кусок вместо прежних 100мс — меньше отдельных write_audio()/
    # sleep() вызовов на фразу, значит меньше шансов на дребезг таймингов
    # Windows (см. случай allow_interrupt=False выше). Порог всё равно
    # требует 500мс подряд, так что отзывчивость почти не страдает.
    chunk_ms = 250
    chunk_len = 2000  # 250мс при 8kHz, 1 байт/сэмпл (8-bit)
    loud_ms = 0
    preroll_parts = []
    baseline_samples = []
    elapsed_ms = 0

    pos = 0
    while pos < len(raw8):
        chunk = raw8[pos:pos + chunk_len]
        call.write_audio(chunk)
        pos += chunk_len
        time.sleep(len(chunk) / 8000.0)
        elapsed_ms += chunk_ms

        if not allow_interrupt:
            continue
        try:
            incoming = call.read_audio(length=chunk_len, blocking=False)
        except Exception:
            incoming = b""
        pcm_in = _pyvoip_to_pcm16(incoming)
        rms = audioop.rms(pcm_in, 2) if pcm_in else 0

        if elapsed_ms <= BARGE_IN_WARMUP_MS:
            baseline_samples.append(rms)
            continue

        baseline_samples.append(rms)
        if len(baseline_samples) > 20:
            baseline_samples.pop(0)
        baseline = max(sorted(baseline_samples)[len(baseline_samples) // 2], 150.0)  # медиана

        if rms > baseline * interrupt_threshold_mult and rms > interrupt_min_rms:
            loud_ms += chunk_ms
            preroll_parts.append(pcm_in)
            if len(preroll_parts) > 2:  # держим хвост максимум ~500мс
                preroll_parts.pop(0)
            if loud_ms >= interrupt_sustain_ms:
                return b"".join(preroll_parts)
        else:
            loud_ms = 0
            preroll_parts.clear()

    time.sleep(0.15)  # +запас перед началом прослушки
    _flush_incoming_audio(call)
    return None


def _flush_incoming_audio(call) -> None:
    """Пока бот говорит, входящее аудио от кандидата продолжает копиться
    во внутреннем буфере pyVoIP (pmin) — read_audio() читает его строго
    последовательно, поэтому если не сбрасывать этот буфер, каждая
    следующая listen() начинает не с живого края потока, а с «хвоста»,
    накопленного за время речи бота. За весь звонок это накапливается
    (каждая реплика бота — секунды непрочитанного backlog), и в итоге
    ответы кандидата распознаются на один-два вопроса позже, чем были
    произнесены на самом деле. Перепрыгиваем курсор чтения в конец
    буфера сразу после речи бота, чтобы listen() всегда стартовал с
    актуального момента."""
    try:
        for rtp_client in call.RTPClients:
            pm = rtp_client.pmin
            with pm.bufferLock:
                pm.buffer.seek(0, 2)  # 2 = os.SEEK_END
    except Exception:
        pass


def listen(call, vocab: Optional[list] = None,
           silence_after_speech_sec: float = 0.9,
           silence_before_speech_sec: float = 6.0,
           max_total_sec: float = 20.0,
           on_partial=None,
           preroll_pcm16: Optional[bytes] = None) -> str:
    """Слушает ответ кандидата из телефонной линии до тишины.
    Останавливается досрочно если звонок оборвался (кандидат положил трубку).

    preroll_pcm16: если кандидат перебил бота (см. speak()), сюда передаётся
    уже пойманный кусочек начала его речи — чтобы не терять первые ~200-300мс
    ответа, скормим его распознавателю сразу, до основного цикла чтения."""
    rec = StreamingRecognizer(input_sample_rate=8000, vocab=vocab)
    parts = []
    last_partial = ""
    last_change_at = time.time()
    speech_started = False
    start = time.time()
    chunk_len = 160  # 20мс при 8kHz, 1 байт/сэмпл (8-bit)

    if preroll_pcm16:
        r = rec.feed(preroll_pcm16)
        speech_started = True
        if r.get("final"):
            parts.append(r["final"])
        elif r.get("partial"):
            last_partial = r["partial"]

    while True:
        now = time.time()
        if now - start > max_total_sec:
            break
        st = _call_state_name(call)
        if "ENDED" in st or "FAIL" in st:
            break
        try:
            # blocking=True здесь опасен: RTPClient.read() внутри pyVoIP
            # крутится в цикле, пока не получит НЕ-тишину, и никак не
            # реагирует на разрыв звонка — при полной тишине на линии это
            # зависает навсегда. Читаем неблокирующе и сами задаём паузу
            # между чтениями (20мс — реальный интервал RTP-пакетов),
            # тогда проверки max_total_sec/тишины/обрыва звонка выше
            # реально успевают отрабатывать каждую итерацию.
            raw8 = call.read_audio(length=chunk_len, blocking=False)
        except Exception:
            break
        time.sleep(chunk_len / 8000.0)
        pcm16 = _pyvoip_to_pcm16(raw8)
        r = rec.feed(pcm16)
        if r.get("final"):
            parts.append(r["final"])
            last_partial = ""
            last_change_at = now
            if speech_started:
                break
        else:
            cur = r.get("partial", "")
            if cur != last_partial:
                last_partial = cur
                last_change_at = now
                if cur:
                    speech_started = True
                    if on_partial:
                        try: on_partial(cur)
                        except Exception: pass
        silence = now - last_change_at
        if speech_started and silence >= silence_after_speech_sec:
            break
        if not speech_started and silence >= silence_before_speech_sec:
            break

    tail = rec.finalize()
    if tail:
        parts.append(tail)
    return " ".join(p for p in parts if p).strip()


def detect_voicemail(call, max_listen_sec: float = 8.0) -> Optional[str]:
    """Слушает линию СРАЗУ после ответа, ещё до первой фразы бота — часто
    именно в этот момент уже играет приветствие автоответчика/голосовой
    почты оператора («Абонент недоступен...», «Включен автоответчик...»).
    Без ограничения словаря (обычная диктовка), чтобы распознать любую
    формулировку.

    Приветствия автоответчика обычно длинные и непрерывные, с короткими
    паузами между предложениями — если резать по первой паузе (как в
    обычном listen() для ответов кандидата), можно остановиться раньше
    ключевых слов («...оставьте сообщение») и не поймать автоответчик.
    Поэтому если речь УЖЕ началась — ждём тишины после неё заметно дольше
    обычного (silence_after_speech_sec).

    silence_before_speech_sec, наоборот, короткий (не max_listen_sec) —
    живой человек часто отвечает молча, ожидая что заговорит звонящий
    (обычный телефонный этикет); если ждать тут все 8 секунд как раньше,
    получается долгая неловкая тишина перед первой фразой бота на КАЖДОМ
    звонке живому человеку. Автоответчик почти всегда начинает говорить
    сразу — короткого окна достаточно, чтобы его поймать, а живому
    кандидату не придётся ждать дольше пары секунд.

    Возвращает распознанный текст если это похоже на автоответчик, иначе
    None."""
    heard = listen(call, vocab=None,
                    silence_after_speech_sec=2.5,
                    silence_before_speech_sec=2.0,
                    max_total_sec=max_listen_sec)
    if heard and is_voicemail_phrase(heard):
        return heard
    return None


def _run_dialog_loop(call, scenario, candidate_name: str, log, result: dict,
                      known_answers: Optional[dict] = None,
                      on_transcript_update=None) -> None:
    """Общий цикл диалога поверх уже установленного (отвеченного) звонка —
    используется и в run_call() (прямой исходящий SIP), и в
    run_call_via_bridge() (входящий звонок от Novofon после моста).

    known_answers: ответы, уже известные из загруженного файла/ручного
    ввода — бот не переспрашивает эти вопросы вживую (см. dialog.py).

    on_transcript_update: если передан, вызывается с копией sess.transcript
    после каждой реплики (для живого мониторинга звонка на портале) —
    ошибки внутри проглатываются, чтобы сбой отправки на портал не мог
    оборвать реальный звонок."""
    def push_transcript():
        if on_transcript_update:
            try: on_transcript_update(list(sess.transcript))
            except Exception: pass

    sess = DialogSession(scenario, known_answers=known_answers)
    action = sess.start()
    push_transcript()
    first_phrase = True
    heard_anything = False
    while True:
        st = _call_state_name(call)
        if "ENDED" in st or "FAIL" in st:
            log("Звонок оборвался (кандидат положил трубку).")
            result["status"] = "hangup_by_candidate"
            result["answers"] = sess.answers
            result["notes"] = sess.notes
            result["transcript"] = sess.transcript
            if sess.i < len(sess.steps):
                step = sess.steps[sess.i]
                result["dropped_at_step"] = step.get("crit", step["id"])
            return

        preroll = None
        if action.kind in ("speak_then_listen", "speak_then_end"):
            text = render_name(action.text, candidate_name)
            log(f"[БОТ] {text}")
            # Сразу после установки моста звук ещё не устаканился (эхо/
            # щелчки на подключении) — на самой первой фразе звонка
            # перебивание чаще ловит это как речь. Отключаем barge-in
            # только для первой фразы, дальше включаем как обычно.
            preroll = speak(call, text, allow_interrupt=not first_phrase)
            first_phrase = False
            if preroll:
                log("[КАНДИДАТ] (перебил бота)")
        if action.kind != "speak_then_listen":
            break

        if sess.pending == "lmk_follow":
            vocab = vocab_for_step({"expect": "yesno"})
        else:
            cur_step = sess.steps[sess.i] if sess.i < len(sess.steps) else {}
            vocab = vocab_for_step(cur_step)

        answer = listen(call, vocab=vocab, preroll_pcm16=preroll)
        log(f"[КАНДИДАТ] {answer or '(тишина)'}")
        if answer:
            heard_anything = True
        action = sess.submit_answer(answer)
        push_transcript()

    result["answers"] = action.answers
    result["notes"] = action.notes
    result["transcript"] = action.transcript
    result["verdict"] = action.end_verdict
    result["stop_reason"] = action.end_reason
    if result["status"] == "unknown":
        result["status"] = "answered_completed"
    # Если за весь звонок кандидат ни разу ничего не сказал (везде
    # тишина) — сценарий мог доиграть до конца автоответчику или на
    # очень плохой линии. Явное определение (detect_voicemail) могло не
    # сработать, если приветствие началось не сразу, а с задержкой —
    # это резервный признак для последующего разбора.
    if not heard_anything:
        result["possible_voicemail"] = True


def run_call(phone_number: str, scenario_id: str = DEFAULT_SCENARIO,
             known_answers: Optional[dict] = None,
             candidate_name: str = "",
             on_log=None,
             on_transcript_update=None) -> dict:
    """Совершает один реальный звонок и ведёт полный диалог.

    known_answers: {crit: value} — уже известные из загруженного файла/
                   ручного ввода ответы, бот не переспрашивает их вживую.
    candidate_name: подставляется в текст бота вместо {name}.
    on_transcript_update: коллбэк для живого мониторинга звонка (см.
                   _run_dialog_loop) — вызывается после каждой реплики.

    Возвращает dict:
      status: answered_completed | no_answer | busy | hangup_by_candidate
              | error
      verdict: passed | stopped | declined | None (если не отвечено)
      stop_reason, answers, notes, transcript, duration_s, error
    """
    def log(msg):
        print(msg, flush=True)
        if on_log:
            try: on_log(msg)
            except Exception: pass

    target = normalize_number(phone_number)
    if not target:
        return {"status": "error", "error": f"Не понял номер: {phone_number}",
                "verdict": None, "stop_reason": None, "answers": {}, "notes": {},
                "transcript": [], "duration_s": 0}

    env = load_env()
    server = require(env, "SIP_SERVER")
    port = int(env.get("SIP_PORT", "5060"))
    user = require(env, "SIP_USER")
    pwd = require(env, "SIP_PASS")
    local_ip = get_local_ip()

    scenario = load_scenario(scenario_id)
    log(f"Сценарий: {scenario['name']}")

    result = {
        "status": "unknown", "verdict": None, "stop_reason": None,
        "answers": {}, "notes": {}, "transcript": [],
        "duration_s": 0, "error": None, "possible_voicemail": False,
        "dropped_at_step": None,
    }

    phone = VoIPPhone(server=server, port=port, username=user, password=pwd,
                       myIP=local_ip, callCallback=lambda call: None)
    call_start = time.time()
    call = None
    try:
        log("Регистрируюсь в SIP...")
        phone.start()
        _patch_sip_socket(phone)
        log(f"Звоню на +{target}...")
        try:
            call = phone.call(target)
        except socket.timeout as e:
            result["status"] = "error"
            result["error"] = f"Сервер не ответил на набор номера: {e}"
            log(f"❌ {result['error']}")
            return result

        answered = False
        deadline = time.time() + 30
        while time.time() < deadline:
            st = _call_state_name(call)
            if "ANSWER" in st:
                answered = True
                break
            if "BUSY" in st:
                result["status"] = "busy"
                break
            if "ENDED" in st or "FAIL" in st:
                result["status"] = "no_answer"
                break
            time.sleep(0.3)

        if not answered:
            if result["status"] == "unknown":
                result["status"] = "no_answer"  # истёк дедлайн, никто не поднял
            log(f"Не дозвонились: {result['status']}")
            try: call.hangup()
            except Exception: pass
            return result

        log("Проверяю, не автоответчик ли это...")
        vm_text = detect_voicemail(call)
        if vm_text:
            log(f"📼 Похоже на автоответчик: «{vm_text}» — вешаю трубку.")
            result["status"] = "voicemail"
            result["error"] = vm_text
            try: call.hangup()
            except Exception: pass
            return result

        log("✅ Ответили! Начинаю диалог.")
        _run_dialog_loop(call, scenario, candidate_name, log, result, known_answers=known_answers,
                          on_transcript_update=on_transcript_update)

        try: call.hangup()
        except Exception: pass

    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"
        log(f"❌ Ошибка звонка: {result['error']}")
        if call is not None:
            try: call.hangup()
            except Exception: pass
    finally:
        result["duration_s"] = round(time.time() - call_start, 1)
        try: phone.stop()
        except Exception: pass

    return result


def run_call_via_bridge(phone_number: str, scenario_id: str = DEFAULT_SCENARIO,
                         candidate_name: str = "", on_log=None,
                         known_answers: Optional[dict] = None,
                         scenario: Optional[dict] = None,
                         on_transcript_update=None) -> dict:
    """Совершает звонок в обход ограничения нашей SIP-линии (type=in —
    только входящие, см. Data API get.sip_lines). Вместо прямого
    исходящего INVITE (который на этой линии не работает физически, не
    баг pyVoIP) просим Call API Novofon сначала перезвонить НАМ на нашу
    линию (входящий звонок — разрешён), а когда мы ответим — их
    инфраструктура сама дозванивается до кандидата и сводит разговор.
    Голосовой диалог дальше ведётся ровно так же, как в run_call(), но
    поверх уже установленного (не нами инициированного) звонка.

    known_answers: {crit: value} — уже известные из загруженного файла/
                   ручного ввода ответы, бот не переспрашивает их вживую.
    scenario: если передан — используется как есть (например, сценарий из
              БД портала), scenario_id тогда нужен только для лога/отчёта.
              Если None — грузится по scenario_id из локального файла."""
    def log(msg):
        print(msg, flush=True)
        if on_log:
            try: on_log(msg)
            except Exception: pass

    target = normalize_number(phone_number)
    if not target:
        return {"status": "error", "error": f"Не понял номер: {phone_number}",
                "verdict": None, "stop_reason": None, "answers": {}, "notes": {},
                "transcript": [], "duration_s": 0}

    env = load_env()
    server = require(env, "SIP_SERVER")
    port = int(env.get("SIP_PORT", "5060"))
    user = require(env, "SIP_USER")
    pwd = require(env, "SIP_PASS")
    api_secret = require(env, "NOVOFON_API_SECRET")
    virtual_number = require(env, "NOVOFON_VIRTUAL_NUMBER")
    employee_id = int(require(env, "NOVOFON_EMPLOYEE_ID"))
    local_ip = get_local_ip()

    if scenario is None:
        scenario = load_scenario(scenario_id)
    log(f"Сценарий: {scenario['name']}")

    result = {
        "status": "unknown", "verdict": None, "stop_reason": None,
        "answers": {}, "notes": {}, "transcript": [],
        "duration_s": 0, "error": None, "possible_voicemail": False,
        "dropped_at_step": None,
    }

    incoming: "queue.Queue" = queue.Queue()

    def on_incoming_call(call) -> None:
        try:
            call.answer()
            incoming.put(call)
        except Exception as e:
            log(f"Не удалось ответить на входящий звонок от Novofon: {e}")

    phone = VoIPPhone(server=server, port=port, username=user, password=pwd,
                       myIP=local_ip, callCallback=on_incoming_call)
    call_start = time.time()
    call = None
    try:
        log("Регистрируюсь в SIP...")
        phone.start()
        _patch_sip_socket(phone)

        log(f"Прошу Novofon перезвонить на линию {user} и соединить с +{target}...")
        try:
            call_session_id = call_api.start_employee_call(
                api_secret, target, employee_id, user, virtual_number)
        except call_api.NovofonAPIError as e:
            result["status"] = "error"
            result["error"] = f"Call API отказал: {e}"
            log(f"❌ {result['error']}")
            return result
        log(f"call_session_id={call_session_id}, жду входящий звонок на нашу линию...")

        try:
            call = incoming.get(timeout=70)
        except queue.Empty:
            result["status"] = "error"
            result["error"] = "Novofon не перезвонил на нашу линию за 70 сек"
            log(f"❌ {result['error']}")
            return result

        log("Ответили на входящий от Novofon. Жду пока дозвонятся до кандидата...")
        bridged = call_api.wait_for_contact_talking(api_secret, call_session_id, timeout=45)
        if not bridged:
            result["status"] = "no_answer"
            log("Кандидат не взял трубку (или звонок завершился раньше).")
            try: call.hangup()
            except Exception: pass
            return result

        log("Проверяю, не автоответчик ли это...")
        vm_text = detect_voicemail(call)
        if vm_text:
            log(f"📼 Похоже на автоответчик: «{vm_text}» — вешаю трубку.")
            result["status"] = "voicemail"
            result["error"] = vm_text
            try: call.hangup()
            except Exception: pass
            return result

        log("✅ Кандидат на линии! Начинаю диалог.")
        _run_dialog_loop(call, scenario, candidate_name, log, result, known_answers=known_answers,
                          on_transcript_update=on_transcript_update)

        try: call.hangup()
        except Exception: pass

    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"
        log(f"❌ Ошибка звонка: {result['error']}")
        if call is not None:
            try: call.hangup()
            except Exception: pass
    finally:
        result["duration_s"] = round(time.time() - call_start, 1)
        try: phone.stop()
        except Exception: pass

    return result


def main():
    if len(sys.argv) < 2:
        print("Использование: python voicecall/phone_call.py <НОМЕР> [scenario_id] [--direct]")
        print("Пример:        python voicecall/phone_call.py +79991234567")
        print("--direct использует старый прямой исходящий SIP (не работает —")
        print("  наша линия провизирована только на приём, см. Data API get.sip_lines).")
        sys.exit(1)
    number = sys.argv[1]
    scenario_id = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else DEFAULT_SCENARIO
    direct = "--direct" in sys.argv

    print("Прогрев Vosk-модели...")
    stt_warmup()
    scenario = load_scenario(scenario_id)
    print("Прогрев TTS (генерирую все фразы сценария)...")
    prewarm_scenario(scenario, voice=DEFAULT_VOICE, verbose=False)
    for t in all_reask_texts():
        synthesize_telephony_pcm(t, voice=DEFAULT_VOICE)
    print()

    if direct:
        result = run_call(number, scenario_id, candidate_name="")
    else:
        result = run_call_via_bridge(number, scenario_id, candidate_name="")

    print()
    print("═════ ИТОГ ЗВОНКА ═════")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
