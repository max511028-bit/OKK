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


def synthesize_telephony_pcm(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Возвращает PCM 8000Hz mono 16bit (формат для SIP/телефонии).
    Использует ffmpeg из пакета imageio-ffmpeg (ставится через pip)."""
    mp3 = synthesize_mp3(text, voice)
    try:
        import imageio_ffmpeg  # type: ignore
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        # Fallback на системный ffmpeg
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
    return proc.stdout


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
