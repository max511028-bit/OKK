#!/usr/bin/env python3
"""AI-помощник рекрутера STH — использует локальный Ollama вместо Gemini."""

import streamlit as st
import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import re
import os
from datetime import datetime

# ── СЕКРЕТЫ ───────────────────────────────────────────────────────────────────
# Пробуем env-переменную (HuggingFace / systemd EnvironmentFile),
# затем файл рядом со скриптом (для VPS-деплоя)
_sa_env = os.getenv("GOOGLE_SERVICE_ACCOUNT_INFO")
_sa_file = os.path.join(os.path.dirname(__file__), "service_account.json")

if _sa_env:
    service_account_json = _sa_env
elif os.path.exists(_sa_file):
    with open(_sa_file, "r", encoding="utf-8") as _f:
        service_account_json = _f.read()
else:
    st.error("❌ Не найдены данные сервисного аккаунта. "
             "Положите service_account.json рядом с app.py или задайте "
             "переменную окружения GOOGLE_SERVICE_ACCOUNT_INFO.")
    st.stop()

# ── OLLAMA ────────────────────────────────────────────────────────────────────
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://178.63.16.109:11434")  # внешний Ollama IT-команды
DEFAULT_MODEL = "minimax-m2.7:cloud"

def get_ollama_models() -> list[str]:
    """Возвращает список моделей из локального Ollama."""
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        return sorted(models) if models else [DEFAULT_MODEL]
    except Exception:
        return [DEFAULT_MODEL]

def generate_with_ollama(prompt: str, model: str | None = None) -> str:
    """Отправляет prompt в Ollama и возвращает текстовый ответ."""
    if model is None:
        model = st.session_state.get("ollama_model", DEFAULT_MODEL)
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=300,
        )
        r.raise_for_status()
        return r.json()["message"]["content"]
    except requests.exceptions.Timeout:
        raise RuntimeError("⏱ Ollama не ответил за 5 минут. Попробуйте ещё раз.")
    except Exception as e:
        raise RuntimeError(f"Ошибка Ollama: {e}")

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

# ── КЛИЕНТЫ GOOGLE ────────────────────────────────────────────────────────────
@st.cache_resource
def get_clients():
    info = json.loads(service_account_json)
    legacy_creds = ServiceAccountCredentials.from_json_keyfile_dict(info, SCOPES)
    gs = gspread.authorize(legacy_creds)
    modern_creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    docs_svc = build("docs", "v1", credentials=modern_creds, cache_discovery=False)
    return gs, docs_svc

try:
    gs_client, docs_service = get_clients()
except Exception as e:
    st.error(f"Ошибка инициализации Google-клиентов: {e}")
    st.stop()

# ── ЗАГРУЗКА УЧЕБНИКА ─────────────────────────────────────────────────────────
@st.cache_data(ttl=86400)
def load_handbook() -> str:
    try:
        doc = docs_service.documents().get(documentId=HANDBOOK_DOC_ID).execute()
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
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ Не удалось загрузить учебник: {e}"

# ── ЗАГРУЗКА СПИСКА ПРОЕКТОВ ──────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_projects_list() -> dict:
    if not gs_client:
        return {}
    try:
        sh = gs_client.open_by_key(ROADMAP_SHEET_ID)
        ws = sh.worksheet("Шаблон")
        header_row = ws.row_values(1)
        try:
            tz_col_idx = header_row.index("TZ_URL") + 1
        except ValueError:
            st.error("Столбец TZ_URL не найден в заголовках!")
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
        return projects
    except Exception as e:
        st.error(f"Ошибка загрузки проектов: {e}")
        return {}

# ── ЧТЕНИЕ ТЗ ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=14400)
def read_tz_data(sheet_id: str) -> str:
    try:
        sh = gs_client.open_by_key(sheet_id)
        lines = []
        for ws in sh.worksheets():
            records = ws.get_all_values()
            if records:
                lines.append(f"=== Лист: {ws.title} ===")
                for row in records:
                    row_text = " | ".join(str(c).strip() for c in row)
                    if row_text.strip(" |"):
                        lines.append(row_text)
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка чтения ТЗ: {e}"

# ── РАБОТА С ТАБЛИЦЕЙ СТАТИСТИКИ ─────────────────────────────────────────────
def get_or_create_sheet(sh, title: str, headers: list):
    try:
        ws = sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
    if not ws.row_values(1):
        ws.append_row(headers)
    return ws

def save_feedback(project: str, objection: str, variant_title: str, feedback: str):
    try:
        sh = gs_client.open_by_key(STATS_SHEET_ID)
        ws = get_or_create_sheet(sh, "Оценки", ["Дата", "Проект", "Возражение", "Вариант", "Оценка"])
        feedback_text = "Лайк" if feedback == "👍" else "Дизлайк"
        ws.append_row([
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            project,
            objection,
            variant_title,
            feedback_text,
        ])
    except Exception as e:
        st.toast(f"Не удалось сохранить оценку: {e}", icon="⚠️")

def save_request_log(project: str, objection: str, variants: list):
    try:
        sh = gs_client.open_by_key(STATS_SHEET_ID)
        ws = get_or_create_sheet(sh, "Лог запросов", [
            "Дата", "Проект", "Возражение",
            "Вариант 1 (название)", "Вариант 1 (текст)",
            "Вариант 2 (название)", "Вариант 2 (текст)",
            "Вариант 3 (название)", "Вариант 3 (текст)",
        ])
        row = [
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            project,
            objection,
        ]
        for v in (variants + [{}, {}, {}])[:3]:
            row.append(v.get("title", ""))
            row.append(v.get("body", ""))
        ws.append_row(row)
    except Exception as e:
        st.toast(f"Не удалось записать лог: {e}", icon="⚠️")

def save_chat_log(project: str, objection: str, question: str, answer: str):
    try:
        sh = gs_client.open_by_key(STATS_SHEET_ID)
        ws = get_or_create_sheet(sh, "Диалоги", [
            "Дата", "Проект", "Исходное возражение", "Вопрос рекрутера", "Ответ AI"
        ])
        ws.append_row([
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            project,
            objection,
            question,
            answer,
        ])
    except Exception as e:
        st.toast(f"Не удалось записать диалог: {e}", icon="⚠️")

def ensure_stats_sheet(sh):
    try:
        sh.worksheet("Статистика")
        return
    except Exception:
        pass

    ws = sh.add_worksheet(title="Статистика", rows=500, cols=15)
    ws.batch_update([
        {"range": "A1", "values": [["ТОП ВОЗРАЖЕНИЙ"]]},
        {"range": "A2", "values": [["Возражение", "Кол-во"]]},
        {"range": "D1", "values": [["ОЦЕНКИ ВАРИАНТОВ"]]},
        {"range": "D2", "values": [["Вариант", "Лайков", "Дизлайков", "% лайков"]]},
        {"range": "I1", "values": [["АКТИВНОСТЬ ПО ПРОЕКТАМ"]]},
        {"range": "I2", "values": [["Проект", "Запросов"]]},
        {"range": "L1", "values": [["ЗАПРОСОВ ПО ДНЯМ"]]},
        {"range": "L2", "values": [["Дата", "Запросов"]]},
    ])

    f1 = "=IFERROR(QUERY('Лог запросов'!C:C,\"select C, count(C) where C != '' group by C order by count(C) desc label C 'Возражение', count(C) 'Кол-во'\",1),\"Нет данных\")"
    f2 = "=IFERROR(QUERY('Оценки'!D:E,\"select D, count(E) where E = 'Лайк' group by D order by count(E) desc label D 'Вариант', count(E) 'Лайков'\",1),\"Нет данных\")"
    f3 = "=IFERROR(QUERY('Оценки'!D:E,\"select count(E) where E = 'Дизлайк' group by D order by count(E) desc label count(E) 'Дизлайков'\",1),\"\")"
    f4 = "=IFERROR(ARRAYFORMULA(IF(D3:D100<>\"\",IF(E3:E100+F3:F100>0,ROUND(E3:E100/(E3:E100+F3:F100)*100,0)&\"%\",\"\"),\"\")),\"\")"
    f5 = "=IFERROR(QUERY('Лог запросов'!B:B,\"select B, count(B) where B != '' group by B order by count(B) desc label B 'Проект', count(B) 'Запросов'\",1),\"Нет данных\")"
    f6 = "=IFERROR(QUERY('Лог запросов'!A:A,\"select A, count(A) where A != '' group by A order by A desc label A 'Дата', count(A) 'Запросов'\",1),\"Нет данных\")"

    for cell, formula in [("A3",f1),("D3",f2),("F3",f3),("G3",f4),("I3",f5),("L3",f6)]:
        try:
            ws.update(cell, [[formula]], value_input_option="USER_ENTERED")
        except Exception:
            pass

# ── ПРОМПТ ────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ты — AI-помощник рекрутера. Твоя задача — помочь рекрутеру отработать возражение кандидата так, чтобы он согласился выйти на стажировку.

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
4. ПРОДАЮЩИЙ ТЕКСТ, НЕ ИНФОРМАЦИОННЫЙ. Не перечисляй условия — показывай выгоду для кандидата.
5. НЕ ДАВАЙ АЛЬТЕРНАТИВ И НЕ ПЕРЕОРИЕНТИРУЙ. Если кандидат возражает против ГПХ — не говори что есть ТК или другой проект. Если возражает против графика — не предлагай другой склад. Задача — закрыть именно это возражение на этом проекте.
6. ПЕРЕОРИЕНТАЦИЯ НА ДРУГОЙ ПРОЕКТ — вторична. Только если возражение абсолютно непреодолимо и все аргументы по данному проекту исчерпаны.
7. ЗАВЕРШАЙ КАЖДЫЙ ВАРИАНТ СКРИПТА призывом на стажировку — строго по этой формуле: "В любом случае вы ничего не теряете, поэтому предлагаю вам приехать на объект, познакомиться с бригадиром, посмотреть условия, попробовать поработать и далее принимать решение. Завтра готовы будете подъехать?"
8. ЕСЛИ НУЖНОГО ФАКТА НЕТ В ТЗ — не оставляй рекрутера с пустым ответом. Дай универсальную отработку возражения, максимально приближённую к условиям проекта из ТЗ, чтобы рекрутер мог использовать её прямо сейчас. Дополнительно добавь блок УТОЧНИТЬ с конкретным вопросом.

СТРУКТУРА ОТВЕТА — строго соблюдай формат:

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

def extract_relevant_handbook(handbook: str, objection: str) -> str:
    lines = handbook.split("\n")
    sections = []
    current = []
    for line in lines:
        if line.startswith("🫠") or line.startswith("📝") or line.startswith("📖"):
            if current:
                sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))
    base = sections[:2] if len(sections) >= 2 else sections
    obj_lower = objection.lower()
    best = None
    best_score = 0
    for sec in sections[2:]:
        score = sum(1 for word in obj_lower.split() if len(word) > 3 and word in sec.lower())
        if score > best_score:
            best_score = score
            best = sec
    result = base[:]
    if best:
        result.append(best)
    return "\n\n".join(result)

def build_prompt(tz: str, handbook: str, objection: str) -> str:
    relevant_handbook = extract_relevant_handbook(handbook, objection)
    return f"""{SYSTEM_PROMPT}

---
ТЗ ПРОЕКТА:
{tz}

---
УЧЕБНИК (релевантные разделы):
{relevant_handbook}

---
ВОЗРАЖЕНИЕ КАНДИДАТА:
{objection}

---
Дай ответ рекрутеру:"""

def build_chat_prompt(tz: str, handbook: str, objection: str, first_answer: str, chat_history: list, user_message: str) -> str:
    relevant_handbook = extract_relevant_handbook(handbook, user_message)
    history_text = "\n".join([
        f"{'Рекрутер' if m['role'] == 'user' else 'AI'}: {m['content']}"
        for m in chat_history
    ])
    return f"""{CHAT_SYSTEM_PROMPT}

---
ТЗ ПРОЕКТА:
{tz}

---
УЧЕБНИК (релевантные разделы):
{relevant_handbook}

---
ИСХОДНОЕ ВОЗРАЖЕНИЕ: {objection}

ПЕРВЫЙ ОТВЕТ AI:
{first_answer}

ИСТОРИЯ ДИАЛОГА:
{history_text}

---
НОВЫЙ ВОПРОС РЕКРУТЕРА: {user_message}

Ответ:"""

# ── ПАРСИНГ ОТВЕТА ────────────────────────────────────────────────────────────
def parse_response(text):
    result = {"analysis": "", "variants": [], "clarify": "", "tips": ""}
    import re as _re

    m = _re.search(r"АНАЛИЗ:\s*(.*?)(?=ВАРИАНТ\s*1:|$)", text, _re.S | _re.I)
    if m:
        result["analysis"] = m.group(1).strip()

    variants = _re.findall(
        r"ВАРИАНТ\s*\d+:\s*([^\n]+)\n(.*?)(?=ВАРИАНТ\s*\d+:|УТОЧНИТЬ:|РЕКОМЕНДАЦИИ:|$)",
        text, _re.S | _re.I
    )
    for title, body in variants:
        result["variants"].append({"title": title.strip(), "body": body.strip()})

    m = _re.search(r"УТОЧНИТЬ:\s*(.*?)(?=РЕКОМЕНДАЦИИ:|$)", text, _re.S | _re.I)
    if m:
        val = m.group(1).strip()
        if val:
            result["clarify"] = val

    m = _re.search(r"РЕКОМЕНДАЦИИ:\s*(.*?)$", text, _re.S | _re.I)
    if m:
        val = m.group(1).strip()
        if val:
            result["tips"] = val

    return result

# ── ИНТЕРФЕЙС ─────────────────────────────────────────────────────────────────
st.set_page_config(page_title="AI Рекрутер | СТХ", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Space Grotesk', sans-serif;
}

h1 { color: #131313 !important; font-weight: 700 !important; }
h2, h3 { color: #131313 !important; font-weight: 600 !important; }

div.stButton > button[kind="primary"] {
    background-color: #E62250 !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 8px !important;
    font-family: 'Space Grotesk', sans-serif !important;
    font-weight: 600 !important;
}
div.stButton > button[kind="primary"]:hover {
    background-color: #c41d44 !important;
}

div.stButton > button {
    border-radius: 8px !important;
    font-family: 'Space Grotesk', sans-serif !important;
    font-weight: 500 !important;
    border: 1px solid #E62250 !important;
    color: #E62250 !important;
}
div.stButton > button:hover {
    background-color: #fff0f3 !important;
}

textarea, input[type="text"] {
    border-radius: 8px !important;
    border: 1px solid #ddd !important;
    font-family: 'Space Grotesk', sans-serif !important;
}
textarea:focus, input[type="text"]:focus {
    border-color: #E62250 !important;
    box-shadow: 0 0 0 2px rgba(230,34,80,0.15) !important;
}

div[data-testid="stAlert"][kind="success"],
.stSuccess {
    background-color: #fff0f3 !important;
    border-left: 4px solid #E62250 !important;
    border-radius: 8px !important;
}

section[data-testid="stSidebar"] {
    background-color: #f8f8f8 !important;
}

hr { border-color: #f0f0f0 !important; }

details {
    border: 1px solid #f0f0f0 !important;
    border-radius: 8px !important;
}

div[data-testid="stChatInput"] textarea {
    border-color: #E62250 !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown("# 🤖 AI-помощник рекрутера")

with st.spinner("Загрузка данных..."):
    handbook_text = load_handbook()
    projects_map  = load_projects_list()

if handbook_text.startswith("⚠️"):
    st.warning(handbook_text)

project_names = sorted(projects_map.keys())

if not project_names:
    st.warning("⚠️ Список проектов пуст или ссылки не удалось извлечь.")
    st.stop()

st.success(f"✅ Загружено проектов: {len(project_names)}")

# ── САЙДБАР ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("📁 Выбор проекта")
    selected_project = st.selectbox(
        "Начните вводить название:",
        options=[""] + project_names,
        format_func=lambda x: "— выберите проект —" if x == "" else x,
    )

    if selected_project:
        tz_id = projects_map[selected_project]["tz_id"]

        if st.session_state.get("current_project") != selected_project:
            st.session_state["tz"]              = None
            st.session_state["loaded"]          = False
            st.session_state["current_project"] = selected_project
            st.session_state["last_answer"]     = None
            st.session_state["last_objection"]  = None
            st.session_state["chat_history"]    = []

        if st.button("📥 Загрузить ТЗ проекта"):
            with st.spinner("Читаю ТЗ..."):
                content = read_tz_data(tz_id)
            if content.startswith("Ошибка"):
                st.error(content)
            else:
                st.session_state["tz"]     = content
                st.session_state["loaded"] = True
                st.success("ТЗ загружено!")

        if st.session_state.get("loaded"):
            st.success("✅ ТЗ загружено")
            with st.expander("Посмотреть содержимое ТЗ"):
                st.text(st.session_state["tz"][:3000] + ("…" if len(st.session_state["tz"]) > 3000 else ""))

    st.divider()

    # ── ВЫБОР МОДЕЛИ OLLAMA ──────────────────────────────────────────────────
    st.header("🤖 Модель AI")
    available_models = get_ollama_models()
    if "ollama_model" not in st.session_state:
        st.session_state["ollama_model"] = available_models[0]

    selected_model = st.selectbox(
        "Ollama модель:",
        options=available_models,
        index=available_models.index(st.session_state["ollama_model"])
               if st.session_state["ollama_model"] in available_models else 0,
        key="model_selector",
    )
    st.session_state["ollama_model"] = selected_model

    if st.button("🔄 Обновить список моделей"):
        st.cache_data.clear()
        st.rerun()

    st.caption(f"Адрес: {OLLAMA_BASE}")

# ── ОСНОВНАЯ ОБЛАСТЬ ──────────────────────────────────────────────────────────
if not st.session_state.get("last_answer"):
    st.markdown(
        "<div style='background:#f8f8f8;border:1px solid #eee;border-radius:10px;"
        "padding:20px 24px;margin-bottom:20px;'>"
        "<div style='font-weight:700;font-size:1rem;color:#131313;margin-bottom:10px;'>"
        "🤖 AI-помощник рекрутера STH</div>"
        "<div style='color:#444;font-size:0.92rem;line-height:1.7;'>"
        "Этот инструмент разработан для рекрутеров STH, чтобы помочь в отработке возражений кандидатов. "
        "Использует локальную AI-модель — без внешних API.</div>"
        "<div style='margin-top:12px;color:#131313;font-size:0.9rem;'>"
        "<strong>Как начать работу:</strong>"
        "<ol style='margin:8px 0 0 0;padding-left:20px;line-height:2;'>"
        "<li>Выберите проект в боковой панели слева</li>"
        "<li>Нажмите «Загрузить ТЗ проекта»</li>"
        "<li>Введите возражение кандидата в поле ниже</li>"
        "<li>Нажмите «Получить ответ»</li>"
        "<li>Оцените ответы 👍 / 👎 — это помогает улучшать качество</li>"
        "<li>Если ответ неполный — задайте уточняющий вопрос в диалоге</li>"
        "</ol></div></div>",
        unsafe_allow_html=True
    )

if not selected_project:
    st.stop()

if not st.session_state.get("loaded"):
    st.info("👈 Нажмите «Загрузить ТЗ проекта» в боковой панели.")
    st.stop()

st.subheader(f"Проект: {selected_project}")
st.divider()

QUICK_TAGS = [
    "Маленькая зарплата",
    "Далеко добираться",
    "Не хочу ГПХ, хочу ТК",
    "Не хочу на вахту",
    "Подумаю / перезвоню",
    "Тяжело работать на складе",
    "Плохие отзывы на компанию",
    "Не устраивает график",
    "Хочу ежедневные выплаты",
    "Не доверяю аутсорсу",
]

if "objection_text" not in st.session_state:
    st.session_state["objection_text"] = ""

st.markdown("<div style='margin:0 0 6px 0;font-size:0.8rem;color:#888;'>Частые возражения — нажми чтобы подставить:</div>", unsafe_allow_html=True)
tag_cols = st.columns(len(QUICK_TAGS))
for idx, tag in enumerate(QUICK_TAGS):
    with tag_cols[idx]:
        if st.button(tag, key=f"tag_{idx}"):
            st.session_state["objection_text"] = tag
            st.rerun()

objection = st.text_area(
    "✍️ Возражение кандидата:",
    value=st.session_state["objection_text"],
    placeholder="Например: маленькая зарплата, далеко ехать, хочу официальное оформление...",
    height=80,
)
st.session_state["objection_text"] = objection

send = st.button("💬 Получить ответ", type="primary", disabled=not objection.strip())

if send and objection.strip():
    st.session_state["chat_history"]   = []
    st.session_state["last_answer"]    = None
    st.session_state["last_objection"] = objection.strip()
    st.session_state["feedback_given"] = {}

    with st.spinner(f"Генерирую ответ ({st.session_state['ollama_model']})..."):
        prompt = build_prompt(
            tz        = st.session_state["tz"],
            handbook  = handbook_text,
            objection = objection.strip(),
        )
        try:
            raw = generate_with_ollama(prompt)
            st.session_state["last_answer"] = raw
            parsed_for_log = parse_response(raw)
            save_request_log(selected_project, objection.strip(), parsed_for_log["variants"])
            try:
                sh = gs_client.open_by_key(STATS_SHEET_ID)
                ensure_stats_sheet(sh)
            except Exception:
                pass
        except Exception as e:
            st.error(str(e))

# ── ОТОБРАЖЕНИЕ ОТВЕТА ────────────────────────────────────────────────────────
if st.session_state.get("last_answer"):
    raw     = st.session_state["last_answer"]
    parsed  = parse_response(raw)
    obj_txt = st.session_state.get("last_objection", "")

    if "feedback_given" not in st.session_state:
        st.session_state["feedback_given"] = {}

    st.divider()

    if parsed["analysis"]:
        st.markdown(
            "<div style='background:#fff5f7;border-left:4px solid #E62250;"
            "border-radius:6px;padding:14px 18px;margin-bottom:16px;'>"
            "<div style='font-size:0.75rem;color:#E62250;font-weight:700;"
            "text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;'>"
            "🔍 Анализ возражения</div>"
            f"<div style='font-size:0.97rem;color:#131313;line-height:1.6;'>{parsed['analysis']}</div>"
            "</div>",
            unsafe_allow_html=True
        )

    variants = parsed["variants"]

    if not variants:
        st.markdown(raw)
    else:
        first = variants[0]
        body_escaped = first["body"].replace("<", "&lt;").replace(">", "&gt;")
        st.markdown(
            "<div style='border:1.5px solid #E62250;border-radius:10px;"
            "padding:18px 22px;margin-bottom:12px;'>"
            "<div style='font-size:0.75rem;color:#E62250;font-weight:700;"
            "text-transform:uppercase;letter-spacing:0.06em;margin-bottom:10px;'>"
            f"✅ Рекомендуемый ответ — {first['title']}</div>"
            f"<div style='font-size:0.97rem;color:#131313;line-height:1.7;white-space:pre-wrap;'>{body_escaped}</div>"
            "</div>",
            unsafe_allow_html=True
        )

        fb_key = "v0"
        if fb_key not in st.session_state["feedback_given"]:
            st.caption("Оцените этот вариант:")
            c1, c2, c3 = st.columns([1, 1, 8])
            with c1:
                if st.button("👍 Подходит", key="like_0"):
                    save_feedback(selected_project, obj_txt, first["title"], "👍")
                    st.session_state["feedback_given"][fb_key] = "👍"
                    st.rerun()
            with c2:
                if st.button("👎 Не подошло", key="dislike_0"):
                    save_feedback(selected_project, obj_txt, first["title"], "👎")
                    st.session_state["feedback_given"][fb_key] = "👎"
                    st.rerun()
        else:
            st.caption(f"Ваша оценка: {st.session_state['feedback_given'][fb_key]} — спасибо!")

    # ДИАЛОГ
    st.divider()
    st.markdown("### 💬 Уточнить у AI")
    st.caption("Задайте вопрос если хотите уточнить или разобрать конкретную реплику кандидата")

    for msg in st.session_state.get("chat_history", []):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    with st.form("chat_form", clear_on_submit=True):
        chat_col1, chat_col2 = st.columns([6, 1])
        with chat_col1:
            chat_input = st.text_input(
                "chat_input_field",
                placeholder="Например: как ответить если кандидат говорит что у конкурентов платят больше?",
                label_visibility="collapsed",
            )
        with chat_col2:
            chat_send = st.form_submit_button("➤ Отправить", type="primary", use_container_width=True)

    if chat_send and chat_input.strip():
        user_msg = chat_input.strip()
        st.session_state["chat_history"].append({"role": "user", "content": user_msg})
        with st.chat_message("user"):
            st.markdown(user_msg)
        with st.chat_message("assistant"):
            with st.spinner("Думаю..."):
                try:
                    chat_prompt = build_chat_prompt(
                        tz           = st.session_state["tz"],
                        handbook     = handbook_text,
                        objection    = obj_txt,
                        first_answer = raw,
                        chat_history = st.session_state["chat_history"][:-1],
                        user_message = user_msg,
                    )
                    chat_answer = generate_with_ollama(chat_prompt)
                    st.markdown(chat_answer)
                    st.session_state["chat_history"].append({"role": "assistant", "content": chat_answer})
                    save_chat_log(selected_project, obj_txt, user_msg, chat_answer)
                except Exception as e:
                    st.error(str(e))
        st.rerun()

    # ДОПОЛНИТЕЛЬНЫЕ ВАРИАНТЫ
    if variants and len(variants) > 1:
        st.divider()
        st.markdown("##### Дополнительные варианты")
        for i, v in enumerate(variants[1:], start=2):
            with st.expander(f"▶ Вариант {i}: {v['title']}"):
                st.markdown(v["body"])
                fb_key = f"v{i-1}"
                if fb_key not in st.session_state["feedback_given"]:
                    st.caption("Оцените этот вариант:")
                    c1, c2, c3 = st.columns([1, 1, 8])
                    with c1:
                        if st.button("👍 Подходит", key=f"like_{i-1}"):
                            save_feedback(selected_project, obj_txt, v["title"], "👍")
                            st.session_state["feedback_given"][fb_key] = "👍"
                            st.rerun()
                    with c2:
                        if st.button("👎 Не подошло", key=f"dislike_{i-1}"):
                            save_feedback(selected_project, obj_txt, v["title"], "👎")
                            st.session_state["feedback_given"][fb_key] = "👎"
                            st.rerun()
                else:
                    st.caption(f"Ваша оценка: {st.session_state['feedback_given'][fb_key]} — спасибо!")

    if parsed["clarify"]:
        st.divider()
        st.warning("❓ **Нужно уточнить у менеджера:**\n\n" + parsed["clarify"])

    if parsed["tips"]:
        with st.expander("💡 Рекомендации из учебника"):
            st.markdown(parsed["tips"])
