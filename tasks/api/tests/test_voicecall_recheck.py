"""Юнит-тесты пере-валидации ответов по записи (voicecall/dispatch_agent.py,
_recheck_critical_answers). Чистая логика — LLM подменяем стабом, сети нет."""
import sys
from pathlib import Path

VOICECALL_DIR = Path(__file__).resolve().parents[3] / "voicecall"
sys.path.insert(0, str(VOICECALL_DIR))

import dispatch_agent as da  # noqa: E402


TRANSCRIPT = ("[Дорожка 1] да двадцать девять двадцать девять двадцать "
              "российская москва да да есть")

SCENARIO = {"steps": [
    {"crit": "Актуальность", "expect": "yesno", "bot": "находитесь в поиске работы?"},
    {"crit": "Возраст", "expect": "age", "bot": "Сколько вам полных лет?"},
    {"crit": "Гражданство", "expect": "citizen_rf", "bot": "Ваше гражданство"},
    {"crit": "Город", "expect": "free", "bot": "В каком городе проживаете?"},
    {"crit": "ЛМК", "expect": "free", "bot": "Есть медицинская книжка?"},
    {"crit": "Опыт", "expect": "free", "bot": "был опыт работы?"},
]}


def _fake_llm(prompt: str) -> str:
    p = prompt.lower()
    if "возраст кандидата" in p or "полных лет назвал" in p:
        return "29"
    if "ваше гражданство" in p:
        return "российское"
    if "каком городе" in p:
        return "москва"
    if "медицинская книжка" in p:
        return "да"
    if "находитесь" in p:
        return "да"
    return "не распознано"


class TestAnswerGrounded:
    def test_stem_match_handles_inflection(self):
        # «российское» должно считаться подтверждённым звучавшим «российская»
        assert da._answer_grounded("российское", "паспорт российская федерация")

    def test_short_answer_needs_exact(self):
        assert da._answer_grounded("да", "ну да конечно")
        assert not da._answer_grounded("нет", "да да есть")

    def test_hallucinated_value_rejected(self):
        # выдуманного в записи нет → не подтверждаем
        assert not da._answer_grounded("двадцать девять", "алло я вас не слышу")


class TestFullVerification:
    def setup_method(self):
        da._llm_ask = lambda base, prompt, num_predict=12: _fake_llm(prompt)

    def _run(self, live):
        return da._recheck_critical_answers("x", SCENARIO, live, TRANSCRIPT)

    def test_citizen_rf_cleaned_with_audit_trail(self):
        """Реальный случай 2026-07-10: гражданство «красивая» → в записи
        «российское». Выверенное — основное, realtime — в скобках, ⚠."""
        corr, nr, notes = self._run({
            "Актуальность": "да", "Возраст": 20,
            "Гражданство": "не распознано: красивая", "Город": "москва", "ЛМК": "да"})
        assert corr["Гражданство"].startswith("российское")
        assert "красивая" in corr["Гражданство"]  # аудит-след сохранён
        assert nr is True

    def test_age_mismatch_recovered(self):
        corr, _, _ = self._run({"Возраст": 20})
        assert "29" in corr["Возраст"] and "20" in corr["Возраст"]

    def test_correct_realtime_field_untouched(self):
        # Город realtime уже верен («москва») → не трогаем, аудит не плодим
        corr, _, _ = self._run({"Город": "москва"})
        assert "Город" not in corr

    def test_unreached_question_not_invented(self):
        # «Опыт» нет в live_answers (звонок не дошёл) → LLM не даём выдумать
        corr, _, _ = self._run({"Актуальность": "да"})
        assert "Опыт" not in corr
        assert "Возраст" not in corr  # тоже не достигнут


class TestAgeGrounded:
    """П4 (2026-07-10): восстановленный возраст берём, только если он реально
    звучит в записи — иначе LLM галлюцинирует (Еладжу поставили 29, хотя в
    записи «вообще четыре года»)."""

    def test_number_words_match(self):
        assert da._age_grounded(29, "мне двадцать девять лет")
        assert da._age_grounded(30, "ну тридцать")
        assert da._age_grounded(24, "двадцать четыре года")

    def test_digit_match(self):
        assert da._age_grounded(45, "мне 45")

    def test_teens(self):
        assert da._age_grounded(19, "девятнадцать")

    def test_not_grounded_rejected(self):
        # реальный случай Еладжа: «29» в записи не звучит
        assert not da._age_grounded(29, "да ибо да вообще четыре года в петербурге")

    def test_age_not_overwritten_when_ungrounded(self):
        """LLM возвращает 29, но в записи его нет → live (24) НЕ подменяем."""
        da._llm_ask = lambda base, prompt, num_predict=12: "29"
        scenario = {"steps": [{"crit": "Возраст", "expect": "age", "bot": "Сколько лет?"}]}
        corr, _, _ = da._recheck_critical_answers(
            "x", scenario, {"Возраст": 24}, "да вообще четыре года в петербурге")
        assert "Возраст" not in corr  # не подменили галлюцинацией
