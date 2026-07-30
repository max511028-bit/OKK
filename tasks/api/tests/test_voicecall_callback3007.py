"""Ложные отказы и робот-спам-защита (30.07). Разбор контакта 525
«Виталий»: реалтайм услышал одно слово «потом» → оно стояло в списке
отрицаний → вердикт «Актуальность поиска работы: нет». По записи это
оказался робот-защита от спама («уточните, с кем говорю... давайте
позже»). Ни фразы автоответчиков, ни LLM его не узнали (замер на
реальной дорожке: «человек» 3/3).

Скан 86 контактов кампаний 55-64: 8 отказов, 2 из них поставлены на
просьбе перезвонить (525 «потом», 549 «позже») — каждый четвёртый.
Сеть/LLM не задействованы: всё детерминированно."""
import sys
from pathlib import Path

VOICECALL_DIR = Path(__file__).resolve().parents[3] / "voicecall"
sys.path.insert(0, str(VOICECALL_DIR))

import dialog  # noqa: E402
import dispatch_agent as da  # noqa: E402

YESNO = {"id": "aktual", "crit": "Актуальность поиска работы",
         "expect": "yesno", "stop_if": "нет"}

# Реальная дорожка кандидата контакта 525 (большая Vosk-модель по записи)
TRACK_525 = ("да да слушаю вас а уточните пожалуйста с кем говорю "
             "давайте позже кто же")


class TestCallbackNotARefusal:
    """Изм.1-2: просьба перезвонить перестала быть отказом. Раньше
    «потом»/«позже»/«занят» стояли в _NO и давали ложный стоп-фактор,
    а «давайте позже» — ложное ДА (слово «давайте» в _YES)."""

    def test_real_words_from_contacts_525_549(self):
        for w in ["потом", "позже"]:
            assert dialog.is_callback_request(w), w
            assert dialog.interpret(YESNO, w)["val"] != "no", w

    def test_short_availability_forms_detected(self):
        for s in ["попозже", "занят", "я занят", "сейчас неудобно",
                  "за рулем", "за рулём", "в дороге", "на совещании"]:
            assert dialog.is_callback_request(s), s

    def test_davayte_pozzhe_no_longer_reads_as_yes(self):
        # худший случай: кандидат откладывает разговор, а мы пишем «ищет работу»
        assert dialog.interpret(YESNO, "давайте позже")["val"] != "yes"
        assert dialog.is_callback_request("давайте позже")

    def test_existing_long_forms_still_work(self):
        for s in ["перезвоните позже", "перезвоните мне через час",
                  "наберите позже", "я занят перезвоните"]:
            assert dialog.is_callback_request(s), s


class TestRealRefusalsSurvive:
    """Обратная сторона: настоящий отказ обязан остаться отказом."""

    def test_plain_negations_still_no(self):
        for s in ["нет", "нет спасибо", "нет не интересно", "не подходит",
                  "отказываюсь"]:
            assert dialog.interpret(YESNO, s)["val"] == "no", s
            assert not dialog.is_callback_request(s), s

    def test_ne_seychas_deliberately_left_a_refusal(self):
        # «не сейчас» в ответ на «находитесь в поиске работы?» — это «нет»,
        # а не просьба перезвонить. Сознательное решение, см. коммент у _NO.
        assert dialog.interpret(YESNO, "не сейчас")["val"] == "no"
        assert not dialog.is_callback_request("не сейчас")

    def test_refusal_marker_beats_callback_root(self):
        # «позже» рядом с прямым отказом от вакансии ничего не меняет
        for s in ["не надо мне звоните позже не ищу",
                  "не интересно перезвоните не надо",
                  "мне не нужно больше не звоните"]:
            assert not dialog.is_callback_request(s), s

    def test_agreement_untouched(self):
        for s in ["да", "да конечно", "ага", "хорошо", "давайте", "готов"]:
            assert dialog.interpret(YESNO, s)["val"] == "yes", s


class TestOperatorStubsNotCallback:
    """Заглушки сети говорят «перезвоните позже» теми же словами —
    операторский маркер (абонент/аппарат/сообщение) обязан их отсекать."""

    def test_stubs_are_voicemail_not_callback(self):
        for s in ["абонент временно недоступен перезвоните позже",
                  "аппарат абонента выключен перезвоните позднее",
                  "оставьте сообщение или перезвоните позже",
                  "абонент занят другим звонком"]:
            assert not dialog.is_callback_request(s), s
            assert dialog.is_voicemail_phrase(s), s


class TestSpamGuardPhrases:
    """Изм.3: робот-защита от спама (Тинькофф/МТС/Мегафон) переспрашивает
    звонящего вместо ответа. Фраза сама по себе неоднозначна — живой тоже
    может спросить «а с кем я говорю?», поэтому решение по совокупности."""

    def test_detected_on_real_track_525(self):
        assert dialog.is_spamguard_phrase(TRACK_525)

    def test_typical_spamguard_openers(self):
        for s in ["уточните пожалуйста с кем говорю", "представьтесь пожалуйста",
                  "назовите цель звонка", "по какому вопросу вы звоните",
                  "кто звонит", "с кем я разговариваю"]:
            assert dialog.is_spamguard_phrase(s), s

    def test_normal_candidate_answers_are_not_spamguard(self):
        for s in ["да нахожусь двадцать девять российская казань",
                  "алло да слушаю", "нет не ищу работу спасибо",
                  "мне тридцать лет пермь"]:
            assert not dialog.is_spamguard_phrase(s), s

    def test_not_added_to_voicemail_pattern(self):
        # критично: _VOICEMAIL_PATTERN переклассифицирует НЕМЕДЛЕННО и при
        # любом вердикте — живой с вопросом «с кем говорю» туда попасть не должен
        assert not dialog.is_voicemail_phrase("а с кем я говорю")
        assert not dialog.is_voicemail_phrase("представьтесь пожалуйста")


class TestSpamGuardDecidedByPersonalData:
    """Стоп-кран: переклассифицируем в робота, только если собеседник не
    сообщил о себе НИЧЕГО конкретного. Зеркало защиты по возрасту (0в),
    которая 27.07 спасла живого кандидата 467."""

    def _is_robot(self, track):
        return (dialog.is_spamguard_phrase(track)
                and not da._candidate_gave_personal_data(track))

    def test_track_525_classified_as_robot(self):
        assert da._candidate_gave_personal_data(TRACK_525) is False
        assert self._is_robot(TRACK_525) is True

    def test_live_candidate_with_same_question_is_spared(self):
        live = ("да нахожусь двадцать девять российская казань да подходит "
                "а с кем я говорю")
        assert da._candidate_gave_personal_data(live) is True
        assert self._is_robot(live) is False

    def test_citizenship_alone_counts_as_personal_data(self):
        assert da._candidate_gave_personal_data("а с кем я говорю российское гражданство")
        assert da._candidate_gave_personal_data("узбекистан у меня патент")

    def test_medbook_counts_as_personal_data(self):
        assert da._candidate_gave_personal_data("медкнижка есть а вы кто")

    def test_robot_secretary_track_has_no_personal_data(self):
        robot = ("какие контактные данные могу передать абоненту говорите "
                 "пожалуйста а я все запишу")
        assert da._candidate_gave_personal_data(robot) is False
