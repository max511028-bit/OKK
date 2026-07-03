"""STT-модуль для voicecall: аудио → текст ответа кандидата.

Использует Vosk (open source, оффлайн распознавание).
Модель — vosk-model-small-ru-0.22 (~45 МБ, хватает для коротких ответов
типа «да/нет/27 лет/гражданин РФ»).

Два режима:
  - StreamingRecognizer — для звонка: кормишь чанки PCM, получаешь partial
    и final результаты по мере распознавания (Vosk сам решает где границы)
  - recognize_wav_file(path) — для оффлайн-теста: даёшь WAV-файл,
    получаешь текст целиком

CLI режим — проверка:
    python voicecall/stt.py                                  # распознает
    # последний test_output_8khz.wav из tts_cache
    python voicecall/stt.py /path/to/some.wav                # любой WAV
"""
import audioop  # backported для Python 3.13+ через audioop-lts (см. requirements.txt)
import json
import os
import sys
import wave
import zipfile
from pathlib import Path
from typing import Optional
from urllib.request import urlretrieve

# Vosk-модель small-ru-0.22 обучена на 16 kHz. SIP/телефония даёт 8 kHz.
# Поэтому держим внутри 16 kHz и ресэмплим всё входящее в него.
VOSK_SAMPLE_RATE = 16000

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ДВЕ модели с разными ролями:
#
# Большая (vosk-model-ru-0.42, ~1.8 ГБ) — точное СВОБОДНОЕ распознавание:
# открытые ответы кандидата, перепроверка записей, детекция автоответчика.
# ВАЖНО: грамматику (ограничение словаря) она НЕ поддерживает — при
# передаче vocab выдаёт "Runtime graphs are not supported by this model"
# и работает некорректно (эмпирически проверено 2026-07-03: ни честного
# ограничения, ни нормального свободного распознавания — брак на выходе).
#
# Маленькая (vosk-model-small-ru-0.22, ~45 МБ) — шаги с ограниченным
# словарём (да/нет, возраст, пол...): грамматику поддерживает честно,
# на коротких ответах из известного списка слов точнее и в разы быстрее.
VOSK_MODEL_NAME = "vosk-model-ru-0.42"
VOSK_SMALL_MODEL_NAME = "vosk-model-small-ru-0.22"
# Кладём модели в путь без кириллицы — Vosk на Windows плохо работает с
# не-ASCII символами в пути (известная проблема C++ библиотеки).
MODEL_DIR = Path(os.getenv("VOSK_MODEL_DIR") or rf"C:\ProgramData\sth\{VOSK_MODEL_NAME}")
SMALL_MODEL_DIR = Path(os.getenv("VOSK_SMALL_MODEL_DIR") or rf"C:\ProgramData\sth\{VOSK_SMALL_MODEL_NAME}")


def _has_model_files(d: Path) -> bool:
    """Проверка что в папке реально есть файлы модели Vosk."""
    if not d.exists() or not d.is_dir():
        return False
    # У модели обязательны папки am/, conf/, graph/
    return all((d / sub).is_dir() for sub in ("am", "conf", "graph"))


def _ensure_model_at(model_name: str, model_dir: Path, size_hint: str):
    """Скачивает Vosk-модель если её нет на диске. Один раз."""
    if _has_model_files(model_dir):
        return
    url = f"https://alphacephei.com/vosk/models/{model_name}.zip"
    print(f"Vosk-модель не найдена в {model_dir}")
    print(f"Скачиваю с {url} ({size_hint}, подожди)...")
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    zip_path = model_dir.parent / f"{model_name}.zip"
    try:
        urlretrieve(url, zip_path)
    except Exception as e:
        print(f"❌ Не удалось скачать: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  скачано, распаковываю...")
    # Чистим если что-то осталось от прошлой неудачной попытки
    extracted = model_dir.parent / model_name
    import shutil
    if extracted.exists() and extracted != model_dir:
        shutil.rmtree(extracted)
    if model_dir.exists():
        shutil.rmtree(model_dir)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(model_dir.parent)
    # Архив распаковывается в папку с именем модели — переименуем если надо
    if extracted.exists() and extracted != model_dir:
        extracted.rename(model_dir)
    zip_path.unlink(missing_ok=True)
    if not _has_model_files(model_dir):
        print(f"❌ Модель распакована, но обязательных файлов нет в {model_dir}",
              file=sys.stderr)
        sys.exit(1)
    print(f"  ✅ модель готова: {model_dir}")


def ensure_model():
    _ensure_model_at(VOSK_MODEL_NAME, MODEL_DIR, "~1.8 ГБ")


def ensure_small_model():
    _ensure_model_at(VOSK_SMALL_MODEL_NAME, SMALL_MODEL_DIR, "~45 МБ")


def _check_deps():
    try:
        import vosk  # type: ignore  # noqa
    except ImportError:
        print("❌ vosk не установлен.", file=sys.stderr)
        print("   Запусти install_windows.ps1 или: pip install vosk", file=sys.stderr)
        sys.exit(1)


# Кэшируем загруженные модели — Model() занимает секунды на загрузку
_MODEL_CACHE = None
_SMALL_MODEL_CACHE = None


def _vosk_quiet():
    try:
        from vosk import SetLogLevel  # type: ignore
        SetLogLevel(-1)
    except Exception:
        pass


def _get_model():
    global _MODEL_CACHE
    if _MODEL_CACHE is None:
        _check_deps()
        ensure_model()
        from vosk import Model  # type: ignore
        _vosk_quiet()
        _MODEL_CACHE = Model(str(MODEL_DIR))
    return _MODEL_CACHE


def _get_small_model():
    global _SMALL_MODEL_CACHE
    if _SMALL_MODEL_CACHE is None:
        _check_deps()
        ensure_small_model()
        from vosk import Model  # type: ignore
        _vosk_quiet()
        _SMALL_MODEL_CACHE = Model(str(SMALL_MODEL_DIR))
    return _SMALL_MODEL_CACHE


def warmup():
    """Принудительно загрузить обе модели сейчас (пред-прогрев в начале сессии)."""
    _get_model()
    _get_small_model()


class StreamingRecognizer:
    """Покадровое распознавание для звонка. Vosk сам определяет где
    конец фразы — feed() вернёт {final: текст} когда фраза завершилась.

    input_sample_rate — частота входящего аудио (8000 для SIP, 16000 для
    качественного микрофона). Автоматически апсемплим в 16 kHz который
    нужен модели.

    vocab — необязательный список слов/фраз. Если задан, Vosk ограничивает
    словарь только ими (НАМНОГО лучшая точность). Добавь '[unk]' чтобы
    модель могла обозначать неопознанные слова."""

    def __init__(self, input_sample_rate: int = 8000, vocab: Optional[list] = None):
        from vosk import KaldiRecognizer  # type: ignore
        # С vocab — МАЛЕНЬКАЯ модель: только она честно поддерживает
        # грамматику (ограничение словаря). Большая на vocab выдаёт
        # "Runtime graphs are not supported" и распознаёт некорректно
        # (см. комментарий у VOSK_MODEL_NAME). Без vocab — большая,
        # у неё заметно точнее свободное распознавание.
        if vocab:
            model = _get_small_model()
            grammar = json.dumps(vocab, ensure_ascii=False)
            self._rec = KaldiRecognizer(model, VOSK_SAMPLE_RATE, grammar)
        else:
            model = _get_model()
            self._rec = KaldiRecognizer(model, VOSK_SAMPLE_RATE)
        self._rec.SetWords(False)
        self._in_sr = input_sample_rate
        self._needs_resample = input_sample_rate != VOSK_SAMPLE_RATE
        self._resample_state = None

    def _resample(self, chunk: bytes) -> bytes:
        if not self._needs_resample:
            return chunk
        # audioop.ratecv(fragment, sample_width, n_channels, in_rate, out_rate, state)
        out, self._resample_state = audioop.ratecv(
            chunk, 2, 1, self._in_sr, VOSK_SAMPLE_RATE, self._resample_state
        )
        return out

    def feed(self, pcm_chunk: bytes) -> dict:
        """Кормим аудио (PCM 16-bit mono в input_sample_rate). Возвращаем:
           {final: str, partial: ''}      — если Vosk детектировал конец фразы
           {final: None, partial: str}    — иначе, текущее частичное распознавание
        """
        chunk = self._resample(pcm_chunk)
        if self._rec.AcceptWaveform(chunk):
            res = json.loads(self._rec.Result())
            return {"final": (res.get("text") or "").strip(), "partial": ""}
        res = json.loads(self._rec.PartialResult())
        return {"final": None, "partial": (res.get("partial") or "").strip()}

    def finalize(self) -> str:
        """Принудительный финал — забираем что осталось в буфере."""
        res = json.loads(self._rec.FinalResult())
        return (res.get("text") or "").strip()


def recognize_wav_file(path: str) -> str:
    """Распознавание целого WAV-файла. Должен быть mono 16bit PCM. Любой
    sample rate — ресэмплим автоматически. Возвращает СКЛЕЕННЫЙ текст:
    Vosk может разбить большое аудио на несколько фраз, ловим все final'ы."""
    with wave.open(path, "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError(f"WAV должен быть mono 16bit, получили "
                             f"channels={wf.getnchannels()}, samp={wf.getsampwidth()}")
        rec = StreamingRecognizer(input_sample_rate=wf.getframerate())
        parts = []
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            r = rec.feed(data)
            if r.get("final"):
                parts.append(r["final"])
        tail = rec.finalize()
        if tail:
            parts.append(tail)
        return " ".join(p for p in parts if p)


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        # дефолт — последний сгенерированный TTS-семпл
        default = Path(__file__).parent / "tts_cache" / "test_output_8khz.wav"
        if not default.exists():
            print("❌ Не передан WAV-файл, и tts_cache/test_output_8khz.wav не существует.",
                  file=sys.stderr)
            print("   Сначала: python voicecall/tts.py \"Какой-то текст\"", file=sys.stderr)
            sys.exit(1)
        path = str(default)

    if not Path(path).exists():
        print(f"❌ Файл не найден: {path}", file=sys.stderr)
        sys.exit(1)

    print(f"Распознаю: {path}")
    print()
    text = recognize_wav_file(path)
    print(f"Распознанный текст:")
    print(f"  {text}")


if __name__ == "__main__":
    main()
