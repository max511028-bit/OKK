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
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
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

AI_URL_FILE = Path(__file__).parent / "ai_url.json"


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
            headers={"User-Agent": "STH-Portal/1.0"},
        )
        with _urllib.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.post("/ai/proxy/chat")
async def proxy_ai_chat(request: Request):
    """Переслать запрос к Ollama /api/chat через прокси."""
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
            },
            method="POST",
        )
        with _urllib.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except Exception as e:
        raise HTTPException(502, f"AI proxy error: {e}")
