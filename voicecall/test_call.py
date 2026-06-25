"""Тест реального звонка через Novofon.
Звонит на указанный номер, при ответе играет тон 440Hz 3 сек, кладёт трубку.

Требует:
  - валидные SIP-креденшалы в C:\\ProgramData\\sth\\voicecall.env
  - активированный аккаунт Novofon (документы подтверждены)
  - положительный баланс (хотя бы на 1 минуту разговора)

Запуск:
    python voicecall/test_call.py +79991234567

Где +79991234567 — твой ВТОРОЙ мобильник для теста.
"""
import math
import os
import struct
import sys
import time
import wave
from pathlib import Path

# Чиним вывод эмодзи/кириллицы на Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from _sip_config import get_local_ip, load_env, require

try:
    try:
        from pyVoIP.VoIP import VoIPPhone, CallState  # type: ignore
    except ImportError:
        from pyVoIP.VoIP.phone import VoIPPhone  # type: ignore
        from pyVoIP.VoIP.call import CallState  # type: ignore
except ImportError as e:
    print("❌ pyVoIP не установлена. Запусти install_windows.ps1", file=sys.stderr)
    print(f"   {e}", file=sys.stderr)
    sys.exit(1)


SCRIPT_DIR = Path(__file__).parent
ASSETS = SCRIPT_DIR / "assets"
ASSETS.mkdir(exist_ok=True)
TONE_PATH = ASSETS / "tone_440_3s.wav"


def generate_tone_wav(path: Path, freq_hz: int = 440, duration_s: float = 3.0):
    """Простой синус с плавным fade-in/out, 8000Hz моно 16-bit (стандарт SIP)."""
    sample_rate = 8000
    n_samples = int(sample_rate * duration_s)
    amplitude = 0.3
    fade_samples = int(0.1 * sample_rate)  # 100мс fade
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(n_samples):
            t = i / sample_rate
            # огибающая (мягкие края)
            if i < fade_samples:
                env = i / fade_samples
            elif i > n_samples - fade_samples:
                env = (n_samples - i) / fade_samples
            else:
                env = 1.0
            sample = int(32767 * amplitude * env * math.sin(2 * math.pi * freq_hz * t))
            wf.writeframes(struct.pack("<h", sample))
    print(f"Сгенерил тестовый звук: {path}")


def normalize_number(raw: str) -> str:
    """Чистим номер до формата 7XXXXXXXXXX (Novofon принимает без +)."""
    digits = "".join(c for c in raw if c.isdigit())
    if digits.startswith("8") and len(digits) == 11:
        return "7" + digits[1:]
    if digits.startswith("7") and len(digits) == 11:
        return digits
    if len(digits) == 10:
        return "7" + digits
    return digits


def main():
    if len(sys.argv) < 2:
        print("Использование: python voicecall/test_call.py <НОМЕР>")
        print("Пример:        python voicecall/test_call.py +79991234567")
        sys.exit(1)

    target = normalize_number(sys.argv[1])
    if not target:
        print(f"❌ Не понял номер: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)

    env = load_env()
    server = require(env, "SIP_SERVER")
    port = int(env.get("SIP_PORT", "5060"))
    user = require(env, "SIP_USER")
    pwd = require(env, "SIP_PASS")
    local_ip = get_local_ip()

    if not TONE_PATH.exists():
        generate_tone_wav(TONE_PATH)

    print("=== SIP outbound call test ===")
    print(f"From:    {user}@{server}")
    print(f"To:      +{target}")
    print(f"Local IP: {local_ip}")
    print(f"Audio:   {TONE_PATH}")
    print()

    phone = VoIPPhone(
        server=server,
        port=port,
        username=user,
        password=pwd,
        myIP=local_ip,
        callCallback=lambda call: None,
    )

    print("Регистрируюсь...")
    try:
        phone.start()
    except Exception as e:
        print(f"❌ Регистрация не удалась: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
    print("✅ REGISTERED")

    print(f"Звоню на +{target}...")
    try:
        call = phone.call(target)
    except Exception as e:
        print(f"❌ phone.call() упал: {type(e).__name__}: {e}", file=sys.stderr)
        phone.stop()
        sys.exit(3)

    # Ждём ответа кандидата — максимум 30 сек
    answered = False
    deadline = time.time() + 30
    while time.time() < deadline:
        state = getattr(call, "state", None)
        # state может быть Enum (CallState.ANSWERED) или строкой
        name = getattr(state, "name", str(state)).upper()
        if "ANSWER" in name:
            answered = True
            break
        if "ENDED" in name or "FAIL" in name or "BUSY" in name:
            print(f"❌ Звонок завершился до ответа: state={name}")
            break
        time.sleep(0.5)

    if answered:
        print("✅ Ответили. Проигрываю тестовый звук...")
        try:
            with open(TONE_PATH, "rb") as f:
                audio = f.read()
            # pyVoIP writeAudio принимает PCM 8000Hz mono 16bit
            # Пропускаем 44 байта WAV-заголовка
            call.writeAudio(audio[44:])
            time.sleep(4)  # даём проиграть тон + чуть-чуть тишины
        except Exception as e:
            print(f"⚠️  Ошибка при отдаче аудио: {type(e).__name__}: {e}")
        print("Кладу трубку.")
        try:
            call.hangup()
        except Exception:
            pass
    else:
        print("Никто не взял трубку за 30 секунд.")

    time.sleep(1)
    try:
        phone.stop()
    except Exception:
        pass
    print("=== Тест завершён ===")


if __name__ == "__main__":
    main()
