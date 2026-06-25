"""Локальный бот — полный pipeline голосового обзвона работающий через
колонки и микрофон твоего ПК. БЕЗ телефонии.

Что делает:
1. Загружает сценарий (по умолчанию tander-sterlitamak-pack)
2. На каждом шаге:
   - TTS озвучивает вопрос → играет через колонки
   - Слушает микрофон до тишины (~1.5 сек после последней речи)
   - STT распознаёт ответ
   - Dialog engine решает следующий шаг
3. В конце печатает вердикт, ответы, транскрипт.

Запуск:
    python voicecall/local_bot.py
    python voicecall/local_bot.py tander-sterlitamak-pack    # выбор сценария

Когда подключим SIP — заменим play_pcm() и listen_until_silence() на
функции работающие с SIP-аудио (pyVoIP). Вся остальная логика останется.
"""
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Импорты наших модулей
from dialog import DialogSession, load_scenario, DEFAULT_SCENARIO, vocab_for_step
from tts import synthesize_telephony_pcm, prewarm_scenario, DEFAULT_VOICE
from stt import StreamingRecognizer, warmup as stt_warmup


def _check_deps():
    missing = []
    try:
        import sounddevice  # type: ignore # noqa
    except ImportError:
        missing.append("sounddevice")
    try:
        import numpy  # type: ignore # noqa
    except ImportError:
        missing.append("numpy")
    if missing:
        print(f"❌ Не установлены: {', '.join(missing)}", file=sys.stderr)
        print("   Запусти: pip install sounddevice numpy", file=sys.stderr)
        sys.exit(1)


def play_pcm_8khz(pcm: bytes):
    """Играет 8 kHz mono 16-bit PCM в колонки (как будто это голос в трубку)."""
    import numpy as np
    import sounddevice as sd
    if not pcm:
        return
    samples = np.frombuffer(pcm, dtype=np.int16)
    sd.play(samples, samplerate=8000, blocking=False)
    sd.wait()


def play_with_interruption(pcm: bytes,
                            interrupt_threshold_mult: float = 2.5,
                            interrupt_min_rms: float = 1200.0,
                            interrupt_sustain_ms: int = 250,
                            baseline_warmup_ms: int = 600) -> bool:
    """Играет TTS-аудио и параллельно слушает микрофон. Если детектирует
    громкий сигнал на микрофоне (явно громче эхо из колонок) — обрывает
    воспроизведение и возвращает True.

    Это barge-in: можно начать говорить когда бот ещё не дозакончил.

    Параметры:
      interrupt_threshold_mult — во сколько раз громкость должна превысить
        baseline чтобы засчитать как прерывание (2.5 = в 2.5 раза громче)
      interrupt_min_rms        — минимальный абсолютный уровень (защита от
        слишком тихого baseline на старте)
      interrupt_sustain_ms     — сколько мс должно быть громко подряд
        (фильтр от мгновенных хлопков/щелчков)
      baseline_warmup_ms       — сколько мс в начале НЕ детектим прерывание
        (даём эхо колонок «прогреться» в baseline)
    """
    import numpy as np
    import sounddevice as sd
    if not pcm:
        return False

    samples = np.frombuffer(pcm, dtype=np.int16)
    sd.play(samples, samplerate=8000, blocking=False)

    # Микрофон 16kHz, чанки по 50мс
    mic_sr = 16000
    chunk_samples = int(mic_sr * 0.05)  # 50мс
    mic = sd.RawInputStream(samplerate=mic_sr, channels=1, dtype="int16",
                            blocksize=chunk_samples)
    mic.start()
    baseline_samples = []   # для оценки уровня эхо во время воспроизведения
    loud_sustained_ms = 0   # счётчик «громко подряд»
    started = time.time()
    interrupted = False

    try:
        while True:
            # Проверяем, играет ли ещё аудио
            try:
                still_playing = sd.get_stream().active
            except Exception:
                still_playing = False
            if not still_playing:
                break

            try:
                data, _ = mic.read(chunk_samples)
            except Exception:
                break
            np_data = np.frombuffer(bytes(data), dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(np_data ** 2))) if np_data.size else 0.0

            elapsed_ms = (time.time() - started) * 1000
            # Прогрев: первые 600мс просто копим baseline эхо
            if elapsed_ms < baseline_warmup_ms:
                baseline_samples.append(rms)
                continue
            # Поддерживаем baseline скользящим средним
            baseline_samples.append(rms)
            if len(baseline_samples) > 30:
                baseline_samples.pop(0)
            baseline = max(float(np.median(baseline_samples)), 200.0)

            if rms > baseline * interrupt_threshold_mult and rms > interrupt_min_rms:
                loud_sustained_ms += 50
                if loud_sustained_ms >= interrupt_sustain_ms:
                    interrupted = True
                    try: sd.stop()
                    except Exception: pass
                    break
            else:
                loud_sustained_ms = 0
    finally:
        try:
            mic.stop()
            mic.close()
        except Exception:
            pass

    if not interrupted:
        try: sd.wait()
        except Exception: pass
    return interrupted


def listen_until_silence(
    sample_rate: int = 16000,
    silence_after_speech_sec: float = 0.7,    # было 1.5 — режем задержку
    silence_before_speech_sec: float = 6.0,
    max_total_sec: float = 20.0,
    print_progress: bool = True,
    vocab: Optional[list] = None,             # словарь для конкретного шага
) -> str:
    """Слушает микрофон. Возвращает распознанный текст.

    Логика остановки:
    - Если кандидат начал говорить и потом замолчал на silence_after_speech_sec — стоп
    - Если кандидат вообще не говорит дольше silence_before_speech_sec — стоп (тишина)
    - Жёсткий лимит — max_total_sec
    """
    import numpy as np
    import sounddevice as sd
    rec = StreamingRecognizer(input_sample_rate=sample_rate, vocab=vocab)
    parts = []
    last_partial = ""
    last_change_at = time.time()
    speech_started = False
    start = time.time()

    # Чанки по 100 мс — достаточно частый опрос для отзывчивости
    chunk_samples = int(sample_rate * 0.1)
    chunk_bytes_size = chunk_samples * 2

    def status_print(text):
        if print_progress:
            print(f"  🎤 {text}", end="\r", flush=True)

    stream = sd.RawInputStream(
        samplerate=sample_rate, channels=1, dtype="int16",
        blocksize=chunk_samples,
    )
    stream.start()
    try:
        while True:
            now = time.time()
            if now - start > max_total_sec:
                if print_progress: print(" " * 80, end="\r")
                break
            data, overflowed = stream.read(chunk_samples)
            chunk = bytes(data)
            r = rec.feed(chunk)
            if r.get("final"):
                # Vosk детектировал конец фразы — добавляем в результат
                parts.append(r["final"])
                last_partial = ""
                last_change_at = now
                if speech_started:
                    # уже что-то сказали и Vosk закрыл фразу — это хороший признак конца
                    status_print(f"[услышано: {r['final']}]")
                    break
            else:
                cur = r.get("partial", "")
                if cur != last_partial:
                    last_partial = cur
                    last_change_at = now
                    if cur:
                        speech_started = True
                        status_print(f"[слышу: {cur[:60]}]")
            # Проверка тишины
            silence = now - last_change_at
            if speech_started and silence >= silence_after_speech_sec:
                if print_progress: print(" " * 80, end="\r")
                break
            if not speech_started and silence >= silence_before_speech_sec:
                if print_progress: print(" " * 80, end="\r")
                break
    finally:
        stream.stop()
        stream.close()

    tail = rec.finalize()
    if tail:
        parts.append(tail)
    return " ".join(p for p in parts if p).strip()


def run(scenario_id: str):
    _check_deps()
    scenario = load_scenario(scenario_id)
    print(f"╔══ Сценарий: {scenario['name']}")
    print(f"╚══ Голос:    {DEFAULT_VOICE}")
    print(f"   Стоп-факторы: {', '.join(scenario.get('stop_factors', []))}")
    print()

    # ── ПРОГРЕВ ── (один раз при старте — экономит секунды на каждом шаге)
    print("Прогрев Vosk-модели...")
    t0 = time.time()
    stt_warmup()
    print(f"  ✅ Vosk готов ({time.time()-t0:.1f} сек)")

    print("Прогрев TTS — генерирую все фразы сценария...")
    t0 = time.time()
    new_n = prewarm_scenario(scenario, voice=DEFAULT_VOICE, verbose=False)
    print(f"  ✅ TTS готов (новых: {new_n}, всего {time.time()-t0:.1f} сек)")
    print()
    print("══════════════════════════════════════════════")
    print(" Начинаем — говори в микрофон когда бот замолчит")
    print("══════════════════════════════════════════════")

    sess = DialogSession(scenario)
    action = sess.start()

    while True:
        if action.kind in ("speak_then_listen", "speak_then_end"):
            print(f"\n[БОТ]  {action.text}")
            pcm = synthesize_telephony_pcm(action.text)
            if action.kind == "speak_then_end":
                # На финальной фразе барджин не нужен — просто играем до конца
                play_pcm_8khz(pcm)
            else:
                interrupted = play_with_interruption(pcm)
                if interrupted:
                    print("  ⚡ перебил бота — слушаю...")
        if action.kind != "speak_then_listen":
            break
        # Vocab под ожидаемый тип ответа — резко поднимает точность Vosk
        if sess.pending == "lmk_follow":
            vocab = vocab_for_step({"expect": "yesno"})
        else:
            cur_step = sess.steps[sess.i] if sess.i < len(sess.steps) else {}
            vocab = vocab_for_step(cur_step)

        print("  🎙 слушаю...", end="\r", flush=True)
        answer = listen_until_silence(vocab=vocab)
        if answer:
            print(f"[ТЫ]   {answer}")
        else:
            print("[ТЫ]   (молчание)")
        action = sess.submit_answer(answer)

    print()
    print("═════ ИТОГИ ═════")
    print(f"Вердикт:  {action.end_verdict}")
    if action.end_reason:
        print(f"Причина:  {action.end_reason}")
    if action.answers:
        print(f"Ответы:")
        for k, v in action.answers.items():
            print(f"  • {k}: {v}")
    if action.notes:
        print(f"Заметки:")
        for k, v in action.notes.items():
            print(f"  • {k}: {v}")
    print(f"Реплик в транскрипте: {len(action.transcript)}")


def main():
    scenario_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SCENARIO
    try:
        run(scenario_id)
    except KeyboardInterrupt:
        print("\n\nПрерывание пользователем (Ctrl+C).")


if __name__ == "__main__":
    main()
