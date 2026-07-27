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

    def test_stuck_after_ceiling_triggers(self):
        assert reason(turn=4, answer="да", speech=True, dur=1000,
                      sess_i=1, elapsed=50) is not None

    def test_progress_after_ceiling_ok(self):
        # ушли на 4-й вопрос — живой отвечает, не трогаем
        assert reason(turn=6, answer="да", speech=True, dur=1000,
                      sess_i=4, elapsed=60) is None

    def test_stuck_but_before_ceiling_ok(self):
        # 40с — ещё в запасе (порог 45с), живой мог задуматься
        assert reason(turn=2, answer="да", speech=True, dur=1000,
                      sess_i=1, elapsed=40) is None

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


class TestSipRegistrationGuard2707:
    """Фикс 27.07: тестовый звонок владельца дважды дал «ошибка звонка».
    Novofon сообщил finish_reason='sip_offline' — наша линия числилась у него
    офлайн, поэтому он не перезвонил для сведения разговора, а мы 30 секунд
    ждали впустую и писали загадочное «Novofon не перезвонил»."""

    class _Phone:
        """Мини-заглушка VoIPPhone: отдаёт статусы по заданному сценарию."""
        def __init__(self, statuses):
            self._statuses = list(statuses)
        def get_status(self):
            return self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]

    def test_registered_passes_immediately(self):
        from pyVoIP.VoIP import PhoneStatus
        ph = self._Phone([PhoneStatus.REGISTERED])
        assert pc._wait_sip_registered(ph, lambda *_: None, timeout=1.0) is True

    def test_registering_then_registered_waits(self):
        from pyVoIP.VoIP import PhoneStatus
        ph = self._Phone([PhoneStatus.REGISTERING, PhoneStatus.REGISTERING,
                          PhoneStatus.REGISTERED])
        assert pc._wait_sip_registered(ph, lambda *_: None, timeout=2.0) is True

    def test_failed_stops_fast(self):
        from pyVoIP.VoIP import PhoneStatus
        logged = []
        ph = self._Phone([PhoneStatus.FAILED])
        assert pc._wait_sip_registered(ph, lambda m: logged.append(m), timeout=5.0) is False
        assert logged, "должны залогировать причину"

    def test_stuck_registering_times_out(self):
        from pyVoIP.VoIP import PhoneStatus
        ph = self._Phone([PhoneStatus.REGISTERING])
        assert pc._wait_sip_registered(ph, lambda *_: None, timeout=0.3) is False

    def test_timeout_constant_is_sane(self):
        # регистрация на здоровой сети ~100мс; таймаут должен быть заметно
        # меньше 30с ожидания обратного звонка, чтобы не терять время зря
        assert 2.0 <= pc.SIP_REGISTER_TIMEOUT_SEC <= 15.0


class TestNovofonLineStateWait2707:
    """Корневая причина массовых sip_offline (замер 27.07): pyVoIP рапортует
    REGISTERED за ~120мс, а коммутатор Novofon переключает physical_state на
    «Зарегистрирован» только через ~2с. Мы успевали попросить перезвонить в
    это окно — и получали sip_offline. Ждём подтверждения ОТ NOVOFON."""

    def test_registered_state_accepted(self, monkeypatch):
        import call_api
        monkeypatch.setattr(call_api, "get_sip_line_state",
                            lambda *a, **k: "Зарегистрирован")
        assert pc._wait_novofon_sees_line("tok", "0125878", lambda *_: None,
                                          timeout=2.0) is True

    def test_not_registered_times_out(self, monkeypatch):
        import call_api
        monkeypatch.setattr(call_api, "get_sip_line_state",
                            lambda *a, **k: "Не зарегистрирован")
        assert pc._wait_novofon_sees_line("tok", "0125878", lambda *_: None,
                                          timeout=1.0) is False

    def test_becomes_registered_after_delay(self, monkeypatch):
        import call_api
        calls = {"n": 0}
        def state(*a, **k):
            calls["n"] += 1
            return "Не зарегистрирован" if calls["n"] < 3 else "Зарегистрирован"
        monkeypatch.setattr(call_api, "get_sip_line_state", state)
        assert pc._wait_novofon_sees_line("tok", "0125878", lambda *_: None,
                                          timeout=5.0) is True
        assert calls["n"] >= 3, "должны были дождаться смены статуса"

    def test_api_error_does_not_crash(self, monkeypatch):
        import call_api
        def boom(*a, **k):
            raise RuntimeError("api down")
        monkeypatch.setattr(call_api, "get_sip_line_state", boom)
        assert pc._wait_novofon_sees_line("tok", "0125878", lambda *_: None,
                                          timeout=0.8) is False

    def test_timeout_constant_sane(self):
        # 2с типовое ожидание; таймаут должен давать запас, но не тормозить обзвон
        assert 5.0 <= pc.NOVOFON_LINE_TIMEOUT_SEC <= 20.0
