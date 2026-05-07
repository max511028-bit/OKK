"""
Recruiter tool business logic.
Handles Google Sheets/Docs integration, prompt building, and stats logging.
"""
import json
import re
import os
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

# ── КОНСТАНТЫ ─────────────────────────────────────────────────────────────────
ROADMAP_SHEET_ID = "1JTn8c7-hQz3zGRjsOLTxNpVmowBLCGYA4GF0wfC26go"
HANDBOOK_DOC_ID  = "1m7h5FRgra-ZFllYsKUgpV2Frt2rtAKpcTpPcmTPpkhs"
STATS_SHEET_ID   = "1E_glmHFjFynYZLC5HsZywmOT6uNY4L6BfjRRHTiSm9A"

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents.readonly",
]

CREDS_FILE = Path(__file__).parent / "google_credentials.json"

# ── СИСТЕМНЫЙ ПРОМПТ ──────────────────────────────────────────────────────────
RECRUITER_SYSTEM_PROMPT = """Ты — AI-помощник рекрутера. Твоя задача — помочь рекрутеру отработать возражение кандидата так, чтобы он согласился выйти на стажировку.

ДВУХШАГОВЫЙ ПОДХОД — строго соблюдай:

ШАГ 1 — ПРЕЖДЕ ЧЕМ ОТВЕЧАТЬ, извлеки из ТЗ и запомни:
- Тип оплаты: почасовая или сдельная (и конкретные цифры)
- Диапазон заработка за смену и за месяц
- График работы
- Тип оформления (ГПХ / ТК / другое)
- Корпоративный транспорт: есть или нет, откуда
- Вахта: есть или нет, условия проживания, компенсации
- Бонусы, надбавки, компенсации (питание, билеты, реферальный)

ШАГ 2 — составь скрипт отработки, используя ТОЛЬКО конкретные цифры и факты из ШАГ 1. Не придумывай данные которых нет в ТЗ.

ГЛАВНАЯ ЗАДАЧА:
Отработать возражение через выгоды — не давать альтернатив, не переориентировать на другой проект или другой вид оформления. Кандидат обратился с конкретным возражением — именно его нужно закрыть. Опирайся на условия из ТЗ, но текст должен быть продающего характера, не информационного.

ПРАВИЛА (строго соблюдай):
1. Отвечай ТОЛЬКО на русском языке.
2. Факты (зарплата, график, адрес, транспорт, оформление и т.д.) бери ИСКЛЮЧИТЕЛЬНО из ТЗ проекта. Не придумывай и не предполагай.
3. Методологию и техники отработки возражений бери из Учебника.
4. ПРОДАЮЩИЙ ТЕКСТ, НЕ ИНФОРМАЦИОННЫЙ. Не перечисляй условия — показывай выгоду для кандидата. Не "у нас ГПХ" — а "вы получаете зарплату полностью на руки, без налогов, можете совмещать". Не "сдельная оплата" — а "вы сами влияете на свой заработок, чем больше работаете — тем больше получаете". Каждое условие из ТЗ должно звучать как выгода для конкретного человека.
5. НЕ ДАВАЙ АЛЬТЕРНАТИВ И НЕ ПЕРЕОРИЕНТИРУЙ. Если кандидат возражает против ГПХ — не говори что есть ТК или другой проект. Если возражает против графика — не предлагай другой склад. Задача — закрыть именно это возражение на этом проекте.
6. ПЕРЕОРИЕНТАЦИЯ НА ДРУГОЙ ПРОЕКТ — вторична. Только если возражение абсолютно непреодолимо и все аргументы по данному проекту исчерпаны.
7. ЗАВЕРШАЙ КАЖДЫЙ ВАРИАНТ СКРИПТА призывом на стажировку — строго по этой формуле: "В любом случае вы ничего не теряете, поэтому предлагаю вам приехать на объект, познакомиться с бригадиром, посмотреть условия, попробовать поработать и далее принимать решение. Завтра готовы будете подъехать?"
8. ЕСЛИ НУЖНОГО ФАКТА НЕТ В ТЗ — не оставляй рекрутера с пустым ответом. Дай универсальную отработку возражения, максимально приближённую к условиям проекта из ТЗ, чтобы рекрутер мог использовать её прямо сейчас. Дополнительно добавь блок УТОЧНИТЬ с конкретным вопросом.

КРИТИЧЕСКИ ВАЖНО — ФОРМАТ ОТВЕТА:
Строго соблюдай следующую структуру и ТОЛЬКО её. Не добавляй ничего вне этих блоков, не меняй названия заголовков:

АНАЛИЗ:
[1-2 предложения: тип возражения и что за ним стоит]

ВАРИАНТ 1: [короткое название подхода]
[текст скрипта — самый сильный вариант]
[призыв на стажировку]

ВАРИАНТ 2: [короткое название подхода]
[текст скрипта]
[призыв на стажировку]

ВАРИАНТ 3: [короткое название подхода, если уместен]
[текст скрипта]
[призыв на стажировку]

УТОЧНИТЬ: [только если факта нет в ТЗ — конкретный вопрос менеджеру. Если всё есть — этот блок не нужен]

РЕКОМЕНДАЦИИ: [не более 2 коротких техники из учебника. Если нечего добавить — не нужен]
"""

CHAT_SYSTEM_PROMPT = """Ты — AI-помощник рекрутера. Вы уже обсудили возражение кандидата и получили скрипт отработки.
Теперь рекрутер задаёт уточняющие вопросы. Отвечай кратко и по делу, опираясь на ТЗ проекта и учебник.
Отвечай ТОЛЬКО на русском языке. Придерживайся той же логики: факты только из ТЗ, текст продающий."""

# ── КЭШИ ──────────────────────────────────────────────────────────────────────
_cache_lock = threading.Lock()
_handbook_cache: Optional[str] = None
_handbook_ts: float = 0.0
_projects_cache: Optional[dict] = None
_projects_ts: float = 0.0
_tz_cache: dict = {}       # sheet_id → (text, ts)

HANDBOOK_TTL  = 86400  # 24 ч
PROJECTS_TTL  = 300    # 5 мин
TZ_TTL        = 14400  # 4 ч


# ── КЛИЕНТЫ GOOGLE ────────────────────────────────────────────────────────────
_gs_client = None
_docs_service = None
_clients_error: Optional[str] = None


def _init_clients():
    global _gs_client, _docs_service, _clients_error
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        # Пробуем файл, потом env
        if CREDS_FILE.exists():
            info = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
        else:
            raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_INFO", "")
            if not raw:
                raise RuntimeError("google_credentials.json не найден и GOOGLE_SERVICE_ACCOUNT_INFO не задан")
            info = json.loads(raw)

        legacy_creds = ServiceAccountCredentials.from_json_keyfile_dict(info, SCOPES)
        _gs_client = gspread.authorize(legacy_creds)

        modern_creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        _docs_service = build("docs", "v1", credentials=modern_creds, cache_discovery=False)
        _clients_error = None
    except Exception as e:
        _clients_error = str(e)
        _gs_client = None
        _docs_service = None


def get_clients():
    """Вернуть (gs_client, docs_service), инициализировать при необходимости."""
    if _gs_client is None and _clients_error is None:
        _init_clients()
    return _gs_client, _docs_service


def clients_ready() -> bool:
    get_clients()
    return _gs_client is not None


# ── ЗАГРУЗКА УЧЕБНИКА ─────────────────────────────────────────────────────────
def load_handbook() -> str:
    global _handbook_cache, _handbook_ts
    with _cache_lock:
        now = time.time()
        if _handbook_cache is not None and now - _handbook_ts < HANDBOOK_TTL:
            return _handbook_cache

    _, docs = get_clients()
    if not docs:
        return f"⚠️ Google Docs недоступен: {_clients_error}"
    try:
        doc = docs.documents().get(documentId=HANDBOOK_DOC_ID).execute()
        lines = []
        for block in doc.get("body", {}).get("content", []):
            para = block.get("paragraph")
            if not para:
                continue
            text = "".join(
                el.get("textRun", {}).get("content", "")
                for el in para.get("elements", [])
            )
            if text.strip():
                lines.append(text.rstrip("\n"))
        result = "\n".join(lines)
        with _cache_lock:
            _handbook_cache = result
            _handbook_ts = time.time()
        return result
    except Exception as e:
        return f"⚠️ Не удалось загрузить учебник: {e}"


# ── ЗАГРУЗКА СПИСКА ПРОЕКТОВ ──────────────────────────────────────────────────
def load_projects_list() -> dict:
    global _projects_cache, _projects_ts
    with _cache_lock:
        now = time.time()
        if _projects_cache is not None and now - _projects_ts < PROJECTS_TTL:
            return _projects_cache

    gs, _ = get_clients()
    if not gs:
        return {}
    try:
        sh = gs.open_by_key(ROADMAP_SHEET_ID)
        ws = sh.worksheet("Шаблон")
        header_row = ws.row_values(1)
        try:
            tz_col_idx = header_row.index("TZ_URL") + 1
        except ValueError:
            return {}
        col_k = ws.col_values(11)
        col_q = ws.col_values(tz_col_idx)
        projects = {}
        for i in range(1, max(len(col_k), len(col_q))):
            name_val = col_k[i].strip() if i < len(col_k) else ""
            if not name_val:
                continue
            url = col_q[i].strip() if i < len(col_q) else ""
            if not url:
                continue
            m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
            if m:
                projects[name_val] = {"tz_id": m.group(1)}
        with _cache_lock:
            _projects_cache = projects
            _projects_ts = time.time()
        return projects
    except Exception as e:
        return {}


# ── ЧТЕНИЕ ТЗ ─────────────────────────────────────────────────────────────────
def read_tz_data(sheet_id: str) -> str:
    with _cache_lock:
        if sheet_id in _tz_cache:
            text, ts = _tz_cache[sheet_id]
            if time.time() - ts < TZ_TTL:
                return text

    gs, _ = get_clients()
    if not gs:
        return f"⚠️ Google Sheets недоступен: {_clients_error}"
    try:
        sh = gs.open_by_key(sheet_id)
        lines = []
        for ws in sh.worksheets():
            records = ws.get_all_values()
            if records:
                lines.append(f"=== Лист: {ws.title} ===")
                for row in records:
                    row_text = " | ".join(str(c).strip() for c in row)
                    if row_text.strip(" |"):
                        lines.append(row_text)
        result = "\n".join(lines)
        with _cache_lock:
            _tz_cache[sheet_id] = (result, time.time())
        return result
    except Exception as e:
        return f"Ошибка чтения ТЗ: {e}"


# ── СТАТИСТИКА ─────────────────────────────────────────────────────────────────
def _get_or_create_sheet(sh, title: str, headers: list):
    try:
        ws = sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
    if not ws.row_values(1):
        ws.append_row(headers)
    return ws


def save_feedback(project: str, objection: str, variant_title: str, feedback: str):
    gs, _ = get_clients()
    if not gs:
        return
    try:
        sh = gs.open_by_key(STATS_SHEET_ID)
        ws = _get_or_create_sheet(sh, "Оценки", ["Дата", "Проект", "Возражение", "Вариант", "Оценка"])
        feedback_text = "Лайк" if feedback in ("👍", "like") else "Дизлайк"
        ws.append_row([
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            project,
            objection,
            variant_title,
            feedback_text,
        ])
    except Exception:
        pass


def save_request_log(project: str, objection: str, variants: list):
    gs, _ = get_clients()
    if not gs:
        return
    try:
        sh = gs.open_by_key(STATS_SHEET_ID)
        ws = _get_or_create_sheet(sh, "Лог запросов", [
            "Дата", "Проект", "Возражение",
            "Вариант 1 (название)", "Вариант 1 (текст)",
            "Вариант 2 (название)", "Вариант 2 (текст)",
            "Вариант 3 (название)", "Вариант 3 (текст)",
        ])
        row = [datetime.now().strftime("%d.%m.%Y %H:%M"), project, objection]
        for v in (variants + [{}, {}, {}])[:3]:
            row.append(v.get("title", ""))
            row.append(v.get("body", ""))
        ws.append_row(row)
    except Exception:
        pass


def save_chat_log(project: str, objection: str, question: str, answer: str):
    gs, _ = get_clients()
    if not gs:
        return
    try:
        sh = gs.open_by_key(STATS_SHEET_ID)
        ws = _get_or_create_sheet(sh, "Диалоги", [
            "Дата", "Проект", "Исходное возражение", "Вопрос рекрутера", "Ответ AI"
        ])
        ws.append_row([
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            project, objection, question, answer,
        ])
    except Exception:
        pass


# ── ПОСТРОЕНИЕ ПРОМПТОВ ────────────────────────────────────────────────────────

# Ключевые слова по типам возражений → какие строки ТЗ важны
_OBJECTION_KEYWORDS: dict[str, list[str]] = {
    "salary": [
        "зарплат", "оплат", "заработ", "ставк", "оклад", "рубл", "₽",
        "выплат", "премия", "бонус", "надбав", "доход", "фот", "сдельн",
        "почасов", "смен", "час", "итог", "аванс", "получ",
    ],
    "transport": [
        "транспорт", "автобус", "метро", "маршрут", "доставк", "подвоз",
        "добират", "расстоян", "км", "адрес", "склад", "город", "район",
        "остановк", "станци",
    ],
    "schedule": [
        "график", "смена", "сутки", "часов", "выходн", "рабочих", "смен",
        "ночн", "дневн", "недел", "расписан", "режим", "11", "12",
        "перерыв", "обед",
    ],
    "contract": [
        "оформлен", "договор", "гпх", "трудов", "официальн", "нк рф",
        "самозанят", "ип ", "белая", "зарплат", "налог", "пфр", "снилс",
    ],
    "rotation": [
        "вахт", "общежит", "проживан", "питан", "компенсац", "билет",
        "приезд", "отъезд", "смен вахт", "ротац",
    ],
    "experience": [
        "опыт", "обучен", "стажировк", "требован", "навык", "услови",
        "без опыт", "новичк",
    ],
    "general": [
        "проект", "склад", "клиент", "работодател", "компани", "объект",
        "контакт", "менеджер", "телефон",
    ],
}

# Карта слов из возражения → тип
_OBJECTION_MAP: list[tuple[list[str], str]] = [
    (["мало", "платят", "зарплат", "деньг", "оплат", "заработ", "ставк", "рубл", "бонус", "доход"], "salary"),
    (["транспорт", "далеко", "добират", "автобус", "метро", "ехать", "дорог"], "transport"),
    (["график", "смена", "сутки", "часов", "ночн", "выходн", "режим", "рабочий"], "schedule"),
    (["оформлен", "официальн", "договор", "гпх", "трудов", "белая", "налог", "самозанят"], "contract"),
    (["вахт", "общежит", "жильё", "жилье", "проживан", "питан", "далеко живу"], "rotation"),
    (["опыт", "не умею", "новичок", "обучен", "не работал"], "experience"),
]

MAX_TZ_CHARS = 4000  # Максимум символов ТЗ в промпте (~1000 токенов)


def _detect_objection_type(objection: str) -> list[str]:
    """Определить тип возражения, вернуть список типов по убыванию релевантности."""
    obj_lower = objection.lower()
    scores: dict[str, int] = {}
    for keywords, obj_type in _OBJECTION_MAP:
        score = sum(1 for kw in keywords if kw in obj_lower)
        if score:
            scores[obj_type] = score
    if not scores:
        return ["salary", "general"]
    sorted_types = sorted(scores, key=lambda t: scores[t], reverse=True)
    if "general" not in sorted_types:
        sorted_types.append("general")
    return sorted_types


def extract_relevant_tz(tz: str, objection: str) -> str:
    """
    Умная вырезка ТЗ: возвращает только строки, релевантные для данного возражения.
    Максимум MAX_TZ_CHARS символов — чтобы не перегружать контекст модели.
    """
    if len(tz) <= MAX_TZ_CHARS:
        return tz  # маленькое ТЗ — берём целиком

    obj_types = _detect_objection_type(objection)
    # Собираем нужные ключевые слова (тип возражения + general)
    wanted_kws: list[str] = []
    for t in obj_types[:2]:  # максимум 2 типа
        wanted_kws.extend(_OBJECTION_KEYWORDS.get(t, []))
    wanted_kws.extend(_OBJECTION_KEYWORDS["general"])

    lines = tz.split("\n")
    always_include: list[str] = []  # заголовки листов + первые строки
    relevant: list[str] = []
    other: list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        if low.startswith("=== лист:") or i < 5:
            always_include.append(line)
            continue
        score = sum(1 for kw in wanted_kws if kw in low)
        if score > 0:
            relevant.append(line)
        else:
            other.append(line)

    # Собираем: заголовки + релевантные + добиваем другими если есть место
    result_lines = always_include + relevant
    result = "\n".join(result_lines)
    if len(result) < MAX_TZ_CHARS // 2:
        # Если мало данных — добавляем ещё из остальных строк
        for line in other:
            if len(result) + len(line) > MAX_TZ_CHARS:
                break
            result += "\n" + line
    elif len(result) > MAX_TZ_CHARS:
        result = result[:MAX_TZ_CHARS] + "\n[ТЗ обрезано для скорости]"

    return result


def extract_relevant_handbook(handbook: str, objection: str) -> str:
    """Извлечь ОДИН наиболее релевантный раздел учебника (для скорости)."""
    lines = handbook.split("\n")
    sections = []
    current: list[str] = []
    for line in lines:
        if line.startswith("🫠") or line.startswith("📝") or line.startswith("📖"):
            if current:
                sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))

    if not sections:
        return handbook[:2000]

    # Берём только 1 базовый раздел + 1 наиболее релевантный
    obj_lower = objection.lower()
    best = None
    best_score = 0
    for sec in sections:
        score = sum(1 for word in obj_lower.split() if len(word) > 3 and word in sec.lower())
        if score > best_score:
            best_score = score
            best = sec

    result = sections[0] if sections else ""
    if best and best != sections[0]:
        result = result + "\n\n" + best
    return result[:3000]  # ограничиваем учебник


def build_prompt(tz: str, handbook: str, objection: str) -> str:
    """Собрать промпт для генерации скрипта отработки (с умной вырезкой)."""
    tz_relevant = extract_relevant_tz(tz, objection)
    handbook_relevant = extract_relevant_handbook(handbook, objection)
    return (
        f"{RECRUITER_SYSTEM_PROMPT}\n\n"
        f"---\nТЗ ПРОЕКТА:\n{tz_relevant}\n\n"
        f"---\nУЧЕБНИК (релевантные разделы):\n{handbook_relevant}\n\n"
        f"---\nВОЗРАЖЕНИЕ КАНДИДАТА:\n{objection}\n\n"
        f"---\nДай ответ рекрутеру:"
    )


def build_chat_prompt(
    tz: str,
    handbook: str,
    objection: str,
    first_answer: str,
    chat_history: list,
    user_message: str,
) -> str:
    """Собрать промпт для уточняющего вопроса в диалоге."""
    tz_relevant = extract_relevant_tz(tz, user_message or objection)
    handbook_relevant = extract_relevant_handbook(handbook, user_message)
    history_text = "\n".join(
        f"{'Рекрутер' if m['role'] == 'user' else 'AI'}: {m['content']}"
        for m in chat_history[-6:]  # последние 6 сообщений достаточно
    )
    return (
        f"{CHAT_SYSTEM_PROMPT}\n\n"
        f"---\nТЗ ПРОЕКТА:\n{tz_relevant}\n\n"
        f"---\nУЧЕБНИК:\n{handbook_relevant}\n\n"
        f"---\nИСХОДНОЕ ВОЗРАЖЕНИЕ: {objection}\n\n"
        f"ПЕРВЫЙ ОТВЕТ AI:\n{first_answer[:500]}...\n\n"
        f"---\nДИАЛОГ:\n{history_text}\n\n"
        f"Рекрутер: {user_message}\nAI:"
    )
