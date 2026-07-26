"""Качество классификации (26.07): чистка словаря возраста, детерминированная
сверка простых стопов, восстановление возраста регуляркой, детект «просит
перезвонить» у коротких формулировок. Всё — на выводах из данных кампаний
40/41/47 (298 контактов): 76% потерь возраста, 15 ложных ⚠ на стопах.
Сеть/LLM не задействованы — подменяем стабом."""
import sys
from pathlib import Path

VOICECALL_DIR = Path(__file__).resolve().parents[3] / "voicecall"
sys.path.insert(0, str(VOICECALL_DIR))

import dialog  # noqa: E402
import dispatch_agent as da  # noqa: E402


class TestAgeVocabCleanup:
    """Изм.1: из словаря шага «Возраст» убраны 50 слов-согласий. Они не
    используются при разборе (там только parse_age_from_text), но реально
    побеждали числа на живых звонках — в карточках оказывалось «конечно
    можно тринадцать ок думаю», «хорошо», «окей», «договорились»."""

    def setup_method(self):
        self.vocab = dialog.vocab_for_step({"expect": "age"})

    def test_agreement_words_removed(self):
        for w in ["конечно", "можно", "хорошо", "окей", "договорились", "есть",
                   "точно", "спокойно", "разумеется", "ладно", "принято"]:
            assert w not in self.vocab, f"слово-согласие осталось: {w}"

    def test_numbers_kept(self):
        for w in ["восемнадцать", "двадцать", "тридцать", "сорок", "пять", "семь"]:
            assert w in self.vocab, w

    def test_framing_added(self):
        # без «мне» фраза «мне двадцать пять» декодировалась как «НЕ двадцать пять»
        for w in ["мне", "лет", "года", "полных", "около"]:
            assert w in self.vocab, w

    def test_callback_minimum_kept(self):
        # живой, просящий перезвонить на этом шаге, не должен потеряться
        for w in ["перезвоните", "позже"]:
            assert w in self.vocab, w

    def test_vocab_got_smaller(self):
        assert len(self.vocab) < 90, "словарь должен был уменьшиться (было 90)"

    def test_yesno_step_untouched(self):
        # чистые да/нет шаги работали хорошо (6-10% потерь) — их не трогаем
        yn = dialog.vocab_for_step({"expect": "yesno"})
        for w in ["да", "нет", "конечно", "хорошо"]:
            assert w in yn, w


class TestSimpleStopNotFlagged:
    """Изм.6: у простых стопов «...: нет» LLM-сверка давала ложную тревогу
    (15 из 16 в тестах). Если отрицание звучит в записи — стоп подтверждён
    детерминированно, LLM не вызывается."""

    def setup_method(self):
        self.llm_called = False
        def spy(*a, **k):
            self.llm_called = True
            return {"message": {"content": "проверить"}}
        da._rpc = spy

    def test_negation_in_recording_confirms_stop(self):
        nr, note = da._recheck_verdict(
            "x", "stopped", "Актуальность поиска работы: нет",
            "[Дорожка 1] нет не ищу работу спасибо\n[Дорожка 2] находитесь в поиске работы")
        assert nr is False and note is None
        assert not self.llm_called, "LLM звать не нужно — отрицание есть в записи"

    def test_no_negation_still_checks_llm(self):
        # в записи отрицания нет — расхождение реальное, идём в LLM
        nr, note = da._recheck_verdict(
            "x", "stopped", "Актуальность поиска работы: нет",
            "[Дорожка 1] да конечно интересно расскажите\n[Дорожка 2] в поиске работы")
        assert self.llm_called, "должны были обратиться к LLM"
        assert nr is True and note

    def test_complex_stop_still_verified_by_llm(self):
        # стоп не вида «...: нет» (возраст/судимость) — прежняя логика
        da._recheck_verdict("x", "stopped", "Возраст: 60", "[Дорожка 1] шестьдесят")
        assert self.llm_called

    def test_not_stopped_verdict_skipped(self):
        nr, note = da._recheck_verdict("x", "passed", None, "[Дорожка 1] да")
        assert nr is False and note is None


class TestAgeRestoreDeterministic:
    """Изм.4: возраст из записи восстанавливаем СНАЧАЛА парсером числительных
    (малая LLM на мусорных расшифровках справлялась 1 раз из 79)."""

    SCEN = {"steps": [{"crit": "Возраст", "expect": "age", "bot": "Сколько вам полных лет?"}]}

    def test_regex_restores_without_llm(self):
        called = {"n": 0}
        def spy(*a, **k):
            called["n"] += 1
            return "нет"
        da._llm_ask = spy
        # реальная расшифровка из теста 47 (контакт 442)
        tr = "[Дорожка 1] алло на уютно девятнадцать мф москва авто ту работу нету нет"
        corr, nr, notes = da._recheck_critical_answers(
            "x", self.SCEN, {"Возраст": "не распознано: хорошо"}, tr)
        assert "Возраст" in corr and "19" in corr["Возраст"], corr
        assert called["n"] == 0, "парсер должен был справиться без LLM"

    def test_no_age_in_recording_no_invention(self):
        da._llm_ask = lambda *a, **k: "нет"
        tr = "[Дорожка 1] алло да хорошо говорите\n[Дорожка 2] сколько вам полных лет"
        corr, nr, notes = da._recheck_critical_answers(
            "x", self.SCEN, {"Возраст": "не распознано"}, tr)
        assert "Возраст" not in corr


class TestCallbackShortPhrases:
    """Попутный фикс: «перезвоните позже» / «наберите позже» раньше давали
    False (требовался личный маркер вроде «мне»/«занят») — живые терялись.
    Короткая просьба теперь принимается, длинные операторские — нет."""

    def test_natural_short_requests_detected(self):
        for s in ["перезвоните позже", "наберите позже", "перезвоните потом"]:
            assert dialog.is_callback_request(s), s

    def test_existing_personal_forms_still_work(self):
        for s in ["перезвоните мне позже", "я занят перезвоните", "можете перезвонить позже"]:
            assert dialog.is_callback_request(s), s

    def test_operator_announcement_not_callback(self):
        # операторские заглушки содержат «абонент/аппарат/номер/сообщение»
        for s in ["абонент недоступен перезвоните позже",
                   "аппарат абонента выключен перезвоните позднее",
                   "оставьте сообщение или перезвоните позже"]:
            assert not dialog.is_callback_request(s), s

    def test_unrelated_phrases_not_callback(self):
        for s in ["да двадцать пять москва", "нет не ищу работу", "алло слушаю"]:
            assert not dialog.is_callback_request(s), s
