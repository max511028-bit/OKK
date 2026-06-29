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
    r"согласен|согласна|есть|имеется|имею|верно|так точно|ясно|без проблем)(\s|$)"
)
_NO = re.compile(
    r"(^|\s)(нет|неа|не-а|не готов|не могу|не хочу|не буду|не подходит|нету|"
    r"отказ|против|неудобно|занят|занята|перезвоните|позже|потом|не сейчас)(\s|$)"
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
}


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
        # десятки + единицы: «двадцать девять» = 29
        if val in (20, 30, 40, 50, 60, 70, 80, 90):
            if i + 1 < len(words):
                next_w = words[i + 1]
                if next_w in _NUM_WORDS:
                    next_val = _NUM_WORDS[next_w]
                    if 1 <= next_val <= 9:
                        val += next_val
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


def reask_text(step: dict) -> str:
    return {
        "yesno": "Простите, не расслышала — это «да» или «нет»?",
        "age": "Сколько вам полных лет? Можно просто цифрой.",
        "citizen_rf": "Уточните, гражданство России или другой страны?",
        "crime": "Судимости по 158, 228 или 105 — были или нет?",
        "shifts": "В ночные смены выходить готовы?",
        "gender_male": "Извините за формальность — вы мужчина?",
    }.get(step.get("expect"), "Уточните, пожалуйста, ещё раз?")


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
    "согласна", "верно",
    "нет", "не", "неа", "не могу", "не готов", "не готова", "не хочу", "не подходит",
    "перезвоните", "позже", "потом", "не сейчас",
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
            "женщина", "женский", "девушка", "девушки", "дама",
        ] + _YESNO_VOCAB + ["[unk]"]
    if expect == "citizen_rf":
        return [
            "россии", "россия", "россиянин", "российское", "российская",
            "русский", "русская", "русские", "местный", "местная", "рф",
            "украина", "беларусь", "белорусское", "казахстан", "узбекистан",
            "таджикистан", "киргизстан", "армения", "грузия", "азербайджан",
            "молдова", "туркменистан", "иностранец", "иностранное",
            "паспорт", "патент", "внж", "рвп", "гражданство", "гражданин",
        ] + _YESNO_VOCAB + ["[unk]"]
    if expect == "crime":
        return [
            "сто пятьдесят восемь", "двести двадцать восемь", "сто пять",
            "кража", "наркотики", "наркота", "убийство", "статья", "судимость",
            "срок", "сидел", "условно", "была", "был", "не было", "никогда",
            "чист", "судим", "не судим",
        ] + _YESNO_VOCAB + ["[unk]"]
    if expect == "shifts":
        return [
            "день", "днем", "дневные", "только день", "только дневные",
            "ночь", "ночью", "ночные", "не ночью", "не могу ночью",
            "оба", "любые", "сменные", "сменный", "график", "без разницы", "все равно",
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
    def __init__(self, scenario: dict):
        self.scenario = scenario
        self.steps = scenario["steps"]
        self.i = 0
        self.answers: dict = {}
        self.notes: dict = {}
        self.transcript: list = []
        self.pending: Optional[str] = None
        self.reasked = False
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.ended = False
        self.verdict: Optional[str] = None
        self.stop_reason: Optional[str] = None

    def _log(self, who: str, text: str):
        self.transcript.append({"who": who, "text": text,
                                "ts": datetime.now().isoformat(timespec="seconds")})

    def start(self) -> Action:
        """Возвращает первое действие — обычно speak_then_listen с первым вопросом."""
        return self._speak_current()

    def _speak_current(self) -> Action:
        step = self.steps[self.i]
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
        if not raw:
            return self._reask_or_skip()
        self._log("user", raw)
        step = self.steps[self.i]

        # Спецветка для on_no_follow (доп.вопрос про ЛМК)
        if self.pending == "lmk_follow":
            r = interpret({"expect": "yesno"}, raw)
            if r["val"] == "unclear":
                rl = llm_classify({"expect": "yesno",
                                    "bot": step.get("on_no_follow", "")}, raw)
                if rl:
                    r = rl
            self.notes["ЛМК"] = ("нет, отказался от изготовления"
                                 if r["val"] == "no"
                                 else "нет, согласен на изготовление за 2 дня")
            self.pending = None
            return self._next()

        r = interpret(step, raw)
        if r.get("val") == "unclear":
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
            t = reask_text(step)
            self._log("bot", t)
            return Action(kind="speak_then_listen", text=t)
        crit = step.get("crit", step["id"])
        self.notes[crit] = "не распознано" + (": " + raw if raw else "")
        return self._next()

    def _next(self) -> Action:
        self.i += 1
        self.reasked = False
        if self.i >= len(self.steps):
            return self._end("passed", None, last_bot=self.scenario.get("closing"))
        return self._speak_current()


def load_scenario(scenario_id: str) -> dict:
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Сценарий не найден: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


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
