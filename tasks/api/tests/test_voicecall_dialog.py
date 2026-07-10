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


class TestAiAssistantAndVoicemail2026_07_10:
    """Пополнение по тесту Яндекс-лавки (2026-07-10): AI-нейросекретари,
    доигрывавшие сценарий до «годен», и автоответчики, которых не хватало."""

    def test_catches_yandex_ai_assistants(self):
        phrases = [
            "по просьбе абонента на звонки отвечаю я я работаю виртуальным помощником",
            "я не просто бот я голосовой ассистент помогаю абоненту не пропускать звонки я передам ему",
            "умный помощник вы можете отправить сообщение я передам по какому вопросу вы звоните",
            "я голосовой помощник могу передать сообщение",
        ]
        for p in phrases:
            assert dialog.is_voicemail_phrase(p), f"должен ловить робота: {p}"

    def test_catches_missed_operator_voicemails(self):
        phrases = [
            "перенаправлен на голосовую вы можете оставить сообщение после звукового сигнала",
            "телефон разряжен или он находится вне зоны действия сети",
            "оставайтесь на линии абонент не берет трубку",
        ]
        for p in phrases:
            assert dialog.is_voicemail_phrase(p), f"должен ловить автоответчик: {p}"

    def test_real_candidates_not_flagged_as_robot(self):
        answers = [
            "да я в поиске работы", "мне двадцать девять лет", "российское гражданство",
            "москва", "да есть медкнижка", "да подходит смены по двенадцать часов",
            "я сейчас работаю но готов рассмотреть предложение", "нет спасибо не интересно",
        ]
        for p in answers:
            assert not dialog.is_voicemail_phrase(p), f"ложное срабатывание на живом: {p}"


class TestRingbackDetection2026_07_10:
    """Голосовой ринг-бэк «идёт дозвон» — не автоответчик и не кандидат;
    исход «не взял трубку», не «не распознали» (тест Яндекс-3, Дмитрий-153)."""

    def test_ringback_detected_and_not_voicemail(self):
        for p in ["продолжаем дозваниваться до абонента пятый",
                   "продолжив дозваниваться оставайтесь на линии",
                   "ожидайте ответа абонента"]:
            assert dialog.is_ringback_phrase(p), f"ринг-бэк не пойман: {p}"
            assert not dialog.is_voicemail_phrase(p), f"ринг-бэк ошибочно = voicemail: {p}"

    def test_real_voicemail_is_not_ringback(self):
        for p in ["оставьте сообщение после звукового сигнала",
                   "абонент занят перезвоните позднее"]:
            assert not dialog.is_ringback_phrase(p)
            assert dialog.is_voicemail_phrase(p)

    def test_real_answers_not_ringback(self):
        for p in ["да удобно", "москва", "мне тридцать лет", "нет не интересно"]:
            assert not dialog.is_ringback_phrase(p)


class TestCallbackRequestDetection:
    """Живой кандидат, просящий перезвонить позже, не должен теряться в
    воронке как "автоответчик" — is_callback_request() проверяется
    ПЕРЕД is_voicemail_phrase() в _run_dialog_loop (2026-07)."""

    def test_catches_personal_callback_requests(self):
        phrases = [
            "нет можешь перезвонить через десять минут",
            "я занят наберите вечером",
            "перезвоните мне попозже",
            "сейчас не могу говорить перезвони",
            "давайте я вам перезвоню через час",
            "занята сейчас перезвоните через минут пять",
            "за рулем сейчас перезвоните завтра",
        ]
        for p in phrases:
            assert dialog.is_callback_request(p), f"должен поймать: {p}"

    def test_does_not_catch_operator_voicemail_phrases(self):
        """Приоритет: операторские заглушки НЕ должны считаться просьбой
        живого человека, даже если формально похожи (та же фраза
        "перезвоните позже")."""
        phrases = [
            "абонент не берет трубку попробуйте перезвонить позднее",
            "аппарат выключен перезвоните позже",
            "телефон выключен пожалуйста перезвоните позже",
            "все каналы связи заняты попробуйте позвонить позже",
        ]
        for p in phrases:
            assert not dialog.is_callback_request(p), f"ложное срабатывание на автоответчик: {p}"

    def test_does_not_catch_unrelated_answers(self):
        answers = ["да удобно", "нет не было", "двадцать девять", "алло да слушаю"]
        for p in answers:
            assert not dialog.is_callback_request(p), f"ложное срабатывание: {p}"

    def test_empty_and_none_are_safe(self):
        assert dialog.is_callback_request("") is False
        assert dialog.is_callback_request(None) is False

    def test_voicemail_and_callback_phrases_do_not_overlap(self):
        """При равных входных данных ровно ОДНА из двух функций должна
        сработать — иначе неоднозначно, какая ветка в _run_dialog_loop
        выиграет (важно, что там callback проверяется первым)."""
        callback_phrases = [
            "нет можешь перезвонить через десять минут",
            "я занят наберите вечером",
            "занята сейчас перезвоните через минут пять",
        ]
        for p in callback_phrases:
            assert dialog.is_callback_request(p) and not dialog.is_voicemail_phrase(p), p

    def test_callback_bye_text_is_prewarmed(self):
        assert dialog.CALLBACK_BYE_TEXT in dialog.all_reask_texts()


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


class TestFillerPhrases:
    """Часть 2 доработок 2026-07 — короткие подтверждения между вопросами
    (опция сценария settings.fillers), см. _run_dialog_loop в
    phone_call.py: играются только перед НОВЫМ вопросом (sess.reasked
    False), не перед повтором/переспросом."""

    def test_filler_phrases_is_nonempty_list_of_strings(self):
        assert isinstance(dialog.FILLER_PHRASES, list)
        assert len(dialog.FILLER_PHRASES) >= 3
        assert all(isinstance(p, str) and p.strip() for p in dialog.FILLER_PHRASES)

    def test_reasked_flag_distinguishes_new_question_from_repeat(self):
        """Ровно то условие, которое phone_call.py проверяет перед тем как
        разрешить филлер: свежий вопрос -> reasked=False, повтор после
        непонятного ответа -> reasked=True."""
        scenario = dialog.load_scenario("tander-sterlitamak-pack")
        sess = dialog.DialogSession(scenario)
        sess.start()
        sess.submit_answer("да удобно")  # нормальный ответ -> новый вопрос
        assert sess.reasked is False
        sess.submit_answer("бессвязный шум")  # unclear -> переспрос
        assert sess.reasked is True
