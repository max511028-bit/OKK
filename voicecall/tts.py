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
# На 2026 Microsoft Edge TTS даёт только эти 2 русских голоса (облачные,
# требуют интернет). Локальный Silero (see silero_server.py, свой процесс
# на 127.0.0.1:5001) добавляет ещё 5 голосов офлайн — префикс "silero:" в
# имени голоса отличает движок (см. synthesize_telephony_pcm). Полный
# список для UI конструктора сценариев:
VOICE_CHOICES = [
    {"id": "ru-RU-SvetlanaNeural", "label": "Светлана (облако, женский)"},
    {"id": "ru-RU-DmitryNeural",   "label": "Дмитрий (облако, мужской)"},
    {"id": "silero:kseniya",       "label": "Ксения (локальный, женский)"},
    {"id": "silero:baya",          "label": "Байя (локальный, женский)"},
    {"id": "silero:xenia",         "label": "Ксения-2 (локальный, женский)"},
    {"id": "silero:aidar",         "label": "Айдар (локальный, мужской)"},
    {"id": "silero:eugene",        "label": "Евгений (локальный, мужской)"},
]
SILERO_URL = os.getenv("SILERO_URL", "http://127.0.0.1:5001")

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


def _cache_key(text: str, voice: str, rate: str = "+0%") -> str:
    # rate ОБЯЗАТЕЛЬНО в ключе — иначе разные скорости одного голоса
    # схлопнутся в один и тот же файл кэша, и настройка скорости
    # сценария будет молча игнорироваться после первого прогона.
    # "||trim1" — версия обрезки тишины по краям (пункт 1 доработок
    # 2026-07): без неё старые НЕобрезанные файлы кэша (сгенерированные
    # до этой правки) продолжали бы отдаваться как есть.
    h = hashlib.sha1((voice + "||" + rate + "||trim1||" + text).encode("utf-8")).hexdigest()[:16]
    return h


def _get_ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def _rate_to_atempo(rate: str) -> float:
    """"+10%" -> 1.10, "-5%" -> 0.95, "+0%"/пусто -> 1.0. Формат тот же,
    что и у edge-tts rate, чтобы настройка скорости в конструкторе
    сценариев работала одинаково независимо от выбранного движка."""
    if not rate:
        return 1.0
    try:
        pct = float(rate.strip().rstrip("%"))
    except ValueError:
        return 1.0
    return max(0.5, min(2.0, 1.0 + pct / 100.0))  # ffmpeg atempo лимит 0.5-2.0


def _trim_edges_pcm(pcm: bytes) -> bytes:
    """Обрезает тишину ТОЛЬКО по краям (в начале и в конце), не трогая
    паузы между предложениями внутри фразы. Пункт 1 доработок 2026-07:
    у синтезированных фраз (особенно edge-tts) в конце висит ~0.8-1.4с
    цифровой тишины — бот проигрывает её в трубку ДО того как начинает
    слушать ответ, кандидат ~секунду говорит "в пустоту", диалог кажется
    тормозным.

    ВАЖНО: фильтр из превью-эндпоинта портала (main.py, stop_periods=-1)
    режет ВСЕ паузы по всей фразе — его сюда копировать НЕЛЬЗЯ (бот стал
    бы тараторить без пауз между предложениями). Рецепт "только края"
    (проверен замером на реальных фразах): обрезать начало, развернуть
    поток, обрезать "новое начало" (= бывший конец), развернуть обратно.

    Вход/выход: сырой PCM s16le 8000Hz mono. Лучшее старание — при любой
    ошибке ffmpeg, пустом/подозрительно коротком результате возвращаем
    ИСХОДНЫЙ pcm без изменений (звонок важнее косметики; защита от
    съедания всей фразы на очень тихих голосах)."""
    if not pcm:
        return pcm
    ffmpeg_exe = _get_ffmpeg_exe()
    trim_filter = (
        "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB,"
        "areverse,"
        "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB,"
        "areverse"
    )
    import subprocess
    try:
        proc = subprocess.run(
            [ffmpeg_exe, "-y", "-loglevel", "error",
             "-f", "s16le", "-ar", "8000", "-ac", "1", "-i", "pipe:0",
             "-af", trim_filter,
             "-f", "s16le", "-ar", "8000", "-ac", "1", "pipe:1"],
            input=pcm, capture_output=True,
        )
    except Exception:
        return pcm
    if proc.returncode != 0:
        return pcm
    out = proc.stdout
    # Защита: если после обрезки осталось меньше 0.2с — что-то пошло не
    # так (слишком тихий голос целиком ушёл под порог), отдаём исходник.
    if len(out) < int(0.2 * 8000 * 2):
        return pcm
    return out


def _pcm_from_mp3(mp3: bytes, rate: str = "+0%") -> bytes:
    """MP3 (любой sample rate) -> PCM 8000Hz mono 16bit. rate применяется
    только если он ЕЩЁ не учтён в самом синтезе (silero не умеет
    скорость на входе — единственный путь туда) через ffmpeg atempo."""
    ffmpeg_exe = _get_ffmpeg_exe()
    args = [ffmpeg_exe, "-y", "-loglevel", "error", "-i", "pipe:0"]
    atempo = _rate_to_atempo(rate)
    if abs(atempo - 1.0) > 0.001:
        args += ["-af", f"atempo={atempo}"]
    args += ["-ar", "8000", "-ac", "1", "-acodec", "pcm_s16le", "-f", "s16le", "pipe:1"]

    import subprocess
    proc = subprocess.run(args, input=mp3, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg fail: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout


def _synth_silero_pcm(text: str, voice_name: str, rate: str = "+0%") -> bytes:
    """voice_name без префикса "silero:" (например "kseniya"). Просит
    сервер сразу 8000Hz — silero честно умеет этот sample rate, поэтому
    ffmpeg тут нужен только для скорости (atempo), не для ресэмплинга."""
    import urllib.request as _ur
    import urllib.parse as _up
    url = SILERO_URL + "/tts?" + _up.urlencode({
        "text": text, "voice": voice_name, "sample_rate": 8000,
    })
    with _ur.urlopen(url, timeout=30) as resp:
        wav_bytes = resp.read()
    with io.BytesIO(wav_bytes) as buf, wave.open(buf, "rb") as wf:
        if wf.getframerate() != 8000 or wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise RuntimeError(f"silero вернул неожиданный формат: "
                                f"{wf.getframerate()}Hz {wf.getsampwidth()*8}bit "
                                f"{wf.getnchannels()}ch")
        pcm = wf.readframes(wf.getnframes())
    atempo = _rate_to_atempo(rate)
    if abs(atempo - 1.0) > 0.001:
        ffmpeg_exe = _get_ffmpeg_exe()
        import subprocess
        proc = subprocess.run(
            [ffmpeg_exe, "-y", "-loglevel", "error",
             "-f", "s16le", "-ar", "8000", "-ac", "1", "-i", "pipe:0",
             "-af", f"atempo={atempo}",
             "-ar", "8000", "-ac", "1", "-acodec", "pcm_s16le", "-f", "s16le", "pipe:1"],
            input=pcm, capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg atempo fail: {proc.stderr.decode('utf-8', 'replace')[:200]}")
        pcm = proc.stdout
    return pcm


def synthesize_telephony_pcm(text: str, voice: str = DEFAULT_VOICE,
                              use_cache: bool = True, rate: str = "+0%") -> bytes:
    """Возвращает PCM 8000Hz mono 16bit (формат для SIP/телефонии).
    voice с префиксом "silero:" (например "silero:kseniya") идёт через
    локальный Silero-сервер (voicecall/silero_server.py, свой процесс на
    127.0.0.1:5001) — иначе через облачный edge-tts, как раньше.
    Кэширует результат на диске (ключ учитывает voice+rate) — повторные
    вызовы для тех же фраз/настроек мгновенные."""
    if not text or not text.strip():
        # Пустой текст (баг сценария — конструктор не должен такого
        # допускать, но случалось) — edge-tts на пустую строку падает с
        # "вернул пустой ответ", а это раньше рушило весь звонок целиком
        # вместе с уже собранными ответами кандидата. Просто тишина.
        return b""
    if use_cache:
        cache_path = CACHE_DIR / f"pcm_{_cache_key(text, voice, rate)}.raw"
        if cache_path.exists():
            return cache_path.read_bytes()

    if voice.startswith("silero:"):
        voice_name = voice.split(":", 1)[1]
        try:
            pcm = _synth_silero_pcm(text, voice_name, rate)
        except Exception as e:
            # Лучшее старание: локальный сервер может быть не запущен —
            # звонок не должен падать из-за выбора голоса, откатываемся
            # на дефолтный облачный.
            print(f"⚠️  Silero недоступен ({type(e).__name__}: {e}), откат на {DEFAULT_VOICE}",
                  file=sys.stderr)
            mp3 = synthesize_mp3(text, DEFAULT_VOICE, rate=rate)
            pcm = _pcm_from_mp3(mp3, rate="+0%")  # rate уже применён в synthesize_mp3
    else:
        mp3 = synthesize_mp3(text, voice, rate=rate)
        pcm = _pcm_from_mp3(mp3, rate="+0%")  # rate уже применён в synthesize_mp3

    # Обрезка тишины по краям — применяется к ОБОИМ движкам, ПЕРЕД
    # кэшированием (в кэш кладём уже обрезанное — при звонке ffmpeg не
    # вызывается, скорость не страдает). См. _trim_edges_pcm.
    pcm = _trim_edges_pcm(pcm)

    if use_cache:
        try:
            cache_path.write_bytes(pcm)
        except Exception:
            pass
    return pcm


def prewarm_scenario(scenario_dict: dict, voice: str = DEFAULT_VOICE,
                      verbose: bool = True, rate: str = "+0%",
                      extra_texts: Optional[list] = None) -> int:
    """Заранее генерирует и кэширует TTS для всех фраз сценария.
    Возвращает кол-во новых сгенерированных файлов.

    voice/rate ОБЯЗАТЕЛЬНО должны совпадать с тем, что реально звучит в
    звонке (см. настройки сценария) — иначе прогрев кладёт в кэш файлы
    под одним ключом, а speak() во время звонка просит другой (другой
    голос/скорость = другой ключ кэша, см. _cache_key), кэш промахивается
    и фраза синтезируется вживую посреди разговора.

    extra_texts: доп. фразы вне самого сценария — например филлеры
    ("ага", "угу", см. dialog.FILLER_PHRASES), которые тоже реально
    звучат в звонке, но не являются частью steps/closing."""
    texts = list(extra_texts or [])
    for st in scenario_dict.get("steps", []):
        # on_yes пропускали — а это реальная озвучиваемая фраза (см.
        # dialog.py, last_bot=step.get("on_yes") на "да" в yesno-вопросе
        # с концом сценария), из-за чего она синтезировалась вживую
        # прямо посреди звонка вместо кэша — заметная пауза, а если
        # текст вдруг пуст — падение всего звонка (edge-tts не
        # переваривает пустую строку).
        for key in ("bot", "on_yes", "on_no", "on_no_follow", "stop_msg"):
            v = st.get(key)
            if v:
                texts.append(v)
    closing = scenario_dict.get("closing")
    if closing:
        texts.append(closing)
    new_count = 0
    for i, t in enumerate(texts, 1):
        cache_path = CACHE_DIR / f"pcm_{_cache_key(t, voice, rate)}.raw"
        if cache_path.exists():
            if verbose: print(f"  [{i}/{len(texts)}] кэш есть: {t[:50]}...")
            continue
        if verbose: print(f"  [{i}/{len(texts)}] генерирую: {t[:50]}...")
        synthesize_telephony_pcm(t, voice, use_cache=True, rate=rate)
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
