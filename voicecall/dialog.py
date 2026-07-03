"""Dialog engine — мозги бота.

Полностью отвязан от телефонии. Запускается как CLI для теста:
    python voicecall/dialog.py [scenario_id]

Бот пишет вопросы в stdout, ты отвечаешь в stdin, в конце получаешь вердикт.
Когда подключим SIP — stdout будет уходить в TTS → трубку, stdin будет
приходить из STT.
"""
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
from urllib.parse import quote

import urllib.request as _urllib

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
DEFAULT_SCENARIO = "tander-sterlitamak-pack"
PORTAL_BASE = os.getenv("PORTAL_BASE", "https://portalsth.ru/tasks/api")

# ── Парсеры ответов кандидата ────────────────────────────────────────────

_YES = re.compile(
    r"(^|\s)(да|ага|угу|конечно|разумеется|естественно|готов|готова|можно|удобно|"
    r"слушаю|говорите|давайте|давай|ок|окей|хорошо|норм|подходит|пойдет|идет|"
    r"согласен|согласна|есть|имеется|имею|верно|так точно|ясно|без проблем|"
    r"точно|именно|несомненно|обязательно|легко|спокойно|заметано|договорились|"
    r"лады|ладно|добро|принято|ясное дело|почему бы нет|не вопрос|всяко да|"
    r"ага угу|угу угу)(\s|$)"
)
_NO = re.compile(
    r"(^|\s)(нет|неа|не-а|не готов|не могу|не хочу|не буду|не подходит|нету|"
    r"отказ|против|неудобно|занят|занята|перезвоните|позже|потом|не сейчас|"
    r"не думаю|вряд ли|сомневаюсь|ни в коем случае|исключено|нет уж|не в этот раз|"
    r"не выйдет|не сложится|не смогу|не в силах|откажусь|отказываюсь|не судьба)(\s|$)"
)


def _norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[.,!?]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Карта русских числительных 0-99
_NUM_WORDS = {
    "ноль": 0,
    "один": 1, "одна": 1, "одно": 1,
    "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20, "тридцать": 30, "сорок": 40,
    "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70,
    "восемьдесят": 80, "девяносто": 90, "сто": 100,
    # Склонённые формы («мне около тридцати», «уже за сорок») — Vosk с
    # ограниченным словарём их не выдаст (там только именительный падеж
    # в _NUMS), но открытая диктовка (fallback без vocab) иногда может.
    "двадцати": 20, "тридцати": 30, "сорока": 40,
    "пятидесяти": 50, "шестидесяти": 60, "семидесяти": 70,
    "восьмидесяти": 80,
}

# Правдоподобные границы возраста человека вообще (не границы конкретной
# вакансии) — значения за пределами считаем вероятной ошибкой
# распознавания речи и переспрашиваем, а не отклоняем кандидата.
AGE_PLAUSIBLE_MIN = 14
AGE_PLAUSIBLE_MAX = 90


def parse_age_from_text(raw: str) -> Optional[int]:
    """Извлекает возраст из текста. Сначала ищет цифры, потом слова.
    «двадцать девять» → 29, «27 лет» → 27, «мне сорок» → 40.
    Возвращает None если не нашёл."""
    if not raw:
        return None
    # 1) Цифры
    m = re.search(r"\d{1,3}", raw)
    if m:
        return int(m.group(0))
    # 2) Слова
    words = _norm(raw).split()
    i = 0
    while i < len(words):
        w = words[i]
        if w not in _NUM_WORDS:
            i += 1
            continue
        val = _NUM_WORDS[w]
        # десятки + единицы: «двадцать девять» = 29. Единицы обычно идут
        # сразу следующим словом, но Vosk иногда вставляет короткий филлер
        # («двадцать мне девять», паузы речи распознаются как лишнее
        # слово) — проверяем следующее слово, а если это не число, ещё
        # одно через него, прежде чем сдаться.
        if val in (20, 30, 40, 50, 60, 70, 80, 90):
            for j in (i + 1, i + 2):
                if j >= len(words):
                    break
                next_w = words[j]
                if next_w in _NUM_WORDS:
                    next_val = _NUM_WORDS[next_w]
                    if 1 <= next_val <= 9:
                        val += next_val
                    break
        return val
    return None


def interpret(step: dict, raw: str) -> dict:
    """Regex-парсер ответа кандидата. Возвращает {val, stop?}.
    val == "unclear" если не понял — тогда вызывают LLM fallback."""
    t = " " + _norm(raw) + " "
    expect = step.get("expect")

    if expect == "yesno":
        # Умная логика: если есть и да, и нет — берём ПЕРВУЮ позицию,
        # потому что обычно люди говорят основной ответ в начале, потом
        # добавляют филлеры/самокоррекцию. Vosk часто добавляет «нет»
        # после «да готов» из шумовой части — игнорим.
        m_yes = _YES.search(t)
        m_no = _NO.search(t)
        if m_yes and not m_no:
            return {"val": "yes"}
        if m_no and not m_yes:
            return {"val": "no"}
        if m_yes and m_no:
            # Сравниваем позиции — кто раньше, тот и победил
            if m_yes.start() < m_no.start():
                return {"val": "yes"}
            else:
                return {"val": "no"}
        return {"val": "unclear"}

    if expect == "gender_male":
        if re.search(r"(^|\s)(жен|женщ|девушк|дама)", t):
            return {"val": "женский", "stop": True}
        if re.search(r"(^|\s)(муж|мужч|мужик|парень|пацан|да)", t):
            return {"val": "мужской"}
        if _NO.search(t):
            return {"val": "женский", "stop": True}
        if _YES.search(t):
            return {"val": "мужской"}
        return {"val": "unclear"}

    if expect == "age":
        a = parse_age_from_text(raw)
        if a is None:
            return {"val": "unclear"}
        # Правдоподобность возраста ≠ границы вакансии (step min/max).
        # Vosk на короткой grammar-модели иногда теряет первую часть
        # составного числа («двадцать девять» → «девять») — такое
        # значение неправдоподобно как реальный возраст человека, скорее
        # всего ошибка распознавания. Переспрашиваем вместо того чтобы
        # сразу отклонять кандидата по некорректно расслышанному числу.
        if a < AGE_PLAUSIBLE_MIN or a > AGE_PLAUSIBLE_MAX:
            return {"val": "unclear"}
        if a < step.get("min", 18) or a > step.get("max", 45):
            return {"val": a, "stop": True}
        return {"val": a}

    if expect == "citizen_rf":
        if re.search(r"(рф|россия|российск|россиян|русск|местн)", t):
            return {"val": "РФ"}
        if re.search(r"(узбек|таджик|киргиз|кыргыз|казах|армен|азербайдж|грузин|"
                     r"украин|белорус|молдав|туркмен|иностран|патент|внж|рвп)", t):
            return {"val": "не РФ", "stop": True}
        if _YES.search(t):
            return {"val": "РФ"}
        return {"val": "unclear"}

    if expect == "crime":
        if re.search(r"(158|228|105|кража|воровств|наркот|убийств)", t):
            return {"val": "стоп-статья", "stop": True}
        if re.search(r"(нет|не было|не судим|чист|никогда|обижаете)", t):
            return {"val": "нет"}
        if re.search(r"(да|была|был|судим|статья|срок|сидел|условн)", t):
            return {"val": "другая статья"}
        return {"val": "unclear"}

    if expect == "shifts":
        # Распознаём вариант смен БЕЗ решения о стопе — стоп вычисляется
        # в submit_answer по списку step["accepted"] (настраивается в конструкторе).
        # Возможные значения: "только день" | "только ночь" | "день+ночь"
        only_day = re.search(r"(только день|только днем|только дневн|днем могу|лишь день)", t)
        no_night = re.search(r"(ноч.{0,6}(не|нельзя|никак)|не.{0,4}ноч|без ноч)", t)
        only_night = re.search(r"(только ноч|только в ноч|лишь ноч|днем не могу|днем нельзя)", t)
        both = re.search(r"(ноч|любые|оба|обе|без разницы|все равно|хоть когда)", t)
        if only_night:
            return {"val": "только ночь"}
        if only_day or no_night:
            return {"val": "только день"}
        if both:
            return {"val": "день+ночь"}
        m_yes = _YES.search(t)
        m_no = _NO.search(t)
        if m_yes and not m_no:
            return {"val": "день+ночь"}
        if m_no and not m_yes:
            return {"val": "только день"}
        if m_yes and m_no:
            return {"val": "день+ночь" if m_yes.start() < m_no.start() else "только день"}
        return {"val": "unclear"}

    if expect == "free":
        return {"val": raw.strip()}

    return {"val": "unclear"}


def llm_classify(step: dict, raw: str) -> Optional[dict]:
    """Fallback к Qwen — если regex запутался. Возвращает {val, stop?} или None."""
    expect = step.get("expect")
    labels_map = {
        "yesno": ["yes", "no", "unclear"],
        "gender_male": ["мужской", "женский", "unclear"],
        "citizen_rf": ["РФ", "не РФ", "unclear"],
        "crime": ["нет", "другая статья", "стоп-статья", "unclear"],
        "shifts": ["день+ночь", "только день", "unclear"],
    }
    labels = labels_map.get(expect)
    if not labels:
        return None
    try:
        payload = json.dumps(
            {"text": raw, "labels": labels, "question": step.get("bot", "")},
            ensure_ascii=False,
        ).encode("utf-8")
        req = _urllib.Request(
            PORTAL_BASE + "/validator/llm-classify",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urllib.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        lab = data.get("label")
        if not lab or lab == "unclear":
            return None
        # Маппинг LLM-метки в формат interpret()
        if expect == "yesno":
            return {"val": lab}
        if expect == "gender_male":
            return {"val": lab, "stop": lab == "женский"}
        if expect == "citizen_rf":
            return {"val": lab, "stop": lab == "не РФ"}
        if expect == "crime":
            return {"val": lab, "stop": lab == "стоп-статья"}
        if expect == "shifts":
            return {"val": lab, "stop": lab == "только день"}
        return {"val": lab}
    except Exception as e:
        print(f"[llm_classify] ошибка: {type(e).__name__}: {e}", file=sys.stderr)
        return None


_VOICEMAIL_PATTERN = re.compile(
    r"(автоответчик|включен автоответчик|"
    r"оставьте сообщение|оставьте своё сообщение|после сигнала|после гудка|"
    r"недоступен|временно недоступен|вне зоны действия сети|"
    r"абонент не отвечает|абонент не берет трубку|абонент не берёт трубку|"
    r"не берет трубку|не берёт трубку|"
    r"перезвоните\s+\S*\s*позже|позвоните\s+\S*\s*позже|попробуйте перезвонить|"
    r"перенаправлен на голосовой|голосовой почтовый ящик|голосовую почту|"
    r"выключен или находится|номер не обслуживается|заблокирован|"
    r"продолжив договариваться|нажмите один|"
    # Синтетическая AI-болтовня некоторых операторов/приложений во время
    # ожидания соединения (не человек, а сгенерированный "хостинг звонка")
    # — реальный случай на тесте 2026-07-03: минуты бессвязной болтовни
    # про "виртуальную реальность"/"курсы саморазвития" вместо ответа
    # кандидата, при этом отдельные слова случайно распознались как
    # да/нет и привели к ложному отказу настоящему кандидату.
    r"не слышу человеческую речь|нет возможности взять трубку|"
    r"виртуальном ожидании)"
)


def is_voicemail_phrase(raw: str) -> bool:
    """Похоже ли услышанное на приветствие автоответчика/голосовой почты
    оператора, а не на живого человека."""
    if not raw:
        return False
    return bool(_VOICEMAIL_PATTERN.search(_norm(raw)))


_REASK_BY_EXPECT = {
    "yesno": "Простите, не расслышала — это «да» или «нет»?",
    "age": "Сколько вам полных лет? Можно просто цифрой.",
    "citizen_rf": "Уточните, гражданство России или другой страны?",
    "crime": "Судимости по 158, 228 или 105 — были или нет?",
    "shifts": "В ночные смены выходить готовы?",
    "gender_male": "Извините за формальность — вы мужчина?",
}
_REASK_DEFAULT = "Уточните, пожалуйста, ещё раз?"


def reask_text(step: dict) -> str:
    return _REASK_BY_EXPECT.get(step.get("expect"), _REASK_DEFAULT)


def all_reask_texts() -> list:
    """Все возможные варианты reask_text() — для прогрева TTS-кэша перед
    звонком (иначе первое же использование уточняющей фразы посреди
    разговора вызывает живой синтез и заметную паузу)."""
    texts = list(_REASK_BY_EXPECT.values())
    texts.append(_REASK_DEFAULT)
    return texts


# Числительные для возраста — даём Vosk-у словарь чтобы лучше распознавал
_NUMS = [
    "ноль", "один", "одна", "два", "две", "три", "четыре", "пять", "шесть",
    "семь", "восемь", "девять", "десять",
    "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
    "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать",
    "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят",
    "восемьдесят", "девяносто", "сто",
    "лет", "год", "года", "мне", "мне", "уже", "только", "почти",
]
_YESNO_VOCAB = [
    "да", "ага", "угу", "конечно", "разумеется", "готов", "готова", "можно",
    "удобно", "ок", "окей", "хорошо", "есть", "имеется", "имею", "согласен",
    "согласна", "верно", "точно", "именно", "несомненно", "обязательно",
    "легко", "спокойно", "заметано", "договорились", "лады", "ладно", "добро",
    "принято",
    "нет", "не", "неа", "не могу", "не готов", "не готова", "не хочу", "не подходит",
    "перезвоните", "позже", "потом", "не сейчас", "не думаю", "вряд ли",
    "сомневаюсь", "исключено", "не выйдет", "не смогу", "откажусь", "отказываюсь",
]


def vocab_for_step(step: dict) -> Optional[list]:
    """Возвращает список слов которые ждём на этом шаге — подсказка для Vosk.
    None → словарь не ограничиваем."""
    expect = step.get("expect")
    if expect == "yesno":
        return _YESNO_VOCAB + ["[unk]"]
    if expect == "age":
        return _NUMS + _YESNO_VOCAB + ["[unk]"]
    if expect == "gender_male":
        return [
            "мужчина", "мужчин", "мужской", "мужик", "парень", "пацан", "муж",
            "женщина", "женский", "девушка", "девушки", "дама", "тетя", "тетенька",
        ] + _YESNO_VOCAB + ["[unk]"]
    if expect == "citizen_rf":
        return [
            "россии", "россия", "россиянин", "российское", "российская",
            "русский", "русская", "русские", "местный", "местная", "рф",
            "украина", "беларусь", "белорусское", "казахстан", "узбекистан",
            "таджикистан", "киргизстан", "армения", "грузия", "азербайджан",
            "молдова", "туркменистан", "иностранец", "иностранное",
            "паспорт", "патент", "внж", "рвп", "гражданство", "гражданин",
            "подданство", "гражданка",
        ] + _YESNO_VOCAB + ["[unk]"]
    if expect == "crime":
        return [
            "сто пятьдесят восемь", "двести двадцать восемь", "сто пять",
            "кража", "наркотики", "наркота", "убийство", "статья", "судимость",
            "срок", "сидел", "условно", "была", "был", "не было", "никогда",
            "чист", "судим", "не судим", "условный срок", "не привлекался",
            "не привлекалась", "погашена", "снята",
        ] + _YESNO_VOCAB + ["[unk]"]
    if expect == "shifts":
        return [
            "день", "днем", "дневные", "только день", "только дневные",
            "ночь", "ночью", "ночные", "не ночью", "не могу ночью",
            "оба", "любые", "сменные", "сменный", "график", "без разницы", "все равно",
            "плавающий график", "вахта", "вахтой", "посменно",
        ] + _YESNO_VOCAB + ["[unk]"]
    return None  # free и неизвестные — без ограничений


# ── Action — что делать дальше в диалоге ─────────────────────────────────

@dataclass
class Action:
    kind: str                     # 'speak_then_listen' | 'speak_then_end' | 'end'
    text: Optional[str] = None
    end_verdict: Optional[str] = None     # 'passed' | 'stopped' | 'declined'
    end_reason: Optional[str] = None
    answers: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)
    transcript: list = field(default_factory=list)
    summary: Optional[str] = None


# ── Session — одна беседа с одним кандидатом ─────────────────────────────

class DialogSession:
    def __init__(self, scenario: dict, known_answers: Optional[dict] = None,
                 candidate_name: str = ""):
        self.scenario = scenario
        self.candidate_name = candidate_name
        self.steps = scenario["steps"]
        self.i = 0
        self.answers: dict = {}
        self.notes: dict = {}
        self.transcript: list = []
        self.pending: Optional[str] = None
        self.reasked = False
        self.follow_reasked = False  # переспрос уже был на доп.вопросе (lmk_follow)
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.ended = False
        self.verdict: Optional[str] = None
        self.stop_reason: Optional[str] = None
        # Ответы, уже известные из загруженного файла/ручного ввода —
        # {crit: значение}. _speak_current() пропускает эти шаги вместо
        # того чтобы задавать их вслух, см. ниже.
        self.known_answers: dict = dict(known_answers or {})

    def _log(self, who: str, text: str):
        # Реально произносится (см. render_name в phone_call.py) текст с уже
        # подставленным именем кандидата — транскрипт должен показывать то
        # же самое, а не сырой шаблон с буквальным "{name}".
        if who == "bot":
            text = render_name(text, self.candidate_name)
        self.transcript.append({"who": who, "text": text,
                                "ts": datetime.now().isoformat(timespec="seconds")})

    def start(self) -> Action:
        """Возвращает первое действие — обычно speak_then_listen с первым вопросом."""
        return self._speak_current()

    def _speak_current(self) -> Action:
        step = self.steps[self.i]
        crit = step.get("crit", step["id"])
        if crit in self.known_answers:
            raw = self.known_answers[crit]
            raw_str = "" if raw is None else str(raw).strip()
            if raw_str:
                # Прогоняем через interpret() ЗАРАНЕЕ — если значение из
                # файла не парсится под этот тип вопроса, не считаем его
                # известным: иначе submit_answer() ушёл бы в
                # _reask_or_skip(), а тот озвучивает уточняющий вопрос
                # ВСЛУХ посреди звонка, хотя мы должны были просто
                # промолчать и звонить как обычно.
                parsed = interpret(step, raw_str)
                if parsed.get("val") != "unclear":
                    self._log("system", f"[known] {crit} = {raw_str}")
                    return self.submit_answer(raw_str)
        text = step["bot"]
        self._log("bot", text)
        return Action(kind="speak_then_listen", text=text)

    def _end(self, verdict: str, reason: Optional[str], last_bot: Optional[str] = None) -> Action:
        self.ended = True
        self.verdict = verdict
        self.stop_reason = reason
        if last_bot:
            self._log("bot", last_bot)
        return Action(
            kind="speak_then_end" if last_bot else "end",
            text=last_bot,
            end_verdict=verdict,
            end_reason=reason,
            answers=self.answers,
            notes=self.notes,
            transcript=self.transcript,
        )

    def submit_answer(self, raw: str) -> Action:
        """Принимает текстовый ответ кандидата. Возвращает следующее действие."""
        if self.ended:
            return Action(kind="end", end_verdict=self.verdict, end_reason=self.stop_reason,
                          answers=self.answers, notes=self.notes, transcript=self.transcript)
        raw = (raw or "").strip()
        step = self.steps[self.i]

        # Спецветка для on_no_follow (доп.вопрос про ЛМК) — ПЕРЕД общим
        # "raw пуст → _reask_or_skip()", потому что тот работает с
        # self.steps[self.i] (исходный вопрос про ЛМК), а не с текстом
        # доп.вопроса про доплату — иначе на пустой/невнятный ответ на
        # "Стоимость 4000 рублей... Это подходит?" бот вместо повтора
        # ЭТОГО вопроса переспрашивал заново "Есть ли у вас медкнижка?",
        # что кандидату явно не в тему (реальный случай на тесте
        # 2026-07-03: именно так и произошло).
        if self.pending == "lmk_follow":
            if raw:
                self._log("user", raw)
            follow_text = step.get("on_no_follow", "")
            if not raw:
                r = {"val": "unclear"}
            else:
                r = interpret({"expect": "yesno"}, raw)
                if r["val"] == "unclear" and self.follow_reasked:
                    rl = llm_classify({"expect": "yesno", "bot": follow_text}, raw)
                    if rl:
                        r = rl
            if r["val"] == "unclear":
                # Как и в основном пути: сначала честный переспрос ЭТОГО
                # вопроса, LLM-фоллбэк — только со второй непонятной попытки.
                # Раньше неясный ответ тут молча трактовался как согласие
                # на удержание 4000₽ — рискованно приписывать кандидату
                # согласие, которого он не давал.
                if not self.follow_reasked:
                    self.follow_reasked = True
                    self._log("bot", follow_text)
                    return Action(kind="speak_then_listen", text=follow_text)
                self.notes["ЛМК"] = "не распознано (доп.вопрос про доплату)"
                self.pending = None
                return self._next()
            self.notes["ЛМК"] = ("нет, отказался от изготовления"
                                 if r["val"] == "no"
                                 else "нет, согласен на изготовление за 2 дня")
            self.pending = None
            return self._next()

        if not raw:
            return self._reask_or_skip()
        self._log("user", raw)

        r = interpret(step, raw)
        if r.get("val") == "unclear":
            # Сначала — честный переспрос вслух, и только если ответ
            # НЕПОНЯТЕН УЖЕ ВТОРОЙ РАЗ, подключаем LLM-фоллбэк. Раньше LLM
            # спрашивали сразу на первой же непонятной фразе — а STT на
            # телефонном звуке иногда выдаёт откровенный мусор (реальный
            # случай: кандидат сказал «не было судимостей», распозналось
            # как «глебова» — LLM с одного слова решила что это «да»,
            # кандидата ошибочно отклонили). Второй шанс ответить вслух
            # ловит такие случаи раньше, чем ИИ начинает гадать по шуму.
            if not self.reasked:
                return self._reask_or_skip(raw)
            rl = llm_classify(step, raw)
            if rl:
                r = rl

        if r.get("val") == "unclear":
            return self._reask_or_skip(raw)

        # yesno спец-логика: стоп при «нет» (end_on_no) ИЛИ при «да» (end_on_yes)
        if step.get("expect") == "yesno":
            # Стоп при ответе «нет» (приветствие, пол, гражданство, физнагрузка)
            if r["val"] == "no" and step.get("end_on_no"):
                self.answers[step.get("crit", step["id"])] = "нет"
                return self._end(step.get("end_verdict", "stopped"),
                                 step.get("end_reason") or (step.get("crit", step["id"]) + ": нет"),
                                 last_bot=step.get("on_no") or step.get("stop_msg"))
            # Стоп при ответе «да» (судимости: «да, была судимость» → отказ)
            if r["val"] == "yes" and step.get("end_on_yes"):
                self.answers[step.get("crit", step["id"])] = "да"
                return self._end(step.get("end_verdict", "stopped"),
                                 step.get("end_reason") or (step.get("crit", step["id"]) + ": да"),
                                 last_bot=step.get("on_yes") or step.get("stop_msg"))
            # Доп. вопрос при «нет» (ЛМК — предложить изготовить)
            if r["val"] == "no" and step.get("on_no_follow"):
                self.notes[step.get("crit", step["id"])] = "нет"
                self.pending = "lmk_follow"
                follow = step["on_no_follow"]
                self._log("bot", follow)
                return Action(kind="speak_then_listen", text=follow)
            # Обычный yesno — просто записать ответ
            ans = "да" if r["val"] == "yes" else "нет"
            if step.get("soft"):
                self.notes[step.get("crit", step["id"])] = ans
            else:
                self.answers[step.get("crit", step["id"])] = ans
            return self._next()

        # Смены: стоп если распознанный вариант не входит в accepted.
        # accepted по умолчанию ["день+ночь"] — сохраняет старое поведение.
        if step.get("expect") == "shifts":
            accepted = step.get("accepted") or ["день+ночь"]
            self.answers[step.get("crit", step["id"])] = r["val"]
            if r["val"] not in accepted:
                return self._end(step.get("end_verdict", "stopped"),
                                 step.get("end_reason") or (step.get("crit", step["id"]) + ": " + r["val"]),
                                 last_bot=step.get("stop_msg"))
            return self._next()

        if r.get("stop"):
            self.answers[step.get("crit", step["id"])] = r["val"]
            return self._end("stopped", f"{step.get('crit', step['id'])}: {r['val']}",
                             last_bot=step.get("stop_msg"))

        if step.get("soft") or step.get("expect") == "free":
            self.notes[step.get("crit", step["id"])] = raw
        else:
            self.answers[step.get("crit", step["id"])] = r["val"]
        return self._next()

    def _reask_or_skip(self, raw: str = "") -> Action:
        step = self.steps[self.i]
        if not self.reasked:
            self.reasked = True
            # Если не распознано вообще НИЧЕГО (raw пуст — тишина, шум,
            # ложное срабатывание barge-in на кашель/шорох), кандидат мог
            # физически не услышать сам вопрос (бота оборвало раньше
            # времени). Абстрактный шаблон "да или нет?" в таком случае
            # ничего не объясняет — повторяем ИСХОДНЫЙ вопрос целиком.
            # Уточняющий шаблон оставляем только когда что-то РАСПОЗНАНО,
            # но не понято (raw непустой) — там переспрос в тему.
            t = step["bot"] if not raw else reask_text(step)
            self._log("bot", t)
            return Action(kind="speak_then_listen", text=t)
        crit = step.get("crit", step["id"])
        self.notes[crit] = "не распознано" + (": " + raw if raw else "")
        return self._next()

    def _next(self) -> Action:
        self.i += 1
        self.reasked = False
        self.follow_reasked = False
        if self.i >= len(self.steps):
            return self._end("passed", None, last_bot=self.scenario.get("closing"))
        return self._speak_current()


def load_scenario(scenario_id: str) -> dict:
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Сценарий не найден: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def render_name(text: str, name: str = "") -> str:
    """Подставляет {name} в текст бота. Если имени нет — аккуратно вырезает
    плейсхолдер вместе с окружающими запятыми/пробелами, чтобы не осталось
    двойных пробелов или висящей запятой.
    «Здравствуйте, {name}! Меня зовут...» без имени → «Здравствуйте! Меня зовут...»
    """
    if not text or "{name}" not in text:
        return text
    if name:
        return text.replace("{name}", name.strip())
    cleaned = re.sub(r"[,\s]*\{name\}[,\s]*", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([!?.,])", r"\1", cleaned)
    return cleaned


# ── CLI для теста в текстовом режиме ────────────────────────────────────

def main():
    scenario_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SCENARIO
    scenario = load_scenario(scenario_id)
    print(f"╔═ Сценарий: {scenario['name']}")
    print(f"╚═ Стоп-факторы: {', '.join(scenario.get('stop_factors', []))}")
    print()

    sess = DialogSession(scenario)
    action = sess.start()

    while True:
        if action.kind in ("speak_then_listen", "speak_then_end"):
            print(f"[БОТ]  {action.text}")
        if action.kind == "speak_then_listen":
            try:
                ans = input("[ТЫ]   ")
            except EOFError:
                break
            action = sess.submit_answer(ans)
            continue
        # end или speak_then_end
        break

    print()
    print("═════ ИТОГИ ═════")
    print(f"Вердикт:  {action.end_verdict}")
    if action.end_reason:
        print(f"Причина:  {action.end_reason}")
    print(f"Ответы:   {json.dumps(action.answers, ensure_ascii=False)}")
    print(f"Заметки:  {json.dumps(action.notes, ensure_ascii=False)}")
    print(f"Реплик в транскрипте: {len(action.transcript)}")


if __name__ == "__main__":
    main()
