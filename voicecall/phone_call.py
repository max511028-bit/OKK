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
import random
import re
import select
import socket
import sys
import threading
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
from dialog import DialogSession, load_scenario, DEFAULT_SCENARIO, vocab_for_step, render_name, all_reask_texts, is_voicemail_phrase, is_callback_request, is_ringback_phrase, llm_is_robot_live, answer_is_evasive, CALLBACK_BYE_TEXT, FILLER_PHRASES
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
    codec_logged = set()

    # Карта payload type → человекочитаемое имя (RFC 3551 статические типы).
    _PT_NAMES = {0: "PCMU (G.711 µ-law)", 8: "PCMA (G.711 A-law)",
                 18: "G.729", 9: "G.722", 4: "G.723", 3: "GSM", 101: "telephone-event"}

    def patched_parse_packet(self, packet):
        # Диагностика ВХОДЯЩЕГО кодека (2026-07-09): раньше логировали только
        # исходящий (_patch_pcma_encode_bug). Payload type — младшие 7 бит
        # второго байта RTP-заголовка.
        key = id(self)
        if len(packet) >= 2 and key not in codec_logged:
            codec_logged.add(key)
            pt = packet[1] & 0x7F
            name = _PT_NAMES.get(pt, "НЕИЗВЕСТНЫЙ")
            print(f"[audio] входящий кодек (payload type {pt}): {name}", flush=True)
        # Счётчик ВХОДЯЩИХ пакетов (2026-07-09): решает NAT-vs-буфер. Если
        # read_audio отдаёт тишину, а сюда прилетели сотни пакетов — аудио
        # доходит, проблема в буфере/чтении (правится в коде). Если пакетов
        # единицы и поток заглох — входящее реально не доходит (NAT). Пишем
        # безусловно, редко (каждые 250 ≈ раз в 5с), чтобы не залить лог.
        n = counters.get(key, 0) + 1
        counters[key] = n
        if n == 1 or n % 250 == 0:
            print(f"[audio] входящих RTP-пакетов получено: {n}", flush=True)
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


def _patch_rtp_memory_guard() -> None:
    """Защита от MemoryError в RTPPacketManager.write (реальный краш
    2026-07-06: агент упал посреди звонка, поток "RTP Receiver" умер с
    MemoryError на buffer.write, кампания зависла).

    Причина — баг pyVoIP: входящий RTP-timestamp используется как позиция
    в буфере (`self.buffer.seek(offset - self.offset); self.buffer.write`).
    Библиотека защищается ТОЛЬКО от скачка timestamp НАЗАД (rebuild при
    abs >= 100000), но НЕ от скачка ВПЕРЁД: аномально большой timestamp
    (джиттер / потеря пакетов / смена timestamp-базы удалённой стороной /
    32-битное переполнение) даёт seek на позицию в сотни МБ–ГБ, и
    следующий write расширяет BytesIO нулями до неё → мгновенный
    MemoryError, роняющий RTP-поток и весь звонок целиком.

    Оборачиваем write: (1) симметрично отсекаем аномальный скачок ВПЕРЁД
    — пакет "из далёкого будущего" (дальше, чем разумная длительность
    скрининг-звонка) просто игнорируем, не давая seek раздуть буфер;
    (2) на всякий случай ловим MemoryError как последний рубеж, чтобы
    один сбойный пакет не убивал поток и звонок."""
    from pyVoIP import RTP  # type: ignore

    orig_write = RTP.RTPPacketManager.write
    # 8000 единиц timestamp = 1с аудио (PCMA/PCMU 8kHz). Скрининг-звонки
    # длятся 1-3 минуты; 10 минут — заведомо аномальный forward-jump, при
    # этом реальные звонки его никогда не достигнут. Настоящие timestamp-
    # скачки дают offset в сотни миллионов (гигабайты seek) — отсекаем.
    FORWARD_JUMP_LIMIT = 8000 * 600  # ~10 минут аудио
    rebase_counters = {}

    def guarded_write(self, offset, data):
        try:
            if offset - self.offset >= FORWARD_JUMP_LIMIT:
                # Большой forward-скачок timestamp. РАНЬШЕ молча дропали
                # пакет — но это УБИВАЛО ВСЁ входящее аудио, если у потока
                # Novofon легитимный разрыв базы timestamp. Реальный сбой
                # (10.07): первый пакет с базой ~570млн, дальше весь поток
                # на ~4.29млрд → каждый пакет выше лимита → дропалось всё →
                # read отдавал тишину → громкость н/д → «не распознано».
                # Это и была регрессия, ломавшая распознавание с ~06.07
                # (08.07 работало, когда timestamp'ы совпадали с базой).
                # Правильно — ПЕРЕ-БАЗИРОВАТЬСЯ на новый поток (reset +
                # rebuild), ровно как сам pyVoIP делает для скачка НАЗАД
                # (RTP.py write, ветка offset < self.offset). Это и
                # MemoryError предотвращает (buffer = BytesIO(data), без
                # seek на гигантскую позицию), и аудио сохраняет. После
                # одного пере-базирования поток идёт дальше штатно (дельта
                # между пакетами ~160, что сильно меньше лимита).
                key = id(self)
                c = rebase_counters.get(key, 0) + 1
                rebase_counters[key] = c
                if c <= 3:
                    print(f"[audio] RTP timestamp пере-базирован на новый поток "
                          f"(offset={offset}, старая base={self.offset})", flush=True)
                self.offset = offset
                self.rebuild(True, offset, data)
                return
        except Exception:
            pass
        try:
            return orig_write(self, offset, data)
        except MemoryError:
            return

    RTP.RTPPacketManager.write = guarded_write


_patch_pyvoip_proxy_auth()
_patch_pyvoip_ack_tag()
_patch_rtp_logging()
_patch_pcma_encode_bug()
_patch_rtp_memory_guard()

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


# Сколько ждём подтверждения SIP-регистрации перед тем как просить Novofon
# перезвонить. На здоровой сети приходит за ~100мс (замер 27.07); запас на
# случай подтормаживания. Если не дождались — Novofon увидит линию офлайн
# и звонить не станет, так что лучше упасть сразу с понятной причиной, чем
# ждать 30с впустую (на массовом обзвоне это часы на пустом месте).
SIP_REGISTER_TIMEOUT_SEC = 8.0


def _wait_sip_registered(phone, log, timeout: float = SIP_REGISTER_TIMEOUT_SEC) -> bool:
    """Дождаться, что SIP-линия РЕАЛЬНО зарегистрирована (27.07).
    VoIPPhone.start() выставляет статус REGISTERING и возвращает управление —
    подтверждение от сервера приходит отдельно. Раньше мы сразу просили
    Novofon перезвонить: если регистрация не доехала, он видел линию офлайн
    (finish_reason='sip_offline'), не звонил, и мы получали загадочное
    «Novofon не перезвонил за 30 сек» (реальный случай, тест 27.07)."""
    from pyVoIP.VoIP import PhoneStatus
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = phone.get_status()
        if st == PhoneStatus.REGISTERED:
            return True
        if st == PhoneStatus.FAILED:
            log("❌ SIP-регистрация отклонена сервером (статус FAILED).")
            return False
        time.sleep(0.05)
    log(f"❌ SIP-регистрация не подтверждена за {timeout:.0f}с "
        f"(статус: {phone.get_status().name}).")
    return False


# Сколько ждём, пока НАШУ регистрацию увидит коммутатор Novofon. Замер
# 27.07: pyVoIP рапортует REGISTERED за ~120мс, а physical_state у Novofon
# переключается на «Зарегистрирован» через ~2с. Все эти 2 секунды заявка
# на звонок отлетает с sip_offline (7 звонков из 8 в тот день).
NOVOFON_LINE_TIMEOUT_SEC = 12.0


def _wait_novofon_sees_line(api_secret: str, sip_user: str, log,
                             timeout: float = NOVOFON_LINE_TIMEOUT_SEC) -> bool:
    """Дождаться, что линия числится зарегистрированной именно У NOVOFON.
    Локального REGISTERED от pyVoIP недостаточно — см. комментарий выше.
    Возвращает False по таймауту (вызывающий решает, пробовать ли всё равно)."""
    deadline = time.time() + timeout
    t0 = time.time()
    last = None
    while time.time() < deadline:
        try:
            last = call_api.get_sip_line_state(api_secret, sip_user)
        except Exception:
            last = None
        if last and "не" not in last.lower():
            log(f"✅ Novofon видит линию как «{last}» ({(time.time() - t0):.1f}с).")
            return True
        time.sleep(0.4)
    log(f"⚠️  Novofon за {timeout:.0f}с так и не увидел линию "
        f"(статус: {last or 'неизвестен'}).")
    return False


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


# ── Диагностика «громко, но не распознано» (2026-07-09) ──────────────────
# Реальный случай: датчик громкости слышит громкий звук (~5847), а Vosk на
# всех окнах вернул тишину; при этом серверная запись Novofon чистая. Три
# возможных причины требуют РАЗНЫХ фиксов: (а) неверный кодек на входящей
# ноге → в дампе будет громкий мусор, большая модель тоже ничего не
# разберёт; (б) эхо нашего же TTS обратно в линию → большая модель
# распознает текст БОТА; (в) чистый звук, но realtime маленькая модель с
# грамматикой захлебнулась → большая модель разберёт нормальную речь
# кандидата. Дамп сырого (до-AGC) входящего PCM в WAV однозначно
# различает эти случаи — прогоняем его потом большой моделью.
_DIAG_DUMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag")
_diag_dump_count = 0
_DIAG_DUMP_MAX = 6  # не заваливаем диск: несколько дампов на процесс достаточно


def _dump_pcm16_wav(pcm16: bytes, tag: str = "") -> Optional[str]:
    """Пишет 8kHz mono 16-bit PCM в WAV в voicecall/diag/ для ручного
    разбора. Лучшее старание — при любой ошибке молча None, диагностика не
    должна влиять на звонок."""
    global _diag_dump_count
    if not pcm16 or _diag_dump_count >= _DIAG_DUMP_MAX:
        return None
    try:
        import wave
        os.makedirs(_DIAG_DUMP_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(_DIAG_DUMP_DIR, f"unrec_{ts}_{tag}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(pcm16)
        _diag_dump_count += 1
        return path
    except Exception:
        return None


BARGE_IN_THRESHOLD_MULT = 5.0   # во сколько раз громче фонового шума линии
BARGE_IN_MIN_RMS = 900.0        # абсолютный пол (защита от тихого baseline)
BARGE_IN_SUSTAIN_MS = 500       # столько подряд должно быть громко, чтобы засчитать
BARGE_IN_WARMUP_MS = 300        # первые N мс фразы только копим baseline шума линии
# Раньше (3.0x / 400 / 250мс) перебивание ловило фоновый шум и одиночные
# щелчки линии как речь — бот не мог договорить ни фразы. Задрали планку:
# нужно не только заметно (в 5 раз) громче фона, но и держать эту
# громкость 500мс подряд — короткий щелчок/всплеск шума столько не длится,
# а осознанная попытка перебить бота — да.


# Пороги раннего отсева робота (Слой 1/3, экономия эфира 24.07). Вынесены
# из _run_dialog_loop, чтобы решение о запуске свободной пробы было
# юнит-тестируемым отдельно от живого звонка.
LONG_OPENING_MS = 3500     # непрерывная речь длиннее — приветствие робота, не «алло»
# 45с (не 35): запас на медленного живого кандидата. По деньгам безопасно —
# Novofon тарифицирует поминутно, а 45с + ~10с пробы = ~55с укладывается в
# ПЕРВУЮ минуту (как и 35с), удорожания нет.
NO_PROGRESS_SEC = 45.0     # столько без сдвига дальше 1-2 вопроса = робот топчется


def _early_robot_probe_reason(turn_index: int, answer: str, speech_started: bool,
                               speech_dur_ms: int, sess_i: int, elapsed_sec: float,
                               no_progress_used: bool) -> "str | None":
    """Надо ли запустить СВОБОДНУЮ пробу «робот ли» на этом ходу. Возвращает
    причину (для лога) или None. Само решение «робот/человек» принимает уже
    проба (фразы + LLM, по умолчанию человек) — здесь только КОГДА её звать.
      Слой 1: длинная непрерывная речь на первых 2 ходах = приветствие робота.
      Слой 2: на 1-м ходу была речь, но грамматика её не разобрала.
      Слой 3: за ~35с не ушли дальше 1-2 вопроса — робот топчется."""
    if turn_index < 2 and speech_dur_ms > LONG_OPENING_MS:
        return f"длинная речь в начале ({speech_dur_ms}мс, «{(answer or '?')[:40]}»)"
    if turn_index == 0 and not answer and speech_started:
        return "речь на 1-м ходу не распозналась грамматикой"
    if not no_progress_used and sess_i <= 1 and elapsed_sec > NO_PROGRESS_SEC:
        return f"{NO_PROGRESS_SEC:.0f}с без прогресса (шаг {sess_i + 1})"
    return None


def speak(call, text: str, allow_interrupt: bool = True,
          interrupt_threshold_mult: float = BARGE_IN_THRESHOLD_MULT,
          interrupt_min_rms: float = BARGE_IN_MIN_RMS,
          interrupt_sustain_ms: int = BARGE_IN_SUSTAIN_MS,
          metrics: Optional[dict] = None,
          voice: str = DEFAULT_VOICE, rate: str = "+0%") -> Optional[bytes]:
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
    if not text or not text.strip():
        # Пустой текст шага (баг/недосмотр в сценарии — конструктор должен
        # был такого не допускать, но случалось) раньше ронял edge-tts
        # ошибкой "вернул пустой ответ" и вместе с ней весь звонок —
        # ответы кандидата, уже собранные за весь разговор, терялись
        # целиком. Просто молча пропускаем реплику вместо падения.
        return None
    _tts_t0 = time.time()
    pcm16 = synthesize_telephony_pcm(text, voice=voice, rate=rate)
    if metrics is not None:
        # >~80мс почти наверняка означает промах кэша (живой синтез через
        # edge-tts занимает секунды) — это и есть та самая "пауза перед
        # фразой", на которую жалуются на реальных звонках.
        metrics["tts_ms"] = round((time.time() - _tts_t0) * 1000)
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

    # Предзаливка буфера (пункт 5 доработок 2026-07): раньше писали ровно
    # один чанк — спали на его длительность — писали следующий, впритык.
    # Малейшее дрожание time.sleep() на Windows (~15.6мс гранулярность)
    # могло дать буферу воспроизведения (pmout у pyVoIP) на миг пересохнуть
    # между записями — щелчок. Теперь держим буфер ПОСТОЯННО на 1 чанк
    # вперёд: пишем первые ДВА чанка сразу, а дальше на каждой итерации —
    # спим длительность текущего чанка (он уже в буфере и играет) и сразу
    # доливаем чанк через один — так в очереди всегда есть запас, и
    # дрожание таймера конкретной итерации не успевает съесть весь запас.
    # Перебивание кандидатом по-прежнему ловится каждые chunk_ms (250мс),
    # только "хвост" уже отправленного в буфер звука после обнаружения
    # перебивания теперь может быть чуть длиннее (до ~500мс вместо ~250мс)
    # — плата за отсутствие пересыхания, реакция на перебивание всё равно
    # укладывается в общий порог interrupt_sustain_ms (500мс).
    chunks = [raw8[i:i + chunk_len] for i in range(0, len(raw8), chunk_len)]
    if chunks:
        call.write_audio(chunks[0])
    if len(chunks) > 1:
        call.write_audio(chunks[1])
    next_to_write = 2

    for chunk in chunks:
        time.sleep(len(chunk) / 8000.0)
        elapsed_ms += chunk_ms
        if next_to_write < len(chunks):
            call.write_audio(chunks[next_to_write])
            next_to_write += 1

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


def _flush_incoming_audio(call, keep_tail_ms: float = 300.0) -> None:
    """Пока бот говорит, входящее аудио от кандидата продолжает копиться
    во внутреннем буфере pyVoIP (pmin) — read_audio() читает его строго
    последовательно, поэтому если не сбрасывать этот буфер, каждая
    следующая listen() начинает не с живого края потока, а с «хвоста»,
    накопленного за время речи бота. За весь звонок это накапливается
    (каждая реплика бота — секунды непрочитанного backlog), и в итоге
    ответы кандидата распознаются на один-два вопроса позже, чем были
    произнесены на самом деле. Перепрыгиваем курсор чтения ближе к концу
    буфера сразу после речи бота, чтобы listen() всегда стартовал почти
    с актуального момента.

    keep_tail_ms: сколько мс САМОГО СВЕЖЕГО хвоста буфера НЕ трогать —
    если кандидат начал отвечать чуть раньше, чем этот флаш успел
    выполниться (обычная реакция на быстрый/короткий вопрос), первые
    ~200-300мс его речи уже лежат в буфере и раньше вырезались подчистую
    вместе со сбросом — отсюда «Отлично» превращалось в «Лично». 300мс —
    компромисс: достаточно чтобы не резать начало короткого ответа, но
    недостаточно чтобы за много реплик звонка накопить заметный дрейф
    отставания (см. описание проблемы выше)."""
    keep_tail_bytes = int(keep_tail_ms / 1000.0 * 8000)  # 8kHz, 1 байт/сэмпл (8-bit)
    try:
        for rtp_client in call.RTPClients:
            pm = rtp_client.pmin
            with pm.bufferLock:
                pm.buffer.seek(0, 2)  # 2 = os.SEEK_END
                end_pos = pm.buffer.tell()
                pm.buffer.seek(max(0, end_pos - keep_tail_bytes))
    except Exception:
        pass


def _line_has_audio(call, check_sec: float = 4.0, min_rms: float = 350.0,
                     required_chunks: int = 5) -> bool:
    """Есть ли на линии живой звук (речь/гудки далёкого конца), а не
    полная тишина. Используется как fallback-проверка, когда API статусов
    Novofon не отдал состояние «Разговор», но SIP-звонок при этом жив:
    если из трубки реально что-то слышно — соединение состоялось.
    required_chunks Требует несколько громких чанков (не подряд), чтобы
    одиночный щелчок линии не считался звуком."""
    loud = 0
    deadline = time.time() + check_sec
    while time.time() < deadline:
        st = _call_state_name(call)
        if "ENDED" in st or "FAIL" in st:
            return False
        try:
            raw8 = call.read_audio(length=160, blocking=False)
        except Exception:
            return False
        time.sleep(0.02)
        pcm16 = _pyvoip_to_pcm16(raw8)
        if pcm16 and audioop.rms(pcm16, 2) >= min_rms:
            loud += 1
            if loud >= required_chunks:
                return True
    return False


def _line_has_speech(call, check_sec: float = 2.5) -> bool:
    """Есть ли на линии РЕЧЬ (а не тишина/гудок/тон). В отличие от
    _line_has_audio (любой громкий звук) — скармливаем аудио
    распознавателю и ждём хоть какой-то партиал. Зачем (2026-07-10):
      - гудок/тон ринг-бэка Яндекса ГРОМКИЙ, но слов не даёт → сюда НЕ
        попадает (раньше _line_has_audio по громкости считал его «ответом»
        → бот говорил в тон, звонок уходил в «не распознали»);
      - короткое «алё» кандидата — это речь → ловим (раньше теряли: Novofon
        не отдавал статус «Разговор», а поздняя разовая проверка звука уже
        не заставала это «алё» → ложный «не взял трубку», кейс Анны).
    Тихое «алё» подусиливаем, чтобы распознаватель его расслышал."""
    from stt import StreamingRecognizer
    rec = StreamingRecognizer(input_sample_rate=8000, vocab=None)
    deadline = time.time() + check_sec
    while time.time() < deadline:
        st = _call_state_name(call)
        if "ENDED" in st or "FAIL" in st:
            return False
        try:
            raw8 = call.read_audio(length=160, blocking=False)
        except Exception:
            return False
        time.sleep(0.02)
        pcm16 = _pyvoip_to_pcm16(raw8)
        if not pcm16:
            continue
        rms = audioop.rms(pcm16, 2)
        if 40 < rms < 2000:  # подусиление тихой речи (не трогаем громкий тон)
            pcm16 = audioop.mul(pcm16, 2, min(8.0, 2500.0 / max(rms, 1)))
        r = rec.feed(pcm16)
        if r.get("partial") or r.get("final"):
            return True
    return bool(rec.finalize())


def listen(call, vocab: Optional[list] = None,
           silence_after_speech_sec: float = 0.9,
           silence_before_speech_sec: float = 4.5,  # было 6.0 — живее, дыры короче;
           #   тихого кандидата всё равно ждём два круга (~9с суммарно, п.7 2026-07-10)
           max_total_sec: float = 20.0,
           on_partial=None,
           preroll_pcm16: Optional[bytes] = None,
           metrics: Optional[dict] = None) -> str:
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
    speech_started_at = None  # момент первой речи — для длительности сегмента
    start = time.time()
    chunk_len = 160  # 20мс при 8kHz, 1 байт/сэмпл (8-bit)

    # Автоусиление тихого голоса (AGC) — если кандидат говорит тихо (плохая
    # линия, далеко от трубки), распознавание заметно хуже. Считаем
    # СКОЛЬЗЯЩУЮ медиану громкости за последние ~0.5с (игнорируя тишину
    # между словами, иначе туда же попадает шумовой пол и коэффициент
    # считается неверно) и, если она ниже целевого уровня, усиливаем
    # последующие чанки. Пересчёт раз в ~200мс, а не на каждый чанк —
    # иначе коэффициент дёргается и создаёт эффект "накачки" громкости.
    AGC_TARGET_RMS = 3200.0
    AGC_MAX_GAIN = 9.0  # было 6.0 — на очень тихих линиях (RMS ~120-450) упирались в потолок
    AGC_MIN_RMS_FOR_CALC = 60.0
    AGC_RECALC_EVERY = 10  # чанков (~200мс при 20мс/чанк)
    agc_gain = 1.0
    agc_recent_rms: list = []
    agc_chunk_i = 0
    captured_pcm = bytearray()  # диагностика: сырое входящее аудио окна

    if preroll_pcm16:
        r = rec.feed(preroll_pcm16)
        speech_started = True
        speech_started_at = time.time()
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
        if pcm16:
            captured_pcm.extend(pcm16)  # сырое (до-AGC) — для диагностики

        chunk_rms = audioop.rms(pcm16, 2) if pcm16 else 0
        if chunk_rms > AGC_MIN_RMS_FOR_CALC:
            agc_recent_rms.append(chunk_rms)
            if len(agc_recent_rms) > 25:
                agc_recent_rms.pop(0)
        agc_chunk_i += 1
        if agc_chunk_i % AGC_RECALC_EVERY == 0 and agc_recent_rms:
            median_rms = sorted(agc_recent_rms)[len(agc_recent_rms) // 2]
            agc_gain = min(AGC_MAX_GAIN, max(1.0, AGC_TARGET_RMS / median_rms))
        if agc_gain > 1.05:
            pcm16 = audioop.mul(pcm16, 2, agc_gain)

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
                    if not speech_started:
                        speech_started_at = now
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

    if metrics is not None:
        # Медиана громкости ДО усиления AGC — реальный уровень сигнала на
        # линии от кандидата (сырой RMS у 16-bit PCM, тишина = 0). Высокий
        # итоговый agc_gain — признак что кандидат говорил тихо/далеко от
        # трубки, это и есть просадка "качества со стороны кандидата".
        metrics["candidate_rms"] = (
            sorted(agc_recent_rms)[len(agc_recent_rms) // 2] if agc_recent_rms else 0
        )
        metrics["agc_gain"] = round(agc_gain, 1)
        metrics["listen_ms"] = round((time.time() - start) * 1000)
        # Было ли хоть что-то похожее на речь (пусть и не распознанное в
        # итоге) — отличает "кандидат реально что-то сказал, просто не
        # разобрали" от "было тихо, никто ничего не говорил". См. вызов в
        # _run_dialog_loop: на настоящей тишине переспрашивать вслух не
        # нужно, лучше молча подождать ещё раз.
        metrics["speech_started"] = speech_started
        # Длительность непрерывного речевого сегмента (от первой речи до
        # последнего изменения = конца речи). Живое «алло» коротко (~0.5-1.5с);
        # автоответчик/робот-приветствие — 4-10с непрерывной речи. Ключевой
        # ранний сигнал робота (Слой 1, экономия эфира 24.07).
        metrics["speech_dur_ms"] = (
            round((last_change_at - speech_started_at) * 1000)
            if speech_started_at is not None else 0)
        metrics["raw_pcm16"] = bytes(captured_pcm)  # для дампа при «громко, но пусто»

    return " ".join(p for p in parts if p).strip()


def _prewarm_tts_for_name(scenario: dict, candidate_name: str) -> None:
    """prewarm_scenario() в dispatch_agent.py кэширует фразы С ЛИТЕРАЛЬНЫМ
    "{name}" в тексте — а реально в звонке произносится текст ПОСЛЕ
    render_name() с уже подставленным именем кандидата. Это два разных
    текста, значит разный ключ кэша TTS, значит на КАЖДОМ звонке первая
    (и любая другая содержащая имя) фраза бота синтезируется вживую —
    это и есть та самая большая пауза в начале диалога, которая не
    ушла после фикса детекции автоответчика.

    Вызывается в отдельном потоке сразу после того как стал известен
    scenario/candidate_name, ДО того как кандидат физически поднимет
    трубку — реальное время дозвона (гудки) обычно перекрывает время
    синтеза, так что к первой фразе бота аудио уже готово в кэше."""
    if not candidate_name:
        return  # без имени render_name возвращает тот же текст что и в prewarm_scenario — кэш уже есть
    try:
        settings = scenario.get("settings") or {}
        voice = settings.get("voice") or DEFAULT_VOICE
        rate = settings.get("rate") or "+0%"
        texts = []
        for st in scenario.get("steps", []):
            for key in ("bot", "on_yes", "on_no", "on_no_follow", "stop_msg"):
                v = st.get(key)
                if v and "{name}" in v:
                    texts.append(render_name(v, candidate_name))
        closing = scenario.get("closing")
        if closing and "{name}" in closing:
            texts.append(render_name(closing, candidate_name))
        for t in texts:
            synthesize_telephony_pcm(t, voice, use_cache=True, rate=rate)
    except Exception:
        pass  # чисто оптимизация — при сбое просто синтезируем вживую как раньше


def _start_prewarm_for_name(scenario: dict, candidate_name: str) -> None:
    threading.Thread(target=_prewarm_tts_for_name, args=(scenario, candidate_name),
                      daemon=True).start()


def _run_dialog_loop(call, scenario, candidate_name: str, log, result: dict,
                      known_answers: Optional[dict] = None,
                      on_transcript_update=None,
                      bridge_established_at: Optional[float] = None) -> None:
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

    # Настройки голоса сценария (Часть 2 доработок 2026-07 — "человечность"
    # бота): voice/rate читаются ОДИН раз на весь звонок и используются во
    # ВСЕХ speak() ниже — если забыть прокинуть хоть в одном месте, та
    # фраза будет звучать чужим голосом/скоростью посреди разговора.
    # Дефолты СОВПАДАЮТ с тем, что было раньше (DEFAULT_VOICE, "+0%",
    # без филлеров) — старые сценарии без settings звучат как звучали.
    settings = scenario.get("settings") or {}
    voice = settings.get("voice") or DEFAULT_VOICE
    rate = settings.get("rate") or "+0%"
    fillers_enabled = bool(settings.get("fillers"))

    sess = DialogSession(scenario, known_answers=known_answers, candidate_name=candidate_name)
    action = sess.start()
    push_transcript()
    first_phrase = True
    heard_anything = False
    # Сколько ПОДРЯД шагов не удалось распознать вообще ничего. Отдельно
    # от is_voicemail_phrase(): на большинстве шагов (да/нет, возраст и
    # т.п.) Vosk работает с ЖЁСТКО ограниченным словарём (см.
    # vocab_for_step) и физически не может вывести литеральный текст
    # объявления оператора/автоответчика — та проверка там просто не
    # срабатывает (реальный случай на тесте 2026-07-03: Vosk увидел
    # объявление "абонент не берёт трубку...", но с ограниченным словарём
    # да/нет распознал из него только "спасибо", и is_voicemail_phrase()
    # ни на что не среагировал — бот доиграл весь сценарий вслепую).
    # Три подряд полностью нераспознанных шага — гораздо более надёжный
    # сигнал "тут не с кем разговаривать", не зависящий от словаря.
    consecutive_unrecognized = 0
    # Ф2 (17.07): счётчик УКЛОНЧИВЫХ ответов — «мимикрирующие» роботы
    # Яндекса отвечают на простые вопросы встречными («представьтесь»,
    # «откуда мой номер», «нужно подумать»), поэтому старый детект по
    # нераспознанным шагам их не ловит (они «отвечают»). Два уклончивых
    # подряд → LLM-проба «человек/робот» по накопленным репликам.
    evasive_streak = 0
    evasive_utterances = []
    UNRECOGNIZED_LIMIT = 3

    # Экономия эфира (24.07): роботы/автоответчики доигрывали весь сценарий
    # (медиана 88с = ~3 тарифные минуты Novofon), потому что грамматика
    # да/нет/число не даёт нам «услышать» их проговорки во время звонка.
    #   Слой 1 — длинная стартовая речь: живое «алло» коротко, робот выдаёт
    #     4-10с непрерывной речи → сразу свободная проба.
    #   Слой 2 — свободное распознавание на первых ходах (see LAYER2 ниже).
    #   Слой 3 — потолок «нет прогресса»: за ~35с ноль конкретных ответов
    #     → свободная проба + LLM.
    turn_index = 0                 # номер хода кандидата (0 = первый)
    dialog_started_at = time.time()
    no_progress_checked = False    # Слой 3 срабатывает один раз за звонок

    def _hangup_as_voicemail(reason_log: str, probe_text: str):
        """Единая точка «это робот/автоответчик — вешаю трубку»: копит
        собранное, ставит status=voicemail, прикладывает метрики, кладёт
        трубку. Возвращать после вызова."""
        log(reason_log)
        result["status"] = "voicemail"
        result["error"] = probe_text
        result["answers"] = sess.answers
        result["notes"] = sess.notes
        result["transcript"] = sess.transcript
        _attach_call_quality_note(result["notes"], call_metrics, log)
        try: call.hangup()
        except Exception: pass

    def _free_probe_is_robot(reason: str) -> bool:
        """Один круг СВОБОДНОГО распознавания (без грамматики) + детекторы:
        фразы автоответчика ИЛИ LLM «робот». По умолчанию — НЕ робот
        (живого не теряем). Возвращает True, если решено что робот."""
        log(f"🔎 {reason} — слушаю свободным распознаванием, робот ли...")
        probe = listen(call, vocab=None, silence_before_speech_sec=3.0, max_total_sec=10.0)
        if not probe:
            return False
        log(f"[свободное распознавание] «{probe}»")
        if is_voicemail_phrase(probe) or llm_is_robot_live(probe):
            _hangup_as_voicemail(
                f"📼 Автоответчик/робот подтверждён свободным распознаванием ({reason}).",
                probe)
            return True
        return False

    # Тайминги/качество связи по ходу разговора — чтобы при жалобе "были
    # большие паузы"/"плохое качество" можно было посмотреть в логе звонка,
    # а не гадать. latency_ms — пауза МЕЖДУ репликами (от момента когда
    # кандидат замолчал/линия установилась до старта следующей фразы бота);
    # tts_ms — сколько заняло непосредственно озвучивание (>~80мс = живой
    # синтез, не кэш); candidate_rms/agc_gain — громкость и усиление на
    # стороне кандидата (низкая громкость + большое усиление = плохая связь
    # или тихий голос на его стороне).
    call_metrics: list = []
    # Без bridge_established_at (звонок реально был отвечен, ДО проверки
    # автоответчика) latency первой фразы всегда считался бы от входа в
    # эту функцию — а это доли миллисекунды после detect_voicemail(),
    # который сам может слушать линию до 8с. Получалась метрика "первая
    # фраза: 0мс" на КАЖДОМ звонке — формально верная, но бесполезная:
    # реальное время ожидания кандидата (гудки+проверка автоответчика)
    # в неё не попадало вообще.
    last_event_at = bridge_established_at if bridge_established_at is not None else time.time()

    while True:
        st = _call_state_name(call)
        if "ENDED" in st or "FAIL" in st:
            # Пишем сырое SIP-состояние: "ENDED" и "FAIL" — разные истории
            # (кандидат положил трубку vs сбой SIP-стека), а для разбора
            # случаев типа "бот молчал и сразу пометил обрыв" (Максим,
            # тест 2026-07-03) важно видеть, что именно увидел pyVoIP.
            log(f"Звонок оборвался (SIP-состояние: {st}, шаг {sess.i + 1}/{len(sess.steps)}, "
                f"реплик в транскрипте: {len(sess.transcript)}).")
            result["status"] = "hangup_by_candidate"
            result["answers"] = sess.answers
            result["notes"] = sess.notes
            result["transcript"] = sess.transcript
            if sess.i < len(sess.steps):
                step = sess.steps[sess.i]
                result["dropped_at_step"] = step.get("crit", step["id"])
            _attach_call_quality_note(result["notes"], call_metrics, log)
            return

        preroll = None
        if action.kind in ("speak_then_listen", "speak_then_end"):
            # Филлер ("ага", "угу") ТОЛЬКО перед НОВЫМ вопросом — не перед
            # первой фразой звонка (нечему подтверждать) и не перед
            # повтором/переспросом (sess.reasked=True — ответ ведь не
            # поняли, подтверждать нечего, будет звучать нелепо). См.
            # dialog.FILLER_PHRASES и настройку settings.fillers сценария.
            if (fillers_enabled and not first_phrase and not sess.reasked
                    and action.kind == "speak_then_listen" and random.random() < 0.4):
                filler = random.choice(FILLER_PHRASES)
                log(f"[БОТ, филлер] {filler}")
                speak(call, filler, allow_interrupt=False, voice=voice, rate=rate)

            text = render_name(action.text, candidate_name)
            log(f"[БОТ] {text}")
            latency_ms = round((time.time() - last_event_at) * 1000)
            m = {"latency_ms": latency_ms}
            # Сразу после установки моста звук ещё не устаканился (эхо/
            # щелчки на подключении) — на самой первой фразе звонка
            # перебивание чаще ловит это как речь. Отключаем barge-in
            # только для первой фразы, дальше включаем как обычно.
            preroll = speak(call, text, allow_interrupt=not first_phrase, metrics=m,
                             voice=voice, rate=rate)
            first_phrase = False
            tts_note = " (вживую!)" if m.get("tts_ms", 0) > 80 else " (кэш)"
            log(f"[тайминг] пауза перед фразой: {latency_ms}мс · синтез: {m.get('tts_ms', 0)}мс{tts_note}")
            call_metrics.append(m)
            if preroll:
                log("[КАНДИДАТ] (перебил бота)")
        if action.kind != "speak_then_listen":
            break

        if sess.pending == "lmk_follow":
            vocab = vocab_for_step({"expect": "yesno"})
        else:
            cur_step = sess.steps[sess.i] if sess.i < len(sess.steps) else {}
            vocab = vocab_for_step(cur_step)

        m_listen = {}
        answer = listen(call, vocab=vocab, preroll_pcm16=preroll, metrics=m_listen)
        # Если не разобрано вообще НИЧЕГО, но при этом на линии не было даже
        # намёка на речь (тишина всё время слушания) — не переспрашиваем
        # вслух сразу. Кандидат мог просто думать дольше обычного или
        # реагировать с задержкой; переспрос вслух в такой момент перебивает
        # и раздражает чаще, чем помогает (жалоба с реальных тестов
        # 2026-07-03). Один лишний молчаливый круг ожидания — и только если
        # ПОСЛЕ него тоже тишина, переходим к обычному переспросу вслух.
        # Если же что-то похожее на речь звучало (спикер начал говорить, но
        # распознать не вышло — шум/невнятно), переспрос по делу, ждать
        # молча смысла нет.
        if not answer and not m_listen.get("speech_started") and not sess.reasked:
            log("[тишина] ничего не услышано, жду ещё раз молча (без переспроса вслух)...")
            m_listen2 = {}
            answer = listen(call, vocab=vocab, metrics=m_listen2)
            m_listen = m_listen2
        last_event_at = time.time()
        log(f"[КАНДИДАТ] {answer or '(тишина)'}")

        # ── ЭКОНОМИЯ ЭФИРА: ранний отсев робота (24.07, Слои 1-3) ──
        # Условие «когда звать пробу» — в _early_robot_probe_reason (тестируемо);
        # само решение робот/человек принимает проба (фразы + LLM, деф. человек).
        _probe_reason = _early_robot_probe_reason(
            turn_index, answer, bool(m_listen.get("speech_started")),
            m_listen.get("speech_dur_ms", 0), sess.i,
            time.time() - dialog_started_at, no_progress_checked)
        if _probe_reason:
            if "без прогресса" in _probe_reason:
                no_progress_checked = True
            if _free_probe_is_robot(_probe_reason):
                return
        turn_index += 1
        if m_listen.get("candidate_rms"):
            quality = ("тихо" if m_listen["candidate_rms"] < 800 else
                       "нормально" if m_listen["candidate_rms"] < 2500 else "громко")
            log(f"[аудио] кандидат: громкость ~{m_listen['candidate_rms']} ({quality})"
                + (f", усилено x{m_listen['agc_gain']}" if m_listen["agc_gain"] > 1.05 else ""))
        # Диагностика (2026-07-09): ответ не распознан — дампим сырое
        # входящее аудио окна в WAV + логируем сырой RMS и объём. Симптом
        # у Максима «плавает»: раз громко-мусор (~5847), раз тишина (н/д),
        # хотя серверная запись Novofon чистая и входящий кодек PCMA. Дамп
        # прогоню большой моделью: тишина в WAV → RTP не доходит до окна
        # (рассинхрон/старвейшн буфера pyVoIP); мусор → декод; чистая речь
        # → маленькая модель. Считаем СЫРОЙ rms всего окна (не медиану выше
        # порога, как candidate_rms) — он честно покажет тишина или сигнал.
        if not answer and m_listen.get("raw_pcm16"):
            raw = m_listen["raw_pcm16"]
            try:
                raw_rms = audioop.rms(raw, 2)
            except Exception:
                raw_rms = -1
            p = _dump_pcm16_wav(raw, tag=f"rms{raw_rms}")
            log(f"🧪 Не распознано. Сырой RMS окна={raw_rms}, аудио={len(raw)} байт "
                f"(~{len(raw)//16000}с)" + (f" — дамп: {p}" if p else " — дамп не записан"))
        if call_metrics:
            call_metrics[-1].update(m_listen)

        # Живой кандидат, просящий перезвонить позже ("занят, наберите
        # через час") — проверяем ПЕРЕД is_voicemail_phrase(), потому что
        # операторские заглушки говорят похожие слова ("перезвоните
        # позже") и раньше такой кандидат ошибочно улетал в status=
        # voicemail и терялся из воронки. Никакого авто-перезвона —
        # только честный статус, повторный набор строго вручную кнопкой
        # «Заново» (см. tasks/api/main.py _VC_STATUS_MAP).
        if answer and is_callback_request(answer):
            log(f"🔁 Кандидат просит перезвонить: «{answer}»")
            speak(call, CALLBACK_BYE_TEXT, allow_interrupt=False, voice=voice, rate=rate)
            result["status"] = "callback_requested"
            result["answers"] = sess.answers
            result["notes"] = sess.notes
            result["transcript"] = sess.transcript
            _attach_call_quality_note(result["notes"], call_metrics, log)
            try: call.hangup()
            except Exception: pass
            return

        # detect_voicemail() проверяет автоответчик/сообщение оператора
        # ТОЛЬКО один раз, до первой фразы бота. Если такое сообщение
        # начинает играть с задержкой (например бот уже успел поздороваться,
        # прежде чем сеть перевела звонок на "абонент не берёт трубку") —
        # раньше диалог продолжался как ни в чём не бывало, реально
        # разговаривая с автоответчиком весь звонок целиком (все шаги
        # "не распознано", реальные деньги за эфирное время потрачены
        # впустую). Проверяем ту же фразу-детекцию на КАЖДОЙ распознанной
        # реплике кандидата — если похоже на автоответчик, останавливаем
        # сценарий сразу, а не доигрываем его до конца вхолостую.
        if answer and is_voicemail_phrase(answer):
            log(f"📼 Похоже на автоответчик/сообщение оператора посреди разговора: «{answer}» — вешаю трубку.")
            result["status"] = "voicemail"
            result["error"] = answer
            result["answers"] = sess.answers
            result["notes"] = sess.notes
            result["transcript"] = sess.transcript
            _attach_call_quality_note(result["notes"], call_metrics, log)
            try: call.hangup()
            except Exception: pass
            return

        # Голосовой ринг-бэк «идёт дозвон, оставайтесь на линии» — абонент
        # ещё НЕ ответил, мы говорим в гудок. Это не автоответчик: исход
        # «не взял трубку» (перезвон уместен), а не «не распознали».
        if answer and is_ringback_phrase(answer):
            log(f"📞 На линии голосовой ринг-бэк (идёт дозвон): «{answer}» — не соединилось, вешаю трубку.")
            result["status"] = "no_answer"
            result["error"] = answer
            result["transcript"] = sess.transcript
            _attach_call_quality_note(result["notes"], call_metrics, log)
            try: call.hangup()
            except Exception: pass
            return

        if answer:
            cur_step = sess.steps[sess.i] if sess.i < len(sess.steps) else {}
            if answer_is_evasive(cur_step, answer):
                evasive_streak += 1
                evasive_utterances.append(answer)
                log(f"[поведение] уклончивый ответ #{evasive_streak}: «{answer[:60]}»")
                if evasive_streak >= 2:
                    probe_text = " ".join(evasive_utterances[-3:])
                    if llm_is_robot_live(probe_text):
                        log("🤖 Два уклончивых ответа подряд + LLM подтвердил робота — вешаю трубку.")
                        result["status"] = "voicemail"
                        result["error"] = probe_text
                        result["answers"] = sess.answers
                        result["notes"] = sess.notes
                        result["transcript"] = sess.transcript
                        _attach_call_quality_note(result["notes"], call_metrics, log)
                        try: call.hangup()
                        except Exception: pass
                        return
            else:
                evasive_streak = 0
            heard_anything = True
            consecutive_unrecognized = 0
        else:
            consecutive_unrecognized += 1
            if consecutive_unrecognized == 2:
                # Два шага подряд впустую — вероятно на линии играет
                # автоответчик/объявление сети, но шаги сценария слушают
                # с ОГРАНИЧЕННЫМ словарём (да/нет/возраст) и физически не
                # могут распознать его текст. Один пробный круг СВОБОДНЫМ
                # распознаванием (большая модель, без словаря): если там
                # буквально слышна фраза автоответчика — уверенный
                # status=voicemail уже на 2-м вопросе, а не расплывчатое
                # low_recognition после 3-го.
                log("🔎 Два шага подряд без ответа — слушаю свободным распознаванием, не автоответчик ли...")
                probe = listen(call, vocab=None, silence_before_speech_sec=3.0,
                               max_total_sec=10.0)
                if probe:
                    log(f"[свободное распознавание] «{probe}»")
                    # Двухступенчатый детект робота (16.07): (1) точные фразы;
                    # (2) если фразы мимо (STT искажает) — короткий LLM-вопрос
                    # «человек/робот» (llm_is_robot_live, таймаут 2.5с,
                    # по умолчанию — человек). Раньше роботы без точной фразы
                    # доигрывали весь сценарий: 83с медианы, 58 мин эфира на
                    # кампанию из 129 контактов.
                    if is_voicemail_phrase(probe) or llm_is_robot_live(probe):
                        log(f"📼 Автоответчик/робот подтверждён (фразы или LLM) — вешаю трубку.")
                        result["status"] = "voicemail"
                        result["error"] = probe
                        result["answers"] = sess.answers
                        result["notes"] = sess.notes
                        result["transcript"] = sess.transcript
                        _attach_call_quality_note(result["notes"], call_metrics, log)
                        try: call.hangup()
                        except Exception: pass
                        return
                    # Распозналась живая (не автоответчиковая) речь — на
                    # линии человек, просто словарные шаги его не поняли.
                    # Сбрасываем счётчик на 1: даём сценарию ещё шанс
                    # вместо скорого обрыва по лимиту.
                    consecutive_unrecognized = 1
            if consecutive_unrecognized >= UNRECOGNIZED_LIMIT:
                # Три вопроса подряд без единого распознанного слова — сигнал
                # "продолжать вслепую бессмысленно", но НЕ доказательство что
                # это автоответчик — это может быть и очень плохая линия у
                # живого кандидата. Специально отдельный статус от
                # "voicemail" (та проверка — по буквальному совпадению фразы
                # оператора, тут её нет): чтобы рекрутер не решил "точно
                # робот, можно не перезванивать" там, где это неверно.
                # Доигрывать оставшиеся вопросы вслепую всё равно не имеет
                # смысла — только тратить эфирное время (реальный случай:
                # весь сценарий из 9 вопросов "не распознано" и ложный
                # вердикт "passed" в конце).
                log(f"🔇 {consecutive_unrecognized} вопроса подряд без единого распознанного ответа — "
                    f"продолжать вслепую бессмысленно (не факт что автоответчик!). Завершаю звонок.")
                # Если кандидат УСПЕЛ дать ≥2 осмысленных ответа, а потом
                # пропал — это ОБРЫВ разговора, а не «речь не распознали»:
                # собранные ответы сохраняем, рекрутёр дозвонит (реальный
                # случай Рафат: назвал 52/Россия/Москва, потом замолчал —
                # терялся в «не распознали» с потерей уже собранного).
                meaningful = sum(1 for v in (sess.answers or {}).values()
                                 if str(v).strip() and not str(v).startswith("не распознано"))
                if meaningful >= 2:
                    result["status"] = "hangup_by_candidate"
                    log(f"   (но {meaningful} ответа уже собрано — помечаю как обрыв, не потеря)")
                else:
                    result["status"] = "low_recognition"
                result["error"] = f"{consecutive_unrecognized} шагов подряд без распознанного ответа"
                result["answers"] = sess.answers
                result["notes"] = sess.notes
                result["transcript"] = sess.transcript
                result["dropped_at_step"] = sess.steps[sess.i].get("crit", sess.steps[sess.i]["id"]) if sess.i < len(sess.steps) else None
                _attach_call_quality_note(result["notes"], call_metrics, log)
                try: call.hangup()
                except Exception: pass
                return
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
    # очень плохой линии, если фраза-детекция и пробный круг свободным
    # распознаванием (см. выше) не сработали — это резервный признак для
    # последующего разбора.
    if not heard_anything:
        result["possible_voicemail"] = True
    _attach_call_quality_note(result["notes"], call_metrics, log)


def _attach_call_quality_note(notes: dict, call_metrics: list, log) -> None:
    """Сводка тайминга/качества связи за звонок — и в лог (для быстрой
    диагностики жалоб "были паузы"/"плохое качество"), и в notes звонка
    (уже сохраняются на портале как есть, без миграций БД — см. ТЗ по
    паузам от 2026-07-03)."""
    if not call_metrics:
        return
    latencies = [m["latency_ms"] for m in call_metrics if "latency_ms" in m]
    tts_times = [m["tts_ms"] for m in call_metrics if "tts_ms" in m]
    live_tts = sum(1 for t in tts_times if t > 80)
    rms_values = [m["candidate_rms"] for m in call_metrics if m.get("candidate_rms")]
    max_gain = max((m.get("agc_gain", 1.0) for m in call_metrics), default=1.0)

    first_latency = latencies[0] if latencies else None
    avg_latency = round(sum(latencies) / len(latencies)) if latencies else None
    max_latency = max(latencies) if latencies else None
    avg_rms = round(sum(rms_values) / len(rms_values)) if rms_values else None

    summary = (
        f"первая фраза: {first_latency}мс · "
        f"сред. пауза между репликами: {avg_latency}мс (макс {max_latency}мс) · "
        f"живой синтез (не кэш): {live_tts}/{len(tts_times)} фраз · "
        f"громкость кандидата: {'~'+str(avg_rms) if avg_rms else 'н/д'}"
        + (f" (усиление до x{max_gain})" if max_gain > 1.05 else "")
    )
    log(f"[итог качества] {summary}")
    notes["Тех. качество звонка"] = summary


def _begin_precise_timer() -> bool:
    """Пункт 5 доработок 2026-07: winmm.timeBeginPeriod(1) просит Windows
    держать точность системного таймера ~1мс вместо стандартных ~15.6мс
    на время звонка. Это устраняет задокументированную причину щелчков в
    speak() (см. её докстринг про allow_interrupt=False): грубость
    time.sleep() между кусками звука периодически давала буферу
    воспроизведения пересыхать.

    ОБЯЗАТЕЛЬНО парно с _end_precise_timer() в finally — иначе
    повышенная точность таймера (и лишний расход энергии/CPU от неё)
    останется висеть на весь процесс агента, а не только на время
    звонка. Возвращает True только при реальном успехе — вызывающий код
    должен звать _end_precise_timer() ТОЛЬКО если это True (парность)."""
    try:
        import ctypes
        winmm = ctypes.WinDLL("winmm", use_last_error=True)
        winmm.timeBeginPeriod.argtypes = [ctypes.c_uint32]
        winmm.timeBeginPeriod.restype = ctypes.c_uint32
        return winmm.timeBeginPeriod(1) == 0  # TIMERR_NOERROR
    except Exception:
        return False


def _end_precise_timer() -> None:
    """Парный вызов к _begin_precise_timer() — звать ТОЛЬКО если тот
    вернул True."""
    try:
        import ctypes
        winmm = ctypes.WinDLL("winmm", use_last_error=True)
        winmm.timeEndPeriod.argtypes = [ctypes.c_uint32]
        winmm.timeEndPeriod.restype = ctypes.c_uint32
        winmm.timeEndPeriod(1)
    except Exception:
        pass


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
    _start_prewarm_for_name(scenario, candidate_name)

    result = {
        "status": "unknown", "verdict": None, "stop_reason": None,
        "answers": {}, "notes": {}, "transcript": [],
        "duration_s": 0, "error": None, "possible_voicemail": False,
        "dropped_at_step": None, "call_session_id": None,
    }

    phone = VoIPPhone(server=server, port=port, username=user, password=pwd,
                       myIP=local_ip, callCallback=lambda call: None)
    call_start = time.time()
    call = None
    precise_timer = _begin_precise_timer()
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

        bridge_established_at = time.time()
        # Раньше тут был отдельный detect_voicemail() ДО первой фразы бота
        # (слушал линию до 8с) — на реальных звонках это добавляло 2-3с
        # мёртвой тишины после того как кандидат уже сказал "алло" (плохое
        # первое впечатление, живой человек не выдерживает такую паузу
        # перед живым разговором). Детекция автоответчика теперь идёт ПО
        # ХОДУ диалога (is_voicemail_phrase на каждой реплике + пробный
        # круг свободным распознаванием после 2 нераспознанных подряд —
        # см. _run_dialog_loop), так что отдельная проверка тут не нужна:
        # здороваемся сразу.
        log("✅ Ответили! Начинаю диалог.")
        _run_dialog_loop(call, scenario, candidate_name, log, result, known_answers=known_answers,
                          on_transcript_update=on_transcript_update,
                          bridge_established_at=bridge_established_at)

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
        if precise_timer:
            _end_precise_timer()

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
    _start_prewarm_for_name(scenario, candidate_name)

    result = {
        "status": "unknown", "verdict": None, "stop_reason": None,
        "answers": {}, "notes": {}, "transcript": [],
        "duration_s": 0, "error": None, "possible_voicemail": False,
        "dropped_at_step": None, "call_session_id": None,
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
    precise_timer = _begin_precise_timer()
    try:
        log("Регистрируюсь в SIP...")
        phone.start()
        _patch_sip_socket(phone)
        if not _wait_sip_registered(phone, log):
            result["status"] = "error"
            result["error"] = ("SIP-линия не зарегистрирована — Novofon не сможет "
                               "перезвонить (звонок не начинался)")
            return result
        # Локального REGISTERED мало: коммутатор Novofon узнаёт о регистрации
        # примерно на 2 секунды позже (замер 27.07), и всё это время заявка
        # отлетает с sip_offline. Ждём подтверждения именно от него.
        _wait_novofon_sees_line(api_secret, user, log)

        # Заявка на звонок + ожидание обратного вызова. До ДВУХ кругов:
        # если Novofon сообщил, что линия числилась офлайн (sip_offline —
        # реальный случай 27.07, обе попытки владельца), перерегистрируемся
        # и пробуем ещё раз, вместо того чтобы отдавать «ошибка звонка».
        call = None
        for round_no in (1, 2, 3):
            log(f"Прошу Novofon перезвонить на линию {user} и соединить с +{target}...")
            try:
                call_session_id = call_api.start_employee_call(
                    api_secret, target, employee_id, user, virtual_number)
            except call_api.NovofonAPIError as e:
                result["status"] = "error"
                result["error"] = f"Call API отказал: {e}"
                log(f"❌ {result['error']}")
                return result
            result["call_session_id"] = call_session_id
            log(f"call_session_id={call_session_id}, жду входящий звонок на нашу линию...")

            # Ждём обратный звонок до 30с, НО параллельно рано проверяем,
            # не отказал ли Novofon мгновенно. Замер 27.07: при sip_offline
            # у него start_time == finish_time (0 секунд) — отказ виден сразу,
            # а мы честно выжидали все 30с. При частых sip_offline (7 из 8
            # звонков в тот день) это съедало по минуте на контакт.
            reason = None
            deadline = time.time() + 30
            next_probe = time.time() + 3
            while time.time() < deadline:
                try:
                    call = incoming.get(timeout=0.5)
                    break
                except queue.Empty:
                    pass
                if time.time() >= next_probe:
                    next_probe = time.time() + 3
                    try:
                        r = call_api.get_finish_reason(api_secret, call_session_id)
                    except Exception:
                        r = None
                    if r:  # звонок уже завершён на стороне Novofon
                        reason = r
                        break
            if call is not None:
                break
            if reason is None:
                try:
                    reason = call_api.get_finish_reason(api_secret, call_session_id)
                except Exception:
                    reason = None
            log(f"❌ Novofon не перезвонил (причина по их отчёту: {reason or 'неизвестна'})")
            if reason == "sip_offline" and round_no < 3:
                log(f"🔁 Линия числилась офлайн — перерегистрируюсь "
                    f"(круг {round_no + 1}/3)...")
                try:
                    phone.stop()
                except Exception:
                    pass
                time.sleep(1.5)  # даём Novofon усвоить де-регистрацию
                try:
                    phone.start()
                    _patch_sip_socket(phone)
                except Exception as e:
                    log(f"❌ Повторная SIP-регистрация не удалась: {e}")
                    result["status"] = "error"
                    result["error"] = f"SIP-линия офлайн, перерегистрация не удалась: {e}"
                    return result
                if _wait_sip_registered(phone, log):
                    _wait_novofon_sees_line(api_secret, user, log)
                    continue  # следующий круг
            result["status"] = "error"
            result["error"] = (
                "SIP-линия числилась офлайн у Novofon — он не перезвонил "
                "(звонок кандидату не уходил)" if reason == "sip_offline"
                else "Novofon не перезвонил на нашу линию"
                     + (f" (причина: {reason})" if reason else ""))
            return result
        if call is None:
            result["status"] = "error"
            result["error"] = "Novofon не перезвонил на нашу линию"
            return result

        log("Ответили на входящий от Novofon. Жду пока дозвонятся до кандидата...")
        # Определяем факт ответа кандидата ДВУМЯ путями параллельно в течение
        # ~35с (≈6-7 гудков): (1) статус «Разговор» из list.calls Novofon;
        # (2) РЕЧЬ на линии по RTP. Раньше ждали только статус, а звук
        # проверяли ОДИН раз в конце и по громкости — из-за чего (2026-07-10):
        #   • терялась Анна: Novofon статус не отдал, её короткое «алё»
        #     прозвучало во время ожидания, а поздняя разовая проверка его уже
        #     не застала → ложный «не взял трубку» (в логе такое 46 раз);
        #   • гудок/тон ринг-бэка Яндекса (громкий!) проходил как «живой звук»
        #     → бот говорил в тон → «не распознали». Теперь слушаем РЕЧЬ
        #     (_line_has_speech), а не громкость: тон слов не даёт и НЕ считается
        #     ответом (уходит в «не взял трубку/гудок», а не в «не распознали»).
        bridged, bridge_reason, leg_states = False, "", []
        _bridge_deadline = time.time() + 35
        while time.time() < _bridge_deadline and not bridged:
            if "ENDED" in _call_state_name(call) or "FAIL" in _call_state_name(call):
                break
            b, leg_states = call_api.wait_for_contact_talking(
                api_secret, call_session_id, timeout=2.0, poll_interval=1.0)
            if b:
                bridged, bridge_reason = True, "статус Novofon «Разговор»"
                break
            if _line_has_speech(call, check_sec=2.5):
                bridged, bridge_reason = True, "речь на линии"
                break
        if not bridged:
            result["status"] = "no_answer"
            log(f"Кандидат не взял трубку (на линии только гудок/тишина, речи нет). "
                f"Ноги звонка: {leg_states}, SIP: {_call_state_name(call)}.")
            try: call.hangup()
            except Exception: pass
            return result

        bridge_established_at = time.time()
        # См. комментарий в run_call() — отдельная detect_voicemail() ДО
        # первой фразы бота убрана: добавляла 2-3с тишины после того как
        # кандидат уже ответил. Автоответчик теперь ловится по ходу
        # диалога (_run_dialog_loop).
        log(f"✅ Кандидат на линии ({bridge_reason})! Начинаю диалог.")
        _run_dialog_loop(call, scenario, candidate_name, log, result, known_answers=known_answers,
                          on_transcript_update=on_transcript_update,
                          bridge_established_at=bridge_established_at)

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
        if precise_timer:
            _end_precise_timer()

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
    _cli_settings = scenario.get("settings") or {}
    _cli_voice = _cli_settings.get("voice") or DEFAULT_VOICE
    _cli_rate = _cli_settings.get("rate") or "+0%"
    print("Прогрев TTS (генерирую все фразы сценария)...")
    prewarm_scenario(scenario, voice=_cli_voice, rate=_cli_rate, verbose=False,
                      extra_texts=FILLER_PHRASES)
    for t in all_reask_texts():
        synthesize_telephony_pcm(t, voice=_cli_voice, rate=_cli_rate)
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
