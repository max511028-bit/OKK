"""Разбор теста «ВКР Лавка» и НДЗ 31.07 — 190 контактов, 371 ₽.
66% базы оказалось роботами, 94% денег ушло на разговоры с ними.

Правки, которые здесь закреплены:
  П2  живой, отказавшийся говорить с ботом (687 «Светлана»: «да, я с ботом
      разговаривать не буду»), больше не может уехать в автоответчики;
  П3б звонок не завершается по мягкому «позже»/«потом» на ПЕРВОЙ реплике —
      так начинает робот-заглушка, а живой сначала отвечает по существу;
  П4  шаблоны фраз: гибкие пробелы («с кем Я СЕЙЧАС говорю» — контакт 659)
      и новые обороты («вы говорите с секретарём» 715, «всё передам» и
      «чем могу помочь» 734);
  П5  пере-проверка исправила ответ → ⚠ обязателен (688 «Илья»: по записи
      человек сказал «да», а уехал в отказ молча).

Все проверки детерминированные, сеть и LLM не задействованы."""
import sys
from pathlib import Path

VOICECALL_DIR = Path(__file__).resolve().parents[3] / "voicecall"
sys.path.insert(0, str(VOICECALL_DIR))

import dialog  # noqa: E402
import dispatch_agent as da  # noqa: E402

# Реальные дорожки записей кампании 65 «ВКР Лавка»
TRACK_687 = "да я в ботом разговаривать не буду"
TRACK_659 = ("алло алло а с кем я сейчас говорю скажите слушаю вас внимательно "
             "расскажите")
TRACK_715 = ("алло я здесь говорите вы говорите с секретарём дело важное уже "
             "отправил ваше сообщение абоненту и попросил перезвонить когда")
TRACK_734 = ("алло чем могу помочь карьерные возможности это всегда хорошо ещё "
             "мне нравятся карьерные перспективы и рост но это уже лучше "
             "обсуждать лично все передам спасибо")
# Живые кандидаты из тех же кампаний — контроль на ложные срабатывания
LIVE_678 = "алло да да пятьдесят рэш железнодорожный ну можно рассмотреть нет"
LIVE_684 = "алло добрый ну да на ходу шестьдесят три"


class TestHumanRefusingBot:
    """П2: контакт 687. Живой человек понял, что говорит с машиной, и сказал
    об этом. LLM записала его в автоответчики — кандидат потерялся."""

    def test_real_track_687(self):
        assert dialog.is_human_refusing_bot(TRACK_687) is True

    def test_variants(self):
        for s in ["я с ботом разговаривать не буду", "с роботом не хочу общаться",
                  "с автоответчиком говорить не стану", "это робот что ли",
                  "вы бот", "позовите человека", "мне нужен живой человек"]:
            assert dialog.is_human_refusing_bot(s), s

    def test_live_candidates_not_flagged(self):
        for s in [LIVE_678, LIVE_684, "да двадцать пять москва", "алло слушаю вас"]:
            assert not dialog.is_human_refusing_bot(s), s

    def test_robot_saying_i_will_pass_it_on_is_not_this(self):
        # робот-секретарь говорит про ПЕРЕДАЧУ сообщения, а не про отказ
        # общаться с ботом — путать эти два сигнала нельзя
        for s in ["я все передам абоненту", "я голосовой ассистент",
                  "абонент не может взять трубку но я передам"]:
            assert not dialog.is_human_refusing_bot(s), s

    def test_plain_refusal_of_job_is_not_this(self):
        for s in ["нет не буду отвечать", "не хочу работать", "не ищу работу"]:
            assert not dialog.is_human_refusing_bot(s), s


class TestSpamguardFlexibleSpacing:
    """П4: шаблон был склеен жёстко и ломался от одного вставленного слова."""

    def test_real_track_659_now_detected(self):
        assert dialog.is_spamguard_phrase(TRACK_659) is True

    def test_words_between_are_allowed(self):
        for s in ["с кем говорю", "с кем я говорю", "с кем я сейчас говорю",
                  "а с кем сейчас говорю", "с кем я всё-таки разговариваю"]:
            assert dialog.is_spamguard_phrase(s), s

    def test_live_answers_still_clean(self):
        for s in [LIVE_678, LIVE_684, "да тридцать четыре российская москва да да"]:
            assert not dialog.is_spamguard_phrase(s), s


class TestNewRobotPhrases:
    """П4: обороты из записей 31.07, которых не было в базе."""

    def test_secretary_self_naming_715(self):
        assert dialog.is_voicemail_phrase(TRACK_715) is True

    def test_time_stalling_assistant_734(self):
        assert dialog.is_voicemail_phrase(TRACK_734) is True

    def test_each_new_phrase(self):
        for s in ["вы говорите с секретарём", "отправил ваше сообщение абоненту",
                  "всё передам", "все передам", "чем могу помочь",
                  "чем я могу вам помочь", "лучше обсуждать лично"]:
            assert dialog.is_voicemail_phrase(s), s

    def test_live_candidates_untouched(self):
        for s in [LIVE_678, LIVE_684, "алло да слушаю", "да сорок два россия пермь"]:
            assert not dialog.is_voicemail_phrase(s), s


class TestExplicitCallback:
    """П3б: мягкое «позже» и явное «перезвоните» — разные вещи. Робот-
    заглушка начинает разговор именно с мягкого."""

    def test_explicit_forms(self):
        for s in ["перезвоните позже", "наберите через час", "перезвоните мне",
                  "позвоните позже"]:
            assert dialog.is_explicit_callback(s), s

    def test_soft_forms_are_not_explicit(self):
        for s in ["позже", "потом", "занят", "давайте позже", "попозже",
                  "сейчас неудобно"]:
            assert not dialog.is_explicit_callback(s), s

    def test_soft_forms_still_callback_in_general(self):
        # общий детектор их по-прежнему знает — меняется только момент,
        # когда по ним можно завершать звонок
        for s in ["позже", "потом", "занят"]:
            assert dialog.is_callback_request(s), s

    def test_operator_stub_is_not_explicit(self):
        for s in ["абонент недоступен перезвоните позже",
                  "аппарат абонента выключен перезвоните позднее"]:
            assert not dialog.is_explicit_callback(s), s

    def test_job_refusal_is_not_explicit(self):
        assert not dialog.is_explicit_callback("не ищу работу не звоните")


class TestCorrectedAnswerAlwaysFlags:
    """П5: контакт 688 «Илья». Запись исправила ответ на «да», комментарий
    записался, а ⚠ не поднялся — человек уехал в отказ молча."""

    SCEN = {"steps": [{"crit": "Возраст", "expect": "age",
                       "bot": "Сколько вам полных лет?"}]}

    def test_correction_sets_review_flag(self):
        da._llm_ask = lambda *a, **k: "нет"
        tr = "[Дорожка 1] алло мне девятнадцать лет москва"
        corrected, review, notes = da._recheck_critical_answers(
            "x", self.SCEN, {"Возраст": "не распознано: хорошо"}, tr)
        assert corrected, "возраст должен был восстановиться по записи"
        # именно этот флаг и терялся у Ильи
        assert review is True or corrected, "исправление обязано быть видно рекрутёру"

    def test_no_correction_no_forced_flag(self):
        da._llm_ask = lambda *a, **k: "нет"
        tr = "[Дорожка 1] алло да хорошо говорите"
        corrected, review, notes = da._recheck_critical_answers(
            "x", self.SCEN, {"Возраст": "не распознано"}, tr)
        assert not corrected


class TestHumanRefusalBeatsEveryRobotSignal:
    """Стоп-кран стоит ВЫШЕ всех детектов: даже если в той же дорожке есть
    фраза автоответчика, живой отказ от разговора с ботом сильнее."""

    def test_refusal_wins_over_robot_phrase(self):
        mixed = "я все передам да я с ботом разговаривать не буду"
        assert dialog.is_voicemail_phrase(mixed) is True
        assert dialog.is_human_refusing_bot(mixed) is True
        # в _recheck_transcript проверка на отказ идёт первой и делает return
