"""Детект робота-секретаря, вторая серия (30.07). Разбор контакта 564
«Мария»: AI-секретарь 1м43с вежливо тянул время — «расскажите побольше»,
«я бы ещё подумал», «паузу возьму чтоб подумать», «подъеду к вам лично»,
«откуда у вас эта информация». Ни одного ответа по существу, но «да» на
первый вопрос дало вердикт ГОДЕН.

Что не сработало:
  • механизм уклончивости — 0 срабатываний за весь разговор (список
    оборотов был буквальным: «нужно подумать» есть, «я бы ещё подумал» нет);
  • LLM-проверка — при ручном прогоне на той же дорожке даёт «робот» 3/3,
    а в бою вернула «человек» (один опрос = монетка на пограничных);
  • сбой опроса LLM молча читался как «человек» (в логе кампаний 20
    ответов Bad Gateway и 14 таймаутов).

Замер на 18 реальных дорожках: LLM назвала роботом 6 живых кандидатов,
назвавших возраст — их спасает ТОЛЬКО детерминированный стоп-кран.
Отсюда правило: LLM никогда не решает в одиночку. Сеть не задействована."""
import sys
from pathlib import Path

VOICECALL_DIR = Path(__file__).resolve().parents[3] / "voicecall"
sys.path.insert(0, str(VOICECALL_DIR))

import dialog  # noqa: E402
import dispatch_agent as da  # noqa: E402

# Реальная дорожка кандидата контакта 564 (большая Vosk-модель по записи)
TRACK_564 = ("их толио видя кто это год не узнать а расскажите пожалуйста побольше "
             "пока не до конца понятно интересно но я бы ещё подумал давайте я паузу "
             "возьму чтоб подумать давайте я подъеду к вам лично поговорим так проще "
             "будет повторите пожалуйста скажите откуда у вас эта информация але вы "
             "пропали повторите пожалуй")
# Живой кандидат (контакт 467) — на нём LLM ошибалась и 27.07, и сейчас
TRACK_LIVE = ("он а гидом а да нахожусь двадцать девять российская казань да "
              "подходит надо посмотреть возможно есть")


class TestEvasiveMarkersExtended:
    """Правка 3: обороты, на которых механизм молчал у Марии."""

    def test_marias_turns_now_detected(self):
        step = {"expect": "yesno"}
        for s in ["давайте я паузу возьму чтобы подумать",
                  "давайте я подъеду к вам лично поговорим так проще будет",
                  "расскажите пожалуйста побольше",
                  "откуда у вас эта информация",
                  "я бы ещё подумал",
                  "пока не до конца понятно"]:
            assert dialog.answer_is_evasive(step, s), s

    def test_old_markers_still_work(self):
        step = {"expect": "yesno"}
        for s in ["а вы представьтесь пожалуйста", "скажите кто звонит мне",
                  "мне нужно подумать немного", "я затрудняюсь ответить сейчас",
                  "могу передать сообщение ему"]:
            assert dialog.answer_is_evasive(step, s), s

    def test_min_three_words_guard_kept(self):
        # прежнее сознательное ограничение: по одному-двум словам не судим,
        # иначе живое «кто это?» на незнакомый номер станет уликой
        step = {"expect": "yesno"}
        assert not dialog.answer_is_evasive(step, "представьтесь")
        assert not dialog.answer_is_evasive(step, "кто звонит")
        # на шаге возраста ограничения нет — там живой называет число
        assert dialog.answer_is_evasive({"expect": "age"}, "представьтесь")

    def test_connection_complaints_are_not_evasive(self):
        # СОЗНАТЕЛЬНО не считаем уклончивостью: живой на шумной линии
        # повторяет это постоянно, а правило «3 маркера → робот» ниже
        # начало бы бить по живым
        step = {"expect": "yesno"}
        for s in ["повторите пожалуйста", "але вы пропали", "не слышно ничего"]:
            assert not dialog.answer_is_evasive(step, s), s

    def test_plain_answers_not_evasive(self):
        step = {"expect": "yesno"}
        for s in ["да", "нет", "двадцать девять", "москва"]:
            assert not dialog.answer_is_evasive(step, s), s


class TestCountEvasiveMarkers:
    """Правка 4: считаем РАЗНЫЕ обороты — робот повторяет одну фразу по
    кругу, и десять «представьтесь» это всё ещё один признак."""

    def test_marias_track_over_threshold(self):
        n = dialog.count_evasive_markers(TRACK_564)
        assert n >= dialog.EVASIVE_ROBOT_MIN, f"насчитали всего {n}"

    def test_live_candidate_has_none(self):
        assert dialog.count_evasive_markers(TRACK_LIVE) == 0

    def test_repeats_count_once(self):
        one = dialog.count_evasive_markers("представьтесь")
        many = dialog.count_evasive_markers("представьтесь представьтесь представьтесь")
        assert one == many == 1

    def test_noisy_line_scores_zero(self):
        noisy = "алло да повторите пожалуйста вы пропали не слышно ничего повторите але"
        assert dialog.count_evasive_markers(noisy) == 0

    def test_empty(self):
        assert dialog.count_evasive_markers("") == 0
        assert dialog.count_evasive_markers(None) == 0


class TestBehaviourRuleNeedsNoPersonalData:
    """Решение по совокупности: уклончивость → робот, только если о себе
    не сказано НИЧЕГО. Тот же принцип, что у спам-защиты."""

    def _is_robot(self, track):
        return ((dialog.is_spamguard_phrase(track)
                 or dialog.count_evasive_markers(track) >= dialog.EVASIVE_ROBOT_MIN)
                and not da._candidate_gave_personal_data(track))

    def test_maria_classified_robot(self):
        assert self._is_robot(TRACK_564) is True

    def test_live_candidate_untouched(self):
        assert self._is_robot(TRACK_LIVE) is False

    def test_evasive_but_named_age_is_spared(self):
        # уклонялся, но возраст назвал — переклассификации быть не должно
        mixed = ("мне тридцать два расскажите пожалуйста побольше я бы ещё подумал "
                 "давайте я паузу возьму чтоб подумать")
        assert dialog.count_evasive_markers(mixed) >= dialog.EVASIVE_ROBOT_MIN
        assert da._candidate_gave_personal_data(mixed) is True
        assert self._is_robot(mixed) is False


class TestRobotVerdictVoting:
    """Правка 1: голосование вместо одного опроса. Замер на 18 реальных
    дорожках: 1 голос и 3 голоса дали одинаковые 7 переклассификаций —
    голосование не стало агрессивнее, только устойчивее."""

    LONG = "а расскажите побольше кто это откуда у вас эта информация я бы подумал ещё"

    def test_majority_robot(self):
        da._llm_ask = lambda *a, **k: "робот"
        assert da._llm_robot_verdict("x", self.LONG, votes=3) == (True, True)

    def test_majority_human(self):
        da._llm_ask = lambda *a, **k: "человек"
        assert da._llm_robot_verdict("x", self.LONG, votes=3) == (False, True)

    def test_split_vote_goes_to_majority(self):
        seq = iter(["робот", "человек", "робот"])
        da._llm_ask = lambda *a, **k: next(seq)
        assert da._llm_robot_verdict("x", self.LONG, votes=3) == (True, True)

    def test_minority_robot_loses(self):
        seq = iter(["робот", "человек", "человек"])
        da._llm_ask = lambda *a, **k: next(seq)
        assert da._llm_robot_verdict("x", self.LONG, votes=3) == (False, True)


class TestLLMFailureIsNotHuman:
    """Правка 2: «модель молчит» ≠ «модель сказала человек». Раньше любая
    ошибка тихо давала «годен» — а это 20 Bad Gateway и 14 таймаутов
    в логе кампаний."""

    LONG = "а расскажите побольше кто это откуда у вас эта информация я бы подумал ещё"

    def test_all_calls_failed_reports_not_checked(self):
        da._llm_ask = lambda *a, **k: ""
        is_robot, checked = da._llm_robot_verdict("x", self.LONG, votes=3)
        assert is_robot is False and checked is False

    def test_partial_failure_still_counts_real_votes(self):
        seq = iter(["", "робот", ""])
        da._llm_ask = lambda *a, **k: next(seq)
        assert da._llm_robot_verdict("x", self.LONG, votes=3) == (True, True)

    def test_too_short_is_a_verdict_not_a_failure(self):
        # «алло» — судить не о чем, но это ОТВЕТ, а не сбой: ⚠ не нужен
        da._llm_ask = lambda *a, **k: "робот"
        assert da._llm_robot_verdict("x", "алло") == (False, True)
        assert da._llm_robot_verdict("x", "   ") == (False, True)

    def test_old_bool_api_preserved(self):
        # _llm_is_robot_secretary остаётся bool — на неё завязаны прежние тесты
        da._llm_ask = lambda *a, **k: "робот"
        assert da._llm_is_robot_secretary("x", self.LONG) is True
        assert da._llm_is_robot_secretary("x", "алло") is False


class TestWhenToVoteThreeTimes:
    """Три опроса не всегда — только при косвенном подозрении, чтобы не
    утраивать нагрузку на локальную модель во время кампании."""

    def test_no_age_is_suspicious(self):
        assert da._robot_check_is_suspicious({}, "да да слушаю вас говорите") is True

    def test_unrecognized_answer_is_suspicious(self):
        answers = {"Возраст": "не распознано: потом", "Город": "москва"}
        assert da._robot_check_is_suspicious(answers, "мне тридцать москва") is True

    def test_clean_call_with_age_is_not_suspicious(self):
        answers = {"Возраст": 30, "Город": "москва", "Гражданство": "РФ"}
        assert da._robot_check_is_suspicious(answers, "да тридцать российская москва") is False
