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
import json
import sys
import time
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from _sip_config import get_local_ip, load_env, require
from dialog import DialogSession, load_scenario, DEFAULT_SCENARIO, vocab_for_step, render_name
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
    """16-bit signed PCM → 8-bit unsigned linear (формат write_audio pyVoIP)."""
    if not pcm16:
        return b""
    signed8 = audioop.lin2lin(pcm16, 2, 1)
    return audioop.bias(signed8, 1, 128)


def _pyvoip_to_pcm16(raw8: bytes) -> bytes:
    """8-bit unsigned linear (формат read_audio pyVoIP) → 16-bit signed PCM."""
    if not raw8:
        return b""
    signed8 = audioop.bias(raw8, 1, -128)
    return audioop.lin2lin(signed8, 1, 2)


def _call_state_name(call) -> str:
    state = getattr(call, "state", None)
    return getattr(state, "name", str(state)).upper()


def speak(call, text: str) -> None:
    """Озвучивает фразу в трубку и ждёт пока она реально доиграет.
    write_audio() кладёт данные в буфер, фоновый поток pyVoIP отдаёт их
    по 20мс в реальном времени — поэтому ждём расчётную длительность."""
    pcm16 = synthesize_telephony_pcm(text)
    raw8 = _pcm16_to_pyvoip(pcm16)
    call.write_audio(raw8)
    duration_s = len(raw8) / 8000.0  # 8000 байт/сек при 8kHz, 1 байт/сэмпл
    time.sleep(duration_s + 0.15)  # +запас перед началом прослушки


def listen(call, vocab: Optional[list] = None,
           silence_after_speech_sec: float = 0.9,
           silence_before_speech_sec: float = 6.0,
           max_total_sec: float = 20.0,
           on_partial=None) -> str:
    """Слушает ответ кандидата из телефонной линии до тишины.
    Останавливается досрочно если звонок оборвался (кандидат положил трубку)."""
    rec = StreamingRecognizer(input_sample_rate=8000, vocab=vocab)
    parts = []
    last_partial = ""
    last_change_at = time.time()
    speech_started = False
    start = time.time()
    chunk_len = 160  # 20мс при 8kHz, 1 байт/сэмпл (8-bit)

    while True:
        now = time.time()
        if now - start > max_total_sec:
            break
        st = _call_state_name(call)
        if "ENDED" in st or "FAIL" in st:
            break
        try:
            raw8 = call.read_audio(length=chunk_len, blocking=True)
        except Exception:
            break
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


def run_call(phone_number: str, scenario_id: str = DEFAULT_SCENARIO,
             known_answers: Optional[dict] = None,
             candidate_name: str = "",
             on_log=None) -> dict:
    """Совершает один реальный звонок и ведёт полный диалог.

    known_answers: {crit: value} — заготовка под Стадию 4 (предпроверка
                   и подстановка уже известных из Excel ответов). В этой
                   версии ещё не используется движком напрямую — добавим
                   когда будем делать импорт с пропуском заполненных полей.
    candidate_name: подставляется в текст бота вместо {name}.

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
        "duration_s": 0, "error": None,
    }

    phone = VoIPPhone(server=server, port=port, username=user, password=pwd,
                       myIP=local_ip, callCallback=lambda call: None)
    call_start = time.time()
    call = None
    try:
        log("Регистрируюсь в SIP...")
        phone.start()
        log(f"Звоню на +{target}...")
        call = phone.call(target)

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

        log("✅ Ответили! Начинаю диалог.")

        sess = DialogSession(scenario)
        action = sess.start()
        while True:
            st = _call_state_name(call)
            if "ENDED" in st or "FAIL" in st:
                log("Звонок оборвался (кандидат положил трубку).")
                result["status"] = "hangup_by_candidate"
                break

            if action.kind in ("speak_then_listen", "speak_then_end"):
                text = render_name(action.text, candidate_name)
                log(f"[БОТ] {text}")
                speak(call, text)
            if action.kind != "speak_then_listen":
                break

            if sess.pending == "lmk_follow":
                vocab = vocab_for_step({"expect": "yesno"})
            else:
                cur_step = sess.steps[sess.i] if sess.i < len(sess.steps) else {}
                vocab = vocab_for_step(cur_step)

            answer = listen(call, vocab=vocab)
            log(f"[КАНДИДАТ] {answer or '(тишина)'}")
            action = sess.submit_answer(answer)

        result["answers"] = action.answers
        result["notes"] = action.notes
        result["transcript"] = action.transcript
        result["verdict"] = action.end_verdict
        result["stop_reason"] = action.end_reason
        if result["status"] == "unknown":
            result["status"] = "answered_completed"

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
        print("Использование: python voicecall/phone_call.py <НОМЕР> [scenario_id]")
        print("Пример:        python voicecall/phone_call.py +79991234567")
        sys.exit(1)
    number = sys.argv[1]
    scenario_id = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_SCENARIO

    print("Прогрев Vosk-модели...")
    stt_warmup()
    scenario = load_scenario(scenario_id)
    print("Прогрев TTS (генерирую все фразы сценария)...")
    prewarm_scenario(scenario, voice=DEFAULT_VOICE, verbose=False)
    print()

    result = run_call(number, scenario_id, candidate_name="")

    print()
    print("═════ ИТОГ ЗВОНКА ═════")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
