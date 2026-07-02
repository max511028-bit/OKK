"""TTS-модуль для voicecall: текст → аудио голосом бота.

Использует edge-tts (нейросетевой Microsoft Edge TTS, бесплатно).
Русские голоса: DmitryNeural (мужской), DariyaNeural / SvetlanaNeural (женские).

Два формата вывода:
  - mp3 (для прослушки на ПК) — компактно, 24kHz
  - pcm 8kHz mono 16bit (для отправки в SIP) — нужен для телефонии

CLI режим — для проверки:
    python voicecall/tts.py "Здравствуйте, тест связи"
"""
import asyncio
import hashlib
import io
import os
import sys
import wave
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


DEFAULT_VOICE = "ru-RU-SvetlanaNeural"  # женский, под имя «Алина» в нашем сценарии
ALT_VOICES = {
    "female":   "ru-RU-SvetlanaNeural",
    "male":     "ru-RU-DmitryNeural",
}
# На 2026 Microsoft Edge TTS даёт только эти 2 русских голоса.
# Если нужно больше — silero TTS (open source, требует PyTorch) или
# Yandex SpeechKit (платно, ~₽16 за 1000 символов).

CACHE_DIR = Path(__file__).parent / "tts_cache"
CACHE_DIR.mkdir(exist_ok=True)


def _check_deps():
    try:
        import edge_tts  # type: ignore  # noqa
    except ImportError:
        print("❌ edge-tts не установлен.", file=sys.stderr)
        print("   Запусти install_windows.ps1 (он обновит зависимости)", file=sys.stderr)
        sys.exit(1)


async def _synth_mp3(text: str, voice: str = DEFAULT_VOICE,
                     rate: str = "+0%", pitch: str = "+0Hz") -> bytes:
    """Скачиваем MP3 из edge-tts (24kHz mono)."""
    import edge_tts  # type: ignore
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    data = buf.getvalue()
    if not data:
        raise RuntimeError("edge-tts вернул пустой ответ")
    return data


def synthesize_mp3(text: str, voice: str = DEFAULT_VOICE,
                   rate: str = "+0%", pitch: str = "+0Hz") -> bytes:
    """Синхронная обёртка над async — возвращает MP3-байты."""
    _check_deps()
    return asyncio.run(_synth_mp3(text, voice, rate, pitch))


def _cache_key(text: str, voice: str) -> str:
    h = hashlib.sha1((voice + "||" + text).encode("utf-8")).hexdigest()[:16]
    return h


def synthesize_telephony_pcm(text: str, voice: str = DEFAULT_VOICE,
                              use_cache: bool = True) -> bytes:
    """Возвращает PCM 8000Hz mono 16bit (формат для SIP/телефонии).
    Использует ffmpeg из пакета imageio-ffmpeg. Кэширует результат на
    диске — повторные вызовы для тех же фраз мгновенные."""
    if not text or not text.strip():
        # Пустой текст (баг сценария — конструктор не должен такого
        # допускать, но случалось) — edge-tts на пустую строку падает с
        # "вернул пустой ответ", а это раньше рушило весь звонок целиком
        # вместе с уже собранными ответами кандидата. Просто тишина.
        return b""
    if use_cache:
        cache_path = CACHE_DIR / f"pcm_{_cache_key(text, voice)}.raw"
        if cache_path.exists():
            return cache_path.read_bytes()

    mp3 = synthesize_mp3(text, voice)
    try:
        import imageio_ffmpeg  # type: ignore
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg_exe = "ffmpeg"

    import subprocess
    proc = subprocess.run(
        [ffmpeg_exe, "-y", "-loglevel", "error",
         "-i", "pipe:0",
         "-ar", "8000", "-ac", "1", "-acodec", "pcm_s16le",
         "-f", "s16le", "pipe:1"],
        input=mp3, capture_output=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg fail: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    pcm = proc.stdout
    if use_cache:
        try:
            cache_path.write_bytes(pcm)
        except Exception:
            pass
    return pcm


def prewarm_scenario(scenario_dict: dict, voice: str = DEFAULT_VOICE,
                      verbose: bool = True) -> int:
    """Заранее генерирует и кэширует TTS для всех фраз сценария.
    Возвращает кол-во новых сгенерированных файлов."""
    texts = []
    for st in scenario_dict.get("steps", []):
        for key in ("bot", "on_no", "on_no_follow", "stop_msg"):
            v = st.get(key)
            if v:
                texts.append(v)
    closing = scenario_dict.get("closing")
    if closing:
        texts.append(closing)
    new_count = 0
    for i, t in enumerate(texts, 1):
        cache_path = CACHE_DIR / f"pcm_{_cache_key(t, voice)}.raw"
        if cache_path.exists():
            if verbose: print(f"  [{i}/{len(texts)}] кэш есть: {t[:50]}...")
            continue
        if verbose: print(f"  [{i}/{len(texts)}] генерирую: {t[:50]}...")
        synthesize_telephony_pcm(t, voice, use_cache=True)
        new_count += 1
    return new_count


def save_wav(pcm_8khz: bytes, path: Path):
    """Сохраняет 8000Hz mono 16bit PCM в .wav (для прослушки)."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(pcm_8khz)


def main():
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        text = "Здравствуйте, меня зовут Алина, компания СТХ. Это тестовая фраза для проверки голоса бота."
        print(f"(Текст не указан, использую дефолтный)")
    voice = os.environ.get("TTS_VOICE", DEFAULT_VOICE)
    print(f"Голос:  {voice}")
    print(f"Текст:  {text}")
    print()

    print("Шаг 1: генерирую MP3 через edge-tts...")
    mp3 = synthesize_mp3(text, voice)
    mp3_path = CACHE_DIR / "test_output.mp3"
    mp3_path.write_bytes(mp3)
    print(f"  ✅ MP3 сохранён: {mp3_path} ({len(mp3)} байт)")

    print("Шаг 2: конвертирую в SIP-формат (8kHz mono 16bit PCM)...")
    try:
        pcm = synthesize_telephony_pcm(text, voice)
        wav_path = CACHE_DIR / "test_output_8khz.wav"
        save_wav(pcm, wav_path)
        print(f"  ✅ WAV сохранён: {wav_path} ({len(pcm)} байт PCM)")
    except Exception as e:
        print(f"  ⚠️  Не удалось конвертировать в SIP-формат: {e}")
        print(f"     (для SIP-телефонии нужен ffmpeg, но MP3 для прослушки готов)")

    print()
    print("Открой получившиеся файлы в плеере чтобы проверить как звучит.")
    print("Хочешь автозапуск? Передай TTS_PLAY=1 в env.")

    if os.environ.get("TTS_PLAY") == "1":
        try:
            os.startfile(str(mp3_path))  # Windows
        except Exception:
            print(f"  Открой вручную: {mp3_path}")


if __name__ == "__main__":
    main()
