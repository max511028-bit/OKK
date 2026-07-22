"""Юнит-тесты пере-валидации ответов по записи (voicecall/dispatch_agent.py,
_recheck_critical_answers). Чистая логика — LLM подменяем стабом, сети нет."""
import sys
from pathlib import Path

VOICECALL_DIR = Path(__file__).resolve().parents[3] / "voicecall"
sys.path.insert(0, str(VOICECALL_DIR))

import dispatch_agent as da  # noqa: E402
import dialog  # noqa: E402


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
        da._llm_ask = lambda *a, **k: _fake_llm(a[1] if len(a) > 1 else k.get('prompt', ''))

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
        da._llm_ask = lambda *a, **k: "29"
        scenario = {"steps": [{"crit": "Возраст", "expect": "age", "bot": "Сколько лет?"}]}
        corr, _, _ = da._recheck_critical_answers(
            "x", scenario, {"Возраст": 24}, "да вообще четыре года в петербурге")
        assert "Возраст" not in corr  # не подменили галлюцинацией


class TestQuestionAskedInRecording:
    """У4 (16.07): вопрос считается достигнутым и если он ЗВУЧАЛ в записи
    (дорожка бота) — спасает живых, оборвавшихся по «3 без ответа» на первом
    шаге (Ахмединмухтор: «да ну двадцать шесть» в записи, возраст терялся)."""

    def setup_method(self):
        self.scenario = {"steps": [
            {"crit": "Шаг 1", "expect": "yesno", "bot": "находитесь сейчас в поиске работы?"},
            {"crit": "Возраст", "expect": "age", "bot": "Сколько вам полных лет?"},
        ]}

    def test_age_recovered_when_question_heard_in_recording(self):
        da._llm_ask = lambda *a, **k: "26"
        transcript = ("[Дорожка 1] да ну двадцать шесть\n"
                      "[Дорожка 2] находитесь сейчас в поиске работы сколько вам полных лет")
        live = {"Шаг 1": "не распознано"}  # возраст в live отсутствует — обрыв раньше
        corr, nr, _ = da._recheck_critical_answers("x", self.scenario, live, transcript)
        assert "Возраст" in corr and "26" in corr["Возраст"]
        assert nr is True

    def test_no_recovery_when_question_not_heard(self):
        """Вопрос возраста НЕ звучал → не выдумываем (защита Ахмата 09.07)."""
        da._llm_ask = lambda *a, **k: "26"
        transcript = ("[Дорожка 1] двадцать шесть чего то там\n"
                      "[Дорожка 2] находитесь сейчас в поиске работы")
        corr, _, _ = da._recheck_critical_answers("x", self.scenario, {"Шаг 1": "нет"}, transcript)
        assert "Возраст" not in corr

    def test_grounding_still_blocks_hallucination(self):
        """Вопрос звучал, но названного LLM числа в записи нет → не берём."""
        da._llm_ask = lambda *a, **k: "29"
        transcript = ("[Дорожка 1] алло алло\n"
                      "[Дорожка 2] сколько вам полных лет")
        corr, _, _ = da._recheck_critical_answers("x", self.scenario, {}, transcript)
        assert "Возраст" not in corr


class TestReviewSummary:
    """У6 (16.07): LLM-саммари записи для ⚠-контактов."""

    def test_summary_returned_and_bounded(self):
        da._llm_ask = lambda *a, **k: "живой кандидат, 26 лет, москва " * 30
        # с 21.07 транскрипт должен быть ≥15 слов, а число 26 — реально
        # звучать («двадцать шесть»), иначе саммари отбрасывается (grounding)
        tr = ("[Дорожка 1] да здравствуйте мне двадцать шесть лет живу в москве "
              "работу ищу да готов выйти в ближайшее время спасибо")
        s = da._llm_summary_for_review("x", tr)
        assert s and len(s) <= 300

    def test_empty_transcript_no_summary(self):
        da._llm_ask = lambda *a, **k: "что-то"
        assert da._llm_summary_for_review("x", "   ") == ""


class TestSummaryGrounding2107:
    """Тест 21.07: саммари галлюцинировало «возраст — 25 лет, город —
    москва, согласие — да» для кандидата, который не разговаривал.
    Защита: короткий транскрипт → без саммари; число не из записи →
    саммари отбрасывается."""

    LONG_TR = ("[Дорожка 1] сейчас вам не могу ответить записала ваш ответ для "
               "абонента пока что он занят другим звонком а вы по срочному вопросу\n"
               "[Дорожка 2] добрый день максим звоню вам по поводу работы")

    def test_short_transcript_no_summary(self):
        da._llm_ask = lambda base, prompt, num_predict=12: "кандидат отвечал живо, 25 лет"
        assert da._llm_summary_for_review("x", "(запись не получена от Novofon)") == ""

    def test_hallucinated_number_dropped(self):
        # LLM сочинила «25 лет» — в записи числа нет → саммари в мусор
        da._llm_ask = lambda base, prompt, num_predict=12: "живой кандидат, 25 лет, москва, согласие — да"
        assert da._llm_summary_for_review("x", self.LONG_TR) == ""

    def test_grounded_number_kept(self):
        tr = self.LONG_TR + "\nмне двадцать пять лет"
        da._llm_ask = lambda base, prompt, num_predict=12: "живой кандидат, 25 лет"
        assert "25" in da._llm_summary_for_review("x", tr)

    def test_no_numbers_kept(self):
        da._llm_ask = lambda base, prompt, num_predict=12: "отвечал робот-секретарь, данных мало"
        assert "робот" in da._llm_summary_for_review("x", self.LONG_TR)


class TestOwnerAssistantPhrases2107:
    """Регресс с реальной записи 21.07 (session_394102934): Яндекс-ассистент
    владельца прошёл как живой «стоп». Теперь фразы должны ловить."""

    REAL = ("сейчас вам не мог фар необходимо ли вам перезвонить записала ваш "
            "ответ для абонента пока что он занят другим звонком а вы по срочному вопросу")

    def test_real_transcript_caught(self):
        assert dialog.is_voicemail_phrase(self.REAL)

    def test_fragments_caught(self):
        for p in ["записала ваш ответ для абонента", "записал ваш ответ",
                   "пока что он занят другим звонком", "необходимо ли вам перезвонить"]:
            assert dialog.is_voicemail_phrase(p), p

    def test_live_answers_still_clean(self):
        # «перезвоните позже» сюда не берём: это давний паттерн голосовой
        # почты, а живого с той же фразой первым ловит is_callback_request.
        for p in ["да записала адрес спасибо", "нет не ищу работу",
                   "да мне двадцать пять", "я занят сейчас на смене говорите быстрее"]:
            assert not dialog.is_voicemail_phrase(p), p


class TestMimicRobotAgeSignal:
    """Анти-мимикрия (17.07): «годен» без названного возраста → ⚠.
    Детерминированно, на реальных расшифровках теста 17.07."""

    SCEN = {"steps": [{"expect": "yesno", "bot": "ищете работу?"},
                      {"expect": "age", "bot": "сколько вам лет?"},
                      {"expect": "free", "bot": "город?"}]}

    def test_stated_age_various_forms(self):
        for t in ["да тридцать семь лет москва", "пятьдесят шесть россия",
                   "двадцать два", "восемнадцать люберцы", "мне 45 лет"]:
            assert da._candidate_stated_age(t), t

    def test_no_age_evasive_robot(self):
        for t in ["алло ник может взять трубку кстати спасибо за предложение",
                   "представьтесь пожалуйста слушаю говорите говорите",
                   "я голосовать могу помочь если хотите ещё что-то сообщить"]:
            assert not da._candidate_stated_age(t), t

    def test_passed_no_age_flags_review(self):
        # мимикрирующий робот: годен, но возраст не назван → True (пометить)
        assert da._passed_but_no_age(
            self.SCEN, "passed",
            ["алло да говорите кто звонит откуда у вас мой номер представьтесь"])

    def test_passed_with_age_not_flagged(self):
        # живой: годен и назвал возраст → False (не трогаем)
        assert not da._passed_but_no_age(
            self.SCEN, "passed", ["да тридцать семь белорус москва да"])

    def test_not_passed_never_flagged(self):
        # только для «годен»: стоп/оборвался не наша забота
        assert not da._passed_but_no_age(self.SCEN, "stopped", ["не ищу работу"])

    def test_no_age_step_in_scenario_skips(self):
        scen = {"steps": [{"expect": "yesno", "bot": "ищете работу?"}]}
        assert not da._passed_but_no_age(scen, "passed", ["да говорите кто это"])

    def test_empty_candidate_track_skips(self):
        # кандидат толком не говорил — это не про мимикрию
        assert not da._passed_but_no_age(self.SCEN, "passed", ["(тишина/не распознано)"])


class TestRobotSecretaryClassifier:
    """П2 (2026-07-10): LLM-классификация робота-секретаря по записи там,
    где точные фразы бьют мимо (искажения STT / новые формулировки)."""

    def test_robot_verdict(self):
        da._llm_ask = lambda *a, **k: "робот"
        assert da._llm_is_robot_secretary("x", "[Дорожка 1] я секретарь передам сообщение")

    def test_human_verdict(self):
        da._llm_ask = lambda *a, **k: "человек"
        assert not da._llm_is_robot_secretary("x", "[Дорожка 1] да мне тридцать лет москва")

    def test_ambiguous_defaults_to_human(self):
        # безопасность: неясный ответ LLM НЕ должен стоить живого кандидата
        da._llm_ask = lambda *a, **k: "не знаю"
        assert not da._llm_is_robot_secretary("x", "[Дорожка 1] что-то")

    def test_empty_transcript_is_human(self):
        da._llm_ask = lambda *a, **k: "робот"
        assert not da._llm_is_robot_secretary("x", "   ")
