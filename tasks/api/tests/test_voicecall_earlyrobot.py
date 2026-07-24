"""Экономия эфира (24.07): ранний отсев робота/автоответчика во время звонка.
Тестируем чистую функцию-решатель phone_call._early_robot_probe_reason —
КОГДА звать свободную пробу (само решение робот/человек принимает уже проба
из фраз+LLM, здесь не проверяется). Живого кандидата не должны трогать зря."""
import sys
from pathlib import Path

VOICECALL_DIR = Path(__file__).resolve().parents[3] / "voicecall"
sys.path.insert(0, str(VOICECALL_DIR))

import phone_call as pc  # noqa: E402


def reason(turn=0, answer="", speech=False, dur=0, sess_i=0, elapsed=0.0, np_used=False):
    return pc._early_robot_probe_reason(turn, answer, speech, dur, sess_i, elapsed, np_used)


class TestLayer1LongOpening:
    """Слой 1: длинная непрерывная речь на первых ходах = приветствие робота."""

    def test_long_opening_triggers(self):
        # 5с непрерывной речи на 1-м ходу — не «алло», а автоответчик
        assert reason(turn=0, answer="", speech=True, dur=5000) is not None
        assert reason(turn=1, answer="спасибо", speech=True, dur=4000) is not None

    def test_short_hello_does_not_trigger(self):
        # живое «алло, да» ~800мс — не трогаем
        assert reason(turn=0, answer="да", speech=True, dur=800) is None
        assert reason(turn=1, answer="да", speech=True, dur=1200) is None

    def test_long_speech_only_on_first_turns(self):
        # на 3-м ходу длинная речь уже не повод (человек мог развёрнуто ответить)
        assert reason(turn=2, answer="", speech=True, dur=6000, sess_i=3, elapsed=20) is None


class TestLayer2FirstTurnUnrecognized:
    """Слой 2: на 1-м ходу была речь, но грамматика её не разобрала."""

    def test_speech_but_no_answer_turn0(self):
        assert reason(turn=0, answer="", speech=True, dur=1500) is not None

    def test_silence_turn0_does_not_trigger(self):
        # тишина (человек молчит/думает) — не робот
        assert reason(turn=0, answer="", speech=False, dur=0) is None

    def test_recognized_answer_turn0_ok(self):
        assert reason(turn=0, answer="да", speech=True, dur=1000) is None


class TestLayer3NoProgress:
    """Слой 3: за ~35с не ушли дальше 1-2 вопроса — робот топчется."""

    def test_stuck_after_35s_triggers(self):
        assert reason(turn=4, answer="да", speech=True, dur=1000,
                      sess_i=1, elapsed=40) is not None

    def test_progress_after_35s_ok(self):
        # ушли на 4-й вопрос — живой отвечает, не трогаем
        assert reason(turn=6, answer="да", speech=True, dur=1000,
                      sess_i=4, elapsed=50) is None

    def test_stuck_but_early_ok(self):
        # 20с — рано, живой мог задуматься
        assert reason(turn=2, answer="да", speech=True, dur=1000,
                      sess_i=1, elapsed=20) is None

    def test_fires_once(self):
        # уже проверяли (no_progress_used=True) — второй раз молчим
        assert reason(turn=5, answer="да", speech=True, dur=1000,
                      sess_i=1, elapsed=50, np_used=True) is None


class TestLiveCandidateNotDisturbed:
    """Здоровый живой сценарий: короткие ответы, прогресс по шагам — ни один
    ход не должен звать пробу."""

    def test_normal_dialog_never_probes(self):
        # имитация живого: короткие «да», возраст, город; шаги растут
        turns = [
            dict(turn=0, answer="да",              speech=True, dur=900,  sess_i=0, elapsed=3),
            dict(turn=1, answer="двадцать пять",   speech=True, dur=1400, sess_i=1, elapsed=12),
            dict(turn=2, answer="российское",      speech=True, dur=1100, sess_i=2, elapsed=20),
            dict(turn=3, answer="москва",          speech=True, dur=1000, sess_i=3, elapsed=28),
            dict(turn=4, answer="да",              speech=True, dur=700,  sess_i=4, elapsed=34),
        ]
        for t in turns:
            assert reason(**t) is None, t
