"""FastAPI backend for Задачник (task tracker).
Stores tasks, weekly review, roadmap, TZ, gantt in SQLite.
Mounted at /tasks/api/ via nginx.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

import urllib.request as _urllib
import urllib.error as _urllib_error
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

DB_PATH = os.getenv("TASKS_DB", "/var/www/okk/tasks/api/tasks.db")
SEED_PATH = Path(__file__).parent / "seed.json"

app = FastAPI(title="Задачник API", root_path="/tasks/api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://195.208.119.67", "http://localhost", "http://127.0.0.1"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            event TEXT NOT NULL,
            ts TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS lists (
            name TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        """)
        # Seed once if empty
        n = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if n == 0 and SEED_PATH.exists():
            seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
            for t in seed.get("tasks", []):
                conn.execute(
                    "INSERT OR REPLACE INTO tasks(id,title,data) VALUES(?,?,?)",
                    (t["id"], t.get("title", ""), json.dumps(t, ensure_ascii=False)),
                )
            for key in ("weekly_plan", "weekly_fact", "weekly_overdue",
                        "weekly_decisions", "roadmap", "tz", "gantt"):
                if key in seed:
                    conn.execute(
                        "INSERT OR REPLACE INTO lists(name,data) VALUES(?,?)",
                        (key, json.dumps(seed[key], ensure_ascii=False)),
                    )


class Task(BaseModel):
    id: str
    title: str
    desc: str = ""
    system: str = ""
    type: str = ""
    priority: str = "med"
    risk: str = "med"
    assignee: str = ""
    deadline: str = ""
    status: str = "Не начато"
    tz: str = "❌ Нет"
    metric: str = ""
    effect: str = ""
    blockers: str = "Нет"
    deps: str = "—"
    history: list[str] = []


def row_to_task(row: sqlite3.Row) -> dict:
    return json.loads(row["data"])


def get_list(conn, name: str) -> Any:
    row = conn.execute("SELECT data FROM lists WHERE name=?", (name,)).fetchone()
    return json.loads(row["data"]) if row else []


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/state")
def get_state():
    with db() as conn:
        rows = conn.execute("SELECT data FROM tasks ORDER BY id").fetchall()
        tasks = [json.loads(r["data"]) for r in rows]
        return {
            "tasks": tasks,
            "weekly_plan": get_list(conn, "weekly_plan"),
            "weekly_fact": get_list(conn, "weekly_fact"),
            "weekly_overdue": get_list(conn, "weekly_overdue"),
            "weekly_decisions": get_list(conn, "weekly_decisions"),
            "roadmap": get_list(conn, "roadmap"),
            "tz": get_list(conn, "tz"),
            "gantt": get_list(conn, "gantt"),
        }


@app.get("/tasks")
def list_tasks():
    with db() as conn:
        rows = conn.execute("SELECT data FROM tasks ORDER BY id").fetchall()
        return [json.loads(r["data"]) for r in rows]


@app.get("/tasks/{tid}")
def get_task(tid: str):
    with db() as conn:
        row = conn.execute("SELECT data FROM tasks WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        return json.loads(row["data"])


@app.post("/tasks")
def create_task(t: Task):
    with db() as conn:
        existing = conn.execute("SELECT id FROM tasks WHERE id=?", (t.id,)).fetchone()
        if existing:
            raise HTTPException(409, f"Task {t.id} already exists")
        conn.execute(
            "INSERT INTO tasks(id,title,data) VALUES(?,?,?)",
            (t.id, t.title, json.dumps(t.dict(), ensure_ascii=False)),
        )
        conn.execute(
            "INSERT INTO task_history(task_id,event) VALUES(?,?)",
            (t.id, f"Создана: {t.title}"),
        )
        return t.dict()


@app.put("/tasks/{tid}")
def update_task(tid: str, t: Task):
    if t.id != tid:
        raise HTTPException(400, "id mismatch")
    with db() as conn:
        existing = conn.execute("SELECT data FROM tasks WHERE id=?", (tid,)).fetchone()
        if not existing:
            raise HTTPException(404, "Task not found")
        old = json.loads(existing["data"])
        # Log diffs to history
        diffs = []
        for k in ("status", "assignee", "deadline", "priority", "risk"):
            if old.get(k) != t.dict().get(k):
                diffs.append(f"{k}: {old.get(k)} → {t.dict().get(k)}")
        conn.execute(
            "UPDATE tasks SET title=?, data=?, updated_at=datetime('now') WHERE id=?",
            (t.title, json.dumps(t.dict(), ensure_ascii=False), tid),
        )
        if diffs:
            conn.execute(
                "INSERT INTO task_history(task_id,event) VALUES(?,?)",
                (tid, "; ".join(diffs)),
            )
        return t.dict()


@app.delete("/tasks/{tid}")
def delete_task(tid: str):
    with db() as conn:
        n = conn.execute("DELETE FROM tasks WHERE id=?", (tid,)).rowcount
        if not n:
            raise HTTPException(404, "Task not found")
        conn.execute(
            "INSERT INTO task_history(task_id,event) VALUES(?,?)",
            (tid, "Удалена"),
        )
        return {"ok": True}


@app.get("/tasks/{tid}/history")
def task_history(tid: str):
    with db() as conn:
        rows = conn.execute(
            "SELECT event, ts FROM task_history WHERE task_id=? ORDER BY id DESC",
            (tid,),
        ).fetchall()
        return [{"event": r["event"], "ts": r["ts"]} for r in rows]


class ListPayload(BaseModel):
    data: list


@app.put("/lists/{name}")
def update_list(name: str, payload: ListPayload):
    if name not in ("weekly_plan", "weekly_fact", "weekly_overdue",
                    "weekly_decisions", "roadmap", "tz", "gantt"):
        raise HTTPException(400, "Unknown list name")
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO lists(name,data,updated_at) VALUES(?,?,datetime('now'))",
            (name, json.dumps(payload.data, ensure_ascii=False)),
        )
        return {"ok": True, "name": name, "count": len(payload.data)}


# ═══════════════════════════════════════════════
# KP HISTORY (saved commercial proposals from /kp/)
# ═══════════════════════════════════════════════

def _ensure_kp_table():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS kp_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            city TEXT,
            people INTEGER,
            payload TEXT NOT NULL,
            is_template INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """)


class KPItem(BaseModel):
    title: str
    city: Optional[str] = ""
    people: Optional[int] = 0
    payload: dict
    is_template: Optional[bool] = False


@app.get("/kp")
def list_kp(template: bool = False):
    _ensure_kp_table()
    with db() as conn:
        rows = conn.execute(
            "SELECT id,title,city,people,payload,is_template,created_at FROM kp_history WHERE is_template=? ORDER BY id DESC",
            (1 if template else 0,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "city": r["city"],
                "people": r["people"],
                "payload": json.loads(r["payload"]),
                "is_template": bool(r["is_template"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]


@app.post("/kp")
def save_kp(item: KPItem):
    _ensure_kp_table()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO kp_history(title,city,people,payload,is_template) VALUES(?,?,?,?,?)",
            (
                item.title,
                item.city or "",
                item.people or 0,
                json.dumps(item.payload, ensure_ascii=False),
                1 if item.is_template else 0,
            ),
        )
        return {"id": cur.lastrowid, "ok": True}


@app.delete("/kp/{kpid}")
def delete_kp(kpid: int):
    _ensure_kp_table()
    with db() as conn:
        n = conn.execute("DELETE FROM kp_history WHERE id=?", (kpid,)).rowcount
        if not n:
            raise HTTPException(404, "KP not found")
        return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True}


# ═══════════════════════════════════════════════
# AI URL RELAY — хранит текущий адрес Ollama-туннеля
# Скрипт start-ai.ps1 пишет сюда URL при запуске,
# дашборды читают его при загрузке страницы.
# ═══════════════════════════════════════════════

AI_URL_FILE    = Path(__file__).parent / "ai_url.json"
PROMPTS_FILE   = Path(__file__).parent / "ai_prompts.json"
ADMIN_PASSWORD = "028511"

DEFAULT_PROMPTS: dict = {
    "okk": (
        "Ты — AI-аналитик системы контроля качества (ОКК) колл-центра по подбору складского персонала.\n\n"
        "Роль: анализируешь данные прослушки звонков, проверок МПП и ОРП, опросов новичков. "
        "Помогаешь руководителям принимать решения на основе цифр.\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском языке. Никаких иероглифов и символов других языков\n"
        "- Используй ТОЛЬКО цифры из предоставленных данных. Никогда не придумывай\n"
        "- Если данных недостаточно — прямо скажи об этом\n"
        "- Не повторяй вопрос пользователя в ответе\n"
        "- Не пиши вступлений типа \"Конечно!\", \"Отличный вопрос!\" и т.д.\n\n"
        "Формат ответа:\n"
        "- Длина: 5-10 предложений максимум\n"
        "- Структура: Факты (конкретные цифры) → Вывод (что это значит) → Рекомендация (что делать)\n"
        "- Используй **жирный** для имён сотрудников и ключевых цифр\n"
        "- Списки — когда сравниваешь 3+ объекта\n"
        "- Таблицы в markdown — когда сравниваешь дивизионы или периоды\n\n"
        "Нормы для оценки:\n"
        "- Прослушка: норма ≥85%, ниже 75% — критично\n"
        "- МПП/ОРП: норма ≥80 баллов, ниже 60 — зона риска\n"
        "- Опросы: CSAT норма ≥4.5, NPS норма ≥4.0\n"
        "- Всегда указывай период (месяц) анализируемых данных"
    ),
    "wb": (
        "Ты — AI-аналитик дашборда WB Аутсорсинг. Анализируешь данные по выработке, штрафам и операциям складских сотрудников.\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском языке\n"
        "- Используй ТОЛЬКО цифры из предоставленных данных\n"
        "- Если данных нет — прямо скажи\n"
        "- Без вступлений типа \"Конечно!\", \"Отличный вопрос!\"\n\n"
        "Формат: Факты (цифры) → Вывод → Рекомендация. Максимум 8 предложений.\n"
        "Используй **жирный** для имён и ключевых цифр."
    ),
    "finance": (
        "Ты — AI-аналитик финансового дашборда STH Group. Анализируешь рентабельность, P&L, тренды по проектам, городам и клиентам.\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском языке\n"
        "- Используй данные из контекста. Если данных нет — скажи об этом\n"
        "- Без вступлений типа \"Конечно!\", \"Отличный вопрос!\"\n"
        "- Формат: Факты → Вывод → Рекомендация. Максимум 8 предложений.\n"
        "- Используй **жирный** для названий проектов и ключевых цифр"
    ),
    "kp": (
        "Ты — ассистент по расчёту коммерческих предложений (КП) для аутсорсинга складского персонала STH.\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском языке\n"
        "- Объясняй формулы и расчёты простым языком\n"
        "- Давай практические советы по оптимизации стоимости\n"
        "- Без вступлений типа \"Конечно!\", \"Отличный вопрос!\"\n"
        "- Формат: чёткий, структурированный. Максимум 8 предложений."
    ),
    "hr-game": (
        "Ты — HR-ассистент компании STH по найму складского персонала. Отвечаешь на вопросы по процессам подбора, оформления и адаптации сотрудников.\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском языке\n"
        "- Давай конкретные практические советы\n"
        "- Без вступлений типа \"Конечно!\", \"Отличный вопрос!\"\n"
        "- Формат: структурированный, с примерами. Максимум 8 предложений."
    ),
    "recruiter": (
        "Ты — AI-помощник рекрутера. Твоя задача — помочь рекрутеру отработать возражение кандидата так, чтобы он согласился выйти на стажировку.\n\n"
        "ПРАВИЛА:\n"
        "1. Отвечай ТОЛЬКО на русском языке.\n"
        "2. Факты (зарплата, график, транспорт, оформление) бери ТОЛЬКО из ТЗ проекта. Не придумывай.\n"
        "3. Текст должен быть ПРОДАЮЩИМ, не информационным — показывай выгоду для кандидата.\n"
        "4. НЕ предлагай альтернативных проектов — закрывай конкретное возражение.\n"
        "5. ЗАВЕРШАЙ каждый вариант призывом: \"В любом случае вы ничего не теряете, поэтому предлагаю вам приехать на объект...\"\n\n"
        "ФОРМАТ ОТВЕТА (строго):\n"
        "АНАЛИЗ: [тип возражения]\n"
        "ВАРИАНТ 1: [название] / [скрипт] / [призыв]\n"
        "ВАРИАНТ 2: [название] / [скрипт] / [призыв]\n"
        "ВАРИАНТ 3: [название, если уместен] / [скрипт] / [призыв]\n"
        "УТОЧНИТЬ: [только если факта нет в ТЗ]\n"
        "РЕКОМЕНДАЦИИ: [не более 2 техник из учебника, если уместно]"
    ),
}


def _load_prompts() -> dict:
    """Загрузить промпты: сохранённые + дефолты для отсутствующих ключей."""
    if PROMPTS_FILE.exists():
        try:
            saved = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_PROMPTS, **saved}
        except Exception:
            pass
    return dict(DEFAULT_PROMPTS)


class AIUrlPayload(BaseModel):
    url: Optional[str] = None


@app.get("/ai/url")
def get_ai_url():
    """Вернуть текущий адрес Ollama (или null если оффлайн)."""
    if AI_URL_FILE.exists():
        try:
            return json.loads(AI_URL_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"url": None}


@app.post("/ai/url")
def set_ai_url(payload: AIUrlPayload):
    """Сохранить новый адрес Ollama (вызывается скриптом start-ai.ps1)."""
    AI_URL_FILE.write_text(
        json.dumps({"url": payload.url}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"ok": True, "url": payload.url}


# ═══════════════════════════════════════════════
# AI PROMPTS — хранение системных промптов дашбордов
# ═══════════════════════════════════════════════

@app.get("/ai/prompt/{dashboard}")
def get_prompt(dashboard: str):
    """Вернуть системный промпт для указанного дашборда (публичный)."""
    prompts = _load_prompts()
    if dashboard not in prompts:
        raise HTTPException(404, f"Unknown dashboard: {dashboard}")
    return {"dashboard": dashboard, "prompt": prompts[dashboard]}


@app.get("/admin/prompts")
def admin_get_prompts(password: str = ""):
    """Вернуть все промпты (требует пароль)."""
    if password != ADMIN_PASSWORD:
        raise HTTPException(403, "Неверный пароль")
    return _load_prompts()


@app.post("/admin/prompts")
async def admin_save_prompts(request: Request, password: str = ""):
    """Сохранить все промпты (требует пароль)."""
    if password != ADMIN_PASSWORD:
        raise HTTPException(403, "Неверный пароль")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Ожидается JSON-объект")
    # Сохранить все известные ключи (включая recruiter)
    to_save = {k: str(v) for k, v in body.items() if k in DEFAULT_PROMPTS}
    PROMPTS_FILE.write_text(json.dumps(to_save, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "saved": list(to_save.keys())}


# ═══════════════════════════════════════════════
# AI PROXY — VPS проксирует запросы к Ollama-туннелю.
# Браузер обращается к тому же серверу (без CORS),
# VPS сам запрашивает Ollama через туннель.
# ═══════════════════════════════════════════════

@app.get("/ai/proxy/tags")
def proxy_ai_tags():
    """Вернуть список моделей Ollama через прокси."""
    data = get_ai_url()
    base = data.get("url") if isinstance(data, dict) else None
    if not base:
        return {"models": [], "offline": True}
    try:
        req = _urllib.Request(
            f"{base}/api/tags",
            headers={
                "User-Agent": "STH-Portal/1.0",
                "ngrok-skip-browser-warning": "true",
            },
        )
        with _urllib.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except _urllib_error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = ""
        return {"models": [], "error": str(e), "url": base, "body": body}
    except Exception as e:
        return {"models": [], "error": str(e), "url": base}


@app.post("/ai/proxy/chat")
async def proxy_ai_chat(request: Request):
    """Переслать запрос к Ollama /api/chat через прокси (non-streaming)."""
    data = get_ai_url()
    base = data.get("url") if isinstance(data, dict) else None
    if not base:
        raise HTTPException(503, "AI is offline")
    body = await request.body()
    try:
        req = _urllib.Request(
            f"{base}/api/chat",
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "STH-Portal/1.0",
                "ngrok-skip-browser-warning": "true",
            },
            method="POST",
        )
        with _urllib.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except Exception as e:
        raise HTTPException(502, f"AI proxy error: {e}")


@app.post("/ai/proxy/chat/stream")
async def proxy_ai_chat_stream(request: Request):
    """Стриминг-прокси к Ollama /api/chat. Возвращает NDJSON (stream:true)."""
    data = get_ai_url()
    base = data.get("url") if isinstance(data, dict) else None
    if not base:
        raise HTTPException(503, "AI is offline")

    raw_body = await request.body()
    # Принудительно включить stream:true в теле запроса
    try:
        body_json = json.loads(raw_body)
        body_json["stream"] = True
        body_bytes = json.dumps(body_json, ensure_ascii=False).encode()
    except Exception:
        body_bytes = raw_body

    def generate():
        try:
            req = _urllib.Request(
                f"{base}/api/chat",
                data=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "STH-Portal/1.0",
                    "ngrok-skip-browser-warning": "true",
                },
                method="POST",
            )
            with _urllib.urlopen(req, timeout=300) as resp:
                for line in resp:
                    if line:
                        yield line
        except Exception as e:
            yield json.dumps({"error": str(e)}, ensure_ascii=False).encode() + b"\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


# ═══════════════════════════════════════════════
# RECRUITER API — AI-помощник рекрутера
# ═══════════════════════════════════════════════

def _recruiter_logic():
    """Ленивая загрузка модуля recruiter_logic."""
    import importlib.util, sys
    mod_path = Path(__file__).parent / "recruiter_logic.py"
    if "recruiter_logic" in sys.modules:
        return sys.modules["recruiter_logic"]
    spec = importlib.util.spec_from_file_location("recruiter_logic", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["recruiter_logic"] = mod
    spec.loader.exec_module(mod)
    return mod


@app.get("/recruiter/projects")
def recruiter_projects():
    """Список проектов из Google Sheets (Roadmap)."""
    try:
        rl = _recruiter_logic()
        projects = rl.load_projects_list()
        return {"projects": list(projects.keys()), "ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/recruiter/tz")
def recruiter_load_tz(project: str = ""):
    """Загрузить ТЗ проекта из Google Sheets."""
    try:
        rl = _recruiter_logic()
        projects = rl.load_projects_list()
        if project not in projects:
            raise HTTPException(404, f"Проект не найден: {project}")
        tz_id = projects[project]["tz_id"]
        tz_text = rl.read_tz_data(tz_id)
        return {"tz": tz_text, "ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


class RecruiterAnswerReq(BaseModel):
    project: str
    objection: str
    tz: str
    model: str = "qwen3:8b"


@app.post("/recruiter/answer/stream")
async def recruiter_answer_stream(req: RecruiterAnswerReq):
    """Стриминг ответа на возражение кандидата."""
    data = get_ai_url()
    base = data.get("url") if isinstance(data, dict) else None
    if not base:
        raise HTTPException(503, "AI is offline")

    rl = _recruiter_logic()
    handbook = rl.load_handbook()
    prompt = rl.build_prompt(req.tz, handbook, req.objection)

    payload = json.dumps({
        "model": req.model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "stream": True,
        "think": False,
    }, ensure_ascii=False).encode()

    def generate():
        try:
            http_req = _urllib.Request(
                f"{base}/api/chat",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "STH-Portal/1.0",
                    "ngrok-skip-browser-warning": "true",
                },
                method="POST",
            )
            with _urllib.urlopen(http_req, timeout=300) as resp:
                for line in resp:
                    if line:
                        yield line
        except Exception as e:
            yield json.dumps({"error": str(e)}, ensure_ascii=False).encode() + b"\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


class RecruiterChatReq(BaseModel):
    project: str
    objection: str
    tz: str
    first_answer: str
    history: list
    message: str
    model: str = "qwen3:8b"


@app.post("/recruiter/chat/stream")
async def recruiter_chat_stream(req: RecruiterChatReq):
    """Стриминг уточняющего вопроса в диалоге рекрутера."""
    data = get_ai_url()
    base = data.get("url") if isinstance(data, dict) else None
    if not base:
        raise HTTPException(503, "AI is offline")

    rl = _recruiter_logic()
    handbook = rl.load_handbook()
    prompt = rl.build_chat_prompt(
        req.tz, handbook, req.objection,
        req.first_answer, req.history, req.message
    )

    payload = json.dumps({
        "model": req.model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "think": False,
    }, ensure_ascii=False).encode()

    def generate():
        try:
            http_req = _urllib.Request(
                f"{base}/api/chat",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "STH-Portal/1.0",
                    "ngrok-skip-browser-warning": "true",
                },
                method="POST",
            )
            with _urllib.urlopen(http_req, timeout=300) as resp:
                for line in resp:
                    if line:
                        yield line
        except Exception as e:
            yield json.dumps({"error": str(e)}, ensure_ascii=False).encode() + b"\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


class FeedbackReq(BaseModel):
    project: str
    objection: str
    variant_title: str
    feedback: str  # "like" | "dislike"


@app.post("/recruiter/feedback")
async def recruiter_feedback(req: FeedbackReq):
    """Сохранить оценку варианта скрипта."""
    try:
        rl = _recruiter_logic()
        rl.save_feedback(req.project, req.objection, req.variant_title, req.feedback)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class LogReq(BaseModel):
    project: str
    objection: str
    variants: list  # [{"title": ..., "body": ...}]


@app.post("/recruiter/log")
async def recruiter_log(req: LogReq):
    """Записать лог запроса в Google Sheets."""
    try:
        rl = _recruiter_logic()
        rl.save_request_log(req.project, req.objection, req.variants)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class ChatLogReq(BaseModel):
    project: str
    objection: str
    question: str
    answer: str


@app.post("/recruiter/chat-log")
async def recruiter_chat_log(req: ChatLogReq):
    """Записать диалог рекрутера в Google Sheets."""
    try:
        rl = _recruiter_logic()
        rl.save_chat_log(req.project, req.objection, req.question, req.answer)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/recruiter/status")
def recruiter_status():
    """Проверить доступность Google API."""
    try:
        rl = _recruiter_logic()
        ready = rl.clients_ready()
        return {
            "ok": ready,
            "error": rl._clients_error if not ready else None,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
