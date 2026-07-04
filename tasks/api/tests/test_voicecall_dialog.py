"""Юнит-тесты для voicecall/dialog.py — чистая логика без телефонии,
поэтому тестируется напрямую (без FastAPI client)."""
import sys
from pathlib import Path

VOICECALL_DIR = Path(__file__).resolve().parents[3] / "voicecall"
sys.path.insert(0, str(VOICECALL_DIR))

import dialog  # noqa: E402


class TestVoicemailPhraseDetection:
    """Пополнение базы фраз-автоответчиков (2026-07) — каждая фраза
    должна ловиться, а реальные короткие ответы кандидата — нет."""

    def test_catches_new_operator_phrases(self):
        phrases = [
            "аппарат абонента выключен или находится",
            "телефон выключен пожалуйста перезвоните позже",
            "абонент вне зоны обслуживания сети",
            "невозможно установить соединение с абонентом",
            "все каналы связи заняты попробуйте позже",
            "попробуйте позвонить попозже чуть позже",
            "вы можете перезвонить позднее",
            "оставьте голосовое сообщение после сигнала",
            "чтобы записать сообщение нажмите решетку",
            "набранный вами номер не существует",
            "здравствуйте вы позвонили ивану оставьте сообщение",
        ]
        for p in phrases:
            assert dialog.is_voicemail_phrase(p), f"должен поймать: {p}"

    def test_still_catches_previously_added_phrases(self):
        """Регресс: фразы из прошлых итераций (2026-07-03) не должны
        сломаться при добавлении новых."""
        phrases = [
            "абонент не берет трубку попробуйте перезвонить позднее",
            "не слышу человеческую речь",
            "нет возможности взять трубку",
        ]
        for p in phrases:
            assert dialog.is_voicemail_phrase(p), f"должен ловить как раньше: {p}"

    def test_real_candidate_answers_not_flagged(self):
        """Короткие реальные ответы на закрытые вопросы (да/нет/возраст/
        etc) не должны ложно попадать под автоответчик."""
        answers = [
            "да удобно", "нет не было", "двадцать девять", "да мужчина",
            "алло да слушаю", "подождите секунду возьму трубку",
            "нет спасибо не интересно вакансия не подходит",
        ]
        for p in answers:
            assert not dialog.is_voicemail_phrase(p), f"ложное срабатывание: {p}"

    def test_empty_and_none_are_safe(self):
        assert dialog.is_voicemail_phrase("") is False
        assert dialog.is_voicemail_phrase(None) is False


class TestLlmClassifyDeadCache:
    """Пункт 1: LLM-классификатор не должен держать кандидата в тишине
    10+ секунд, если сервис недоступен — таймаут короче и есть кэш
    недоступности на повторные попытки в течение той же кампании."""

    def test_skips_call_while_marked_dead(self, monkeypatch):
        import time
        dialog._LLM_DEAD_UNTIL = time.time() + 300
        t0 = time.time()
        result = dialog.llm_classify({"expect": "yesno", "bot": "?"}, "неразборчиво")
        dt = time.time() - t0
        assert result is None
        assert dt < 0.05, f"должен вернуться мгновенно из кэша, занял {dt}s"
        dialog._LLM_DEAD_UNTIL = 0.0  # не протекать в другие тесты

    def test_unknown_expect_returns_none_without_network(self):
        dialog._LLM_DEAD_UNTIL = 0.0
        assert dialog.llm_classify({"expect": "free"}, "что угодно") is None
