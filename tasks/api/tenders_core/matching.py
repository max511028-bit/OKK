"""Отбор тендеров по направлениям.

Основная сложность — русская морфология: «аутсорсинг персонала», «аутсорсинга
персоналом» и «аутсорсинговых персональных» должны считаться одной фразой, а
поиск по подстроке этого не даёт. Поэтому фразы и тексты приводятся к основам
алгоритмом Snowball (russian), после чего фраза ищется как последовательность
подряд идущих основ.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Sequence

VOWELS = "аеиоуыэюя"
_WORD_RE = re.compile(r"[а-яa-z0-9]+")


# --------------------------------------------------------------------------
# Snowball-стеммер для русского языка
# --------------------------------------------------------------------------
_PERFECTIVE_GERUND_1 = ("вшись", "вши", "в")
_PERFECTIVE_GERUND_2 = ("ившись", "ывшись", "ивши", "ывши", "ив", "ыв")
_ADJECTIVE = (
    "ими", "ыми", "его", "ого", "ему", "ому", "ее", "ие", "ые", "ое", "ей", "ий",
    "ый", "ой", "ем", "им", "ым", "ом", "их", "ых", "ую", "юю", "ая", "яя", "ою", "ею",
)
_PARTICIPLE_1 = ("ющ", "ем", "нн", "вш", "щ")
_PARTICIPLE_2 = ("ивш", "ывш", "ующ")
_REFLEXIVE = ("ся", "сь")
_VERB_1 = (
    "ешь", "нно", "ете", "йте", "ла", "на", "ли", "ем", "ло", "но", "ет", "ют",
    "ны", "ть", "й", "л", "н",
)
_VERB_2 = (
    "ейте", "уйте", "ила", "ыла", "ена", "ены", "ить", "ыть", "ишь", "ит", "ыт",
    "ую", "ю",
)
_NOUN = (
    "иями", "ями", "ами", "иях", "ях", "ах", "ией", "ей", "ой", "ий", "иям", "ям",
    "ием", "ем", "ам", "ом", "ев", "ов", "ие", "ье", "еи", "ии", "ию", "ью", "ия",
    "ья", "а", "е", "и", "й", "о", "у", "ы", "ь", "ю", "я",
)
_SUPERLATIVE = ("ейше", "ейш")
_DERIVATIONAL = ("ость", "ост")


def _regions(word: str) -> tuple[int, int]:
    """Границы RV и R2 по определению Snowball."""
    rv = len(word)
    for i, ch in enumerate(word):
        if ch in VOWELS:
            rv = i + 1
            break

    def after_vowel_consonant(start: int) -> int:
        i = start
        while i < len(word) - 1:
            if word[i] in VOWELS and word[i + 1] not in VOWELS:
                return i + 2
            i += 1
        return len(word)

    r1 = after_vowel_consonant(0)
    r2 = after_vowel_consonant(r1) if r1 < len(word) else len(word)
    return rv, r2


def _strip(word: str, start: int, endings: Sequence[str], preceded_by: str = "") -> str | None:
    """Снимает первое подошедшее окончание, если оно целиком лежит после `start`."""
    for ending in endings:
        if not word.endswith(ending):
            continue
        cut = len(word) - len(ending)
        if cut < start:
            continue
        if preceded_by:
            if cut == 0 or word[cut - 1] not in preceded_by:
                continue
            cut -= 1
        return word[:cut]
    return None


@lru_cache(maxsize=200_000)
def stem(word: str) -> str:
    """Основа слова. Латиница и цифры возвращаются как есть."""
    word = word.replace("ё", "е")
    if not word or word[0] not in VOWELS + "бвгджзйклмнпрстфхцчшщъь":
        return word

    rv, r2 = _regions(word)

    # Шаг 1
    step1 = _strip(word, rv, _PERFECTIVE_GERUND_2) or _strip(
        word, rv, _PERFECTIVE_GERUND_1, preceded_by="ая"
    )
    if step1 is not None:
        word = step1
    else:
        reflexive = _strip(word, rv, _REFLEXIVE)
        if reflexive is not None:
            word = reflexive
        adjectival = _strip(word, rv, _ADJECTIVE)
        if adjectival is not None:
            word = adjectival
            participle = _strip(word, rv, _PARTICIPLE_2) or _strip(
                word, rv, _PARTICIPLE_1, preceded_by="ая"
            )
            if participle is not None:
                word = participle
        else:
            verb = _strip(word, rv, _VERB_2) or _strip(word, rv, _VERB_1, preceded_by="ая")
            if verb is not None:
                word = verb
            else:
                noun = _strip(word, rv, _NOUN)
                if noun is not None:
                    word = noun

    # Шаг 2
    if word.endswith("и"):
        cut = len(word) - 1
        if cut >= rv:
            word = word[:cut]

    # Шаг 3
    derivational = _strip(word, r2, _DERIVATIONAL)
    if derivational is not None:
        word = derivational

    # Шаг 4
    if word.endswith("нн"):
        word = word[:-1]
    else:
        superlative = _strip(word, rv, _SUPERLATIVE)
        if superlative is not None:
            word = superlative
            if word.endswith("нн"):
                word = word[:-1]
        elif word.endswith("ь"):
            word = word[:-1]
    return word


# --------------------------------------------------------------------------
# Нормализация текста
# --------------------------------------------------------------------------
def normalize(text: str | None) -> str:
    """Нижний регистр, ё→е, только буквы/цифры через один пробел."""
    if not text:
        return ""
    return " ".join(_WORD_RE.findall(text.lower().replace("ё", "е")))


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    return _WORD_RE.findall(text.lower().replace("ё", "е"))


@lru_cache(maxsize=50_000)
def stem_phrase(phrase: str) -> tuple[str, ...]:
    return tuple(stem(t) for t in tokenize(phrase))


def stem_tokens(text: str | None) -> list[str]:
    return [stem(t) for t in tokenize(text)]


# Стеммер не всегда сводит формы одного слова к одной основе: «персонала» даёт
# «персон», а «персоналом» — «персонал». Поэтому основы считаем совпавшими и
# тогда, когда одна является началом другой и расходятся они на пару букв.
_PREFIX_MIN_LEN = 5
_PREFIX_MAX_DIFF = 3


def stems_equal(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < _PREFIX_MIN_LEN or len(long) - len(short) > _PREFIX_MAX_DIFF:
        return False
    return long.startswith(short)


def phrase_in_stems(phrase_stems: Sequence[str], text_stems: Sequence[str]) -> bool:
    """Есть ли последовательность основ фразы среди основ текста."""
    n, m = len(phrase_stems), len(text_stems)
    if n == 0 or n > m:
        return False
    first = phrase_stems[0]
    for i in range(m - n + 1):
        if not stems_equal(text_stems[i], first):
            continue
        if all(stems_equal(text_stems[i + j], phrase_stems[j]) for j in range(1, n)):
            return True
    return False


# --------------------------------------------------------------------------
# Отбор
# --------------------------------------------------------------------------
@dataclass
class TenderText:
    """Предпосчитанные представления текста тендера — считаем один раз на тендер."""

    plain: str
    stems: list[str]

    @classmethod
    def build(cls, *parts: str | None) -> "TenderText":
        raw = " \n ".join(p for p in parts if p)
        return cls(plain=normalize(raw), stems=stem_tokens(raw))


@dataclass
class MatchResult:
    direction_id: int
    score: float
    matched: list[str] = field(default_factory=list)


def _keyword_hits(keyword, text: TenderText) -> bool:
    mode = (keyword.match_mode or "stem").lower()
    phrase = keyword.phrase or ""
    if not phrase.strip():
        return False
    if mode == "regex":
        try:
            return re.search(phrase, text.plain, re.IGNORECASE) is not None
        except re.error:
            return False
    if mode == "exact":
        needle = normalize(phrase)
        return bool(needle) and needle in text.plain
    return phrase_in_stems(stem_phrase(phrase), text.stems)


def _passes_filters(direction, tender) -> bool:
    """Числовые и категориальные фильтры направления."""
    _get = (lambda k: tender.get(k)) if isinstance(tender, dict) else (lambda k: getattr(tender, k, None))
    price = _get("price")
    region = _get("region") or ""
    law = _get("law") or ""
    okpd2 = _get("okpd2") or ""
    source_code = _get("source_code") or ""
    customer = _get("customer") or ""

    if direction.source_codes and source_code not in direction.source_codes:
        return False
    if direction.min_price is not None and (price is None or price < direction.min_price):
        return False
    if direction.max_price is not None and price is not None and price > direction.max_price:
        return False
    if direction.laws and law not in direction.laws:
        return False
    if direction.regions:
        region_norm = normalize(region)
        if not any(normalize(r) in region_norm for r in direction.regions if r.strip()):
            return False
    if direction.okpd2:
        codes = [c.strip() for c in re.split(r"[;,\s]+", okpd2) if c.strip()]
        if not codes or not any(
            code.startswith(prefix.strip()) for code in codes for prefix in direction.okpd2
        ):
            return False

    # Город (добавлено 31.07 по просьбе владельца). Отдельного поля «город»
    # у площадок нет — они отдают регион и адрес заказчика вперемешку,
    # поэтому ищем вхождение по всем текстовым полям, где город реально
    # встречается. Фильтр намеренно широкий: лучше показать лишний тендер
    # соседнего города, чем потерять нужный из-за того, что площадка
    # записала «г. Пермь» в название заказчика, а не в регион.
    if getattr(direction, "cities", None):
        haystack = normalize(" ".join([
            region, customer, _get("title") or "", _get("description") or "",
        ]))
        if not any(normalize(c) in haystack for c in direction.cities if c.strip()):
            return False

    # Конкретный заказчик — а вот тут наоборот, ищем строго по полю
    # заказчика: смысл фильтра в том, чтобы следить за определённой
    # компанией, и совпадение по тексту описания было бы ложным.
    if getattr(direction, "customers", None):
        customer_norm = normalize(customer)
        if not any(normalize(c) in customer_norm for c in direction.customers if c.strip()):
            return False
    return True


def match_tender(tender, directions: Iterable) -> list[MatchResult]:
    """Возвращает список направлений, под которые подошёл тендер.

    `tender` — ORM-объект Tender или словарь с теми же ключами.
    """
    get = (lambda k: tender.get(k)) if isinstance(tender, dict) else (lambda k: getattr(tender, k, None))
    text = TenderText.build(
        get("title"), get("description"), get("customer"), get("okpd2_name"), get("purchase_method")
    )

    results: list[MatchResult] = []
    for direction in directions:
        if not direction.is_active:
            continue
        if not _passes_filters(direction, tender):
            continue

        active = [k for k in direction.keywords if k.is_active]
        excludes = [k for k in active if k.kind == "exclude"]
        requires = [k for k in active if k.kind == "require"]
        includes = [k for k in active if k.kind == "include"]

        if any(_keyword_hits(k, text) for k in excludes):
            continue
        if requires and not all(_keyword_hits(k, text) for k in requires):
            continue

        score = 0.0
        hits: list[str] = []
        for kw in includes:
            if _keyword_hits(kw, text):
                score += kw.weight
                hits.append(kw.phrase)
        # Направление без include-слов, но с require — засчитываем по требованиям.
        if not includes and requires:
            score = max(score, direction.min_score)
            hits = [k.phrase for k in requires]

        if score >= direction.min_score and score > 0:
            results.append(MatchResult(direction_id=direction.id, score=round(score, 2), matched=hits))

    return results
