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
from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

DB_PATH = os.getenv("TASKS_DB", "/var/www/okk/tasks/api/tasks.db")
SEED_PATH = Path(__file__).parent / "seed.json"

app = FastAPI(title="Задачник API", root_path="/tasks/api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://portalsth.ru", "https://www.portalsth.ru",
        "http://portalsth.ru", "http://www.portalsth.ru",
        "http://195.208.119.67",
        "http://localhost", "http://127.0.0.1",
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Auth-Token"],
)


def require_portal_token(x_auth_token: str = Header(default="")):
    """Защита write-эндпоинтов: требует валидный токен portal-сессии в X-Auth-Token."""
    # _verify_token / _make_token определены ниже — используем lazy-обращение через globals().
    verify = globals().get("_verify_token")
    if not verify or not verify(x_auth_token, "portal"):
        raise HTTPException(status_code=401, detail="Auth required")
    return True


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
        CREATE TABLE IF NOT EXISTS ai_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            dashboard TEXT,
            user_token_short TEXT,
            question TEXT,
            response TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            latency_ms INTEGER,
            cache_hit INTEGER DEFAULT 0,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ai_logs_ts ON ai_logs(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_ai_logs_dashboard ON ai_logs(dashboard, ts DESC);

        CREATE TABLE IF NOT EXISTS ai_test_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            ts TEXT DEFAULT (datetime('now')),
            dashboard TEXT,
            question TEXT,
            ok INTEGER,
            latency_ms INTEGER,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            response_snippet TEXT,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ai_test_runs_ts ON ai_test_runs(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_ai_test_runs_run ON ai_test_runs(run_id);

        -- Универсальное JSON-хранилище для дашбордов month / sales.
        -- name — логический ключ (e.g. 'month/data', 'month/norms', 'month/actions',
        --                          'sales/orders', 'sales/clients').
        CREATE TABLE IF NOT EXISTS dash_blobs (
            name TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        """)
        # Soft-delete migration (idempotent)
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN deleted_at TEXT")
        except sqlite3.OperationalError:
            pass  # колонка уже есть
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_deleted ON tasks(deleted_at)")
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

        # Seed dash_blobs из файлов /var/www/okk/<dashboard>/seed/*.json при пустой таблице
        _seed_dash_blobs(conn)


# ---------- dash_blobs (month / sales) ----------
DASH_SEED_DIR = Path(__file__).resolve().parents[2]  # /var/www/okk


def _seed_dash_blobs(conn) -> None:
    """Заполняет dash_blobs из seed/*.json при первом запуске."""
    mapping = {
        "month/data": DASH_SEED_DIR / "month" / "seed" / "monthly-reports.json",
        "month/norms": DASH_SEED_DIR / "month" / "seed" / "norms.json",
        "month/actions": DASH_SEED_DIR / "month" / "seed" / "actions.json",
        "sales/data": DASH_SEED_DIR / "sales" / "seed" / "sales.json",
    }
    for name, path in mapping.items():
        exists = conn.execute("SELECT 1 FROM dash_blobs WHERE name=?", (name,)).fetchone()
        if exists:
            continue
        if not path.exists():
            continue
        try:
            payload = path.read_text(encoding="utf-8")
            # валидируем что это JSON
            json.loads(payload)
            conn.execute(
                "INSERT INTO dash_blobs(name,data) VALUES(?,?)",
                (name, payload),
            )
        except Exception:
            pass


def _blob_get(name: str):
    with db() as conn:
        row = conn.execute("SELECT data FROM dash_blobs WHERE name=?", (name,)).fetchone()
        if not row:
            raise HTTPException(404, f"Blob '{name}' not found")
        return json.loads(row["data"])


def _blob_put(name: str, payload: Any) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO dash_blobs(name,data,updated_at) VALUES(?,?,datetime('now')) "
            "ON CONFLICT(name) DO UPDATE SET data=excluded.data, updated_at=datetime('now')",
            (name, json.dumps(payload, ensure_ascii=False)),
        )


# --- month endpoints ---
@app.get("/month/data")
def month_get_data():
    return _blob_get("month/data")


@app.put("/month/data", dependencies=[Depends(require_portal_token)])
async def month_put_data(request: Request):
    body = await request.json()
    _blob_put("month/data", body)
    return {"ok": True}


@app.get("/month/norms")
def month_get_norms():
    return _blob_get("month/norms")


@app.put("/month/norms", dependencies=[Depends(require_portal_token)])
async def month_put_norms(request: Request):
    body = await request.json()
    _blob_put("month/norms", body)
    return {"ok": True}


@app.get("/month/actions")
def month_get_actions():
    return _blob_get("month/actions")


@app.put("/month/actions", dependencies=[Depends(require_portal_token)])
async def month_put_actions(request: Request):
    body = await request.json()
    _blob_put("month/actions", body)
    return {"ok": True}


# --- sales endpoints (Dexie-like collections) ---
def _sales_collection(name: str):
    """Возвращает массив объектов коллекции sales (orders, clients, и т.д.)."""
    try:
        return _blob_get(f"sales/{name}")
    except HTTPException:
        return []


@app.get("/sales/{collection}")
def sales_get_collection(collection: str):
    return _sales_collection(collection)


@app.put("/sales/{collection}", dependencies=[Depends(require_portal_token)])
async def sales_put_collection(collection: str, request: Request):
    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(400, "Sales collection must be a JSON array")
    _blob_put(f"sales/{collection}", body)
    return {"ok": True}


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
        rows = conn.execute("SELECT data FROM tasks WHERE deleted_at IS NULL ORDER BY id").fetchall()
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
def list_tasks(include_deleted: bool = False):
    with db() as conn:
        if include_deleted:
            rows = conn.execute("SELECT data FROM tasks ORDER BY id").fetchall()
        else:
            rows = conn.execute("SELECT data FROM tasks WHERE deleted_at IS NULL ORDER BY id").fetchall()
        return [json.loads(r["data"]) for r in rows]


@app.get("/tasks/trash")
def list_trash():
    """Список мягко-удалённых задач (для UI «корзина»)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, deleted_at FROM tasks WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        ).fetchall()
        return [{"id": r["id"], "title": r["title"], "deleted_at": r["deleted_at"]} for r in rows]


@app.get("/tasks/{tid}")
def get_task(tid: str):
    with db() as conn:
        row = conn.execute("SELECT data FROM tasks WHERE id=? AND deleted_at IS NULL", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        return json.loads(row["data"])


@app.post("/tasks", dependencies=[Depends(require_portal_token)])
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


@app.put("/tasks/{tid}", dependencies=[Depends(require_portal_token)])
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


@app.delete("/tasks/{tid}", dependencies=[Depends(require_portal_token)])
def delete_task(tid: str, hard: bool = False):
    """Удалить задачу. По умолчанию — мягко (deleted_at=сейчас, восстановимо 7 дней).
    hard=true — физическое удаление (для админского purge)."""
    with db() as conn:
        if hard:
            n = conn.execute("DELETE FROM tasks WHERE id=?", (tid,)).rowcount
            if not n:
                raise HTTPException(404, "Task not found")
            conn.execute("INSERT INTO task_history(task_id,event) VALUES(?,?)", (tid, "Удалена окончательно"))
        else:
            existing = conn.execute("SELECT id FROM tasks WHERE id=? AND deleted_at IS NULL", (tid,)).fetchone()
            if not existing:
                raise HTTPException(404, "Task not found")
            conn.execute("UPDATE tasks SET deleted_at=datetime('now') WHERE id=?", (tid,))
            conn.execute("INSERT INTO task_history(task_id,event) VALUES(?,?)", (tid, "Удалена (в корзину на 7 дней)"))
        return {"ok": True, "soft": not hard}


@app.post("/tasks/{tid}/restore", dependencies=[Depends(require_portal_token)])
def restore_task(tid: str):
    """Восстановить мягко-удалённую задачу."""
    with db() as conn:
        existing = conn.execute("SELECT id FROM tasks WHERE id=? AND deleted_at IS NOT NULL", (tid,)).fetchone()
        if not existing:
            raise HTTPException(404, "Task not in trash")
        conn.execute("UPDATE tasks SET deleted_at=NULL WHERE id=?", (tid,))
        conn.execute("INSERT INTO task_history(task_id,event) VALUES(?,?)", (tid, "Восстановлена из корзины"))
        return {"ok": True}


@app.post("/admin/tasks/purge-trash")
def purge_trash(request: Request, days: int = 7):
    """Физически удалить задачи, лежавшие в корзине больше N дней (по умолчанию 7).

    Доступно localhost (для cron) и админу (для ручного запуска)."""
    if not _is_local_request(request):
        token = request.headers.get("X-Admin-Token") or request.headers.get("X-Auth-Token") or ""
        if not _verify_token(token, "admin"):
            raise HTTPException(403, "Only localhost or admin")
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM tasks WHERE deleted_at IS NOT NULL AND deleted_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        return {"ok": True, "purged": cur.rowcount}


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


@app.put("/lists/{name}", dependencies=[Depends(require_portal_token)])
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


@app.post("/kp", dependencies=[Depends(require_portal_token)])
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


@app.delete("/kp/{kpid}", dependencies=[Depends(require_portal_token)])
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
# OKK — список вкладок в каждом из 5 источников.
# Браузер тянет CSV-данные напрямую через gviz (быстро, без CORS),
# но для ДИНАМИЧЕСКОГО подхвата новых вкладок (новый месяц, новый
# чек-лист) нужен список имён — а его в gviz нет. Поэтому здесь
# качаем xlsx-экспорт и парсим имена вкладок из workbook.xml.
# Кэш 5 минут в памяти процесса.
# ═══════════════════════════════════════════════

OKK_FILES = {
    "kc":    "1vxEUq2m92T3oPl25vuxKUxTDDZO5hZJHZlC_88xR-8w",   # 1.Прослушка КЦ
    "stats": "1i94OV_2e9B5CGIYBFkWN3W8SLfT8R93598dyoYEoe08",   # 2.Статистика КЦ
    "mpp":   "1wI_MIdxs77znU54lasXdInj2Wf6KyYoMXvakdTlNyAg",   # 3.Тайные МПП
    "orp":   "1dV5tAXBbs7DLSTw1PBUaW800NvUm9W7TvLe4O4yogy0",   # 4.Тайные ОРП
    "rp":    "13eIDsffRUYRyW6Df6-W4dpUzxMehM-yqInOTTnIfZnE",   # 5.Опросы РП
}

_OKK_TABS_CACHE: dict[str, tuple[float, list[str]]] = {}
_OKK_TABS_TTL = 300  # секунд


@app.get("/okk/files")
def okk_files():
    """Лёгкий справочник: ключ → google-id. Браузер использует чтобы построить URL gviz."""
    return OKK_FILES


@app.get("/okk/tabs/{key}")
def okk_tabs(key: str):
    """Список имён вкладок в одной из 5 OKK-таблиц. Кэш 5 минут."""
    import time as _t
    sid = OKK_FILES.get(key)
    if not sid:
        raise HTTPException(404, f"unknown OKK file key: {key}")
    cached = _OKK_TABS_CACHE.get(key)
    now = _t.time()
    if cached and (now - cached[0]) < _OKK_TABS_TTL:
        return {"key": key, "id": sid, "tabs": cached[1], "cached": True}
    import urllib.request as _u, zipfile, io as _io, re as _re
    url = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx"
    try:
        req = _u.Request(url, headers={"User-Agent": "STH-Portal/1.0"})
        with _u.urlopen(req, timeout=20) as resp:
            data = resp.read()
        with zipfile.ZipFile(_io.BytesIO(data)) as z:
            wb_xml = z.read("xl/workbook.xml").decode("utf-8", errors="replace")
        names = _re.findall(r'<sheet[^/]*name="([^"]+)"', wb_xml)
        # xlsx-экспорт режет имена до 31 символа. Если у тебя в Google имя длиннее —
        # gviz по укороченному имени всё равно НЕ найдёт лист. Но 99% случаев совпадает.
        _OKK_TABS_CACHE[key] = (now, names)
        return {"key": key, "id": sid, "tabs": names, "cached": False}
    except Exception as e:
        # Если бэкенд упал — отдадим пустой список, фронт грейсфулли пропустит автодискавер
        return {"key": key, "id": sid, "tabs": [], "error": str(e)[:200]}


# ═══════════════════════════════════════════════
# AI URL RELAY — хранит текущий адрес Ollama-туннеля
# Скрипт start-ai.ps1 пишет сюда URL при запуске,
# дашборды читают его при загрузке страницы.
# ═══════════════════════════════════════════════

AI_URL_FILE    = Path(__file__).parent / "ai_url.json"
PROMPTS_FILE   = Path(__file__).parent / "ai_prompts.json"
# Пароли читаются из env (на VPS: /etc/sth-portal.env, подключается через
# EnvironmentFile= в systemd-юните tasks-api.service). Хардкоженные fallback'и
# удалены — если env не задан, auth просто не сработает (это безопасное поведение).
ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD")
PORTAL_PASSWORD = os.getenv("PORTAL_PASSWORD")
if not ADMIN_PASSWORD or not PORTAL_PASSWORD:
    import logging
    logging.warning("ADMIN_PASSWORD / PORTAL_PASSWORD не заданы в env — авторизация будет отклонять всех")

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
        "- Структура: Факты (конкретные цифры) → Вывод (что это значит) → Рекомендация (что делать)\n"
        "- Длина: ровно столько, сколько нужно для пользы. Обычно 4-8 предложений, без воды\n"
        "- Используй **жирный** для имён сотрудников и ключевых цифр\n"
        "- Списки — когда сравниваешь 3+ объекта\n"
        "- Markdown-таблицы — когда сравниваешь дивизионы или периоды\n\n"
        "Нормы для оценки:\n"
        "- Прослушка: норма ≥85%, ниже 75% — критично\n"
        "- МПП/ОРП: норма ≥80 баллов, ниже 60 — зона риска\n"
        "- Опросы: CSAT норма ≥4.5, NPS норма ≥4.0\n"
        "- Всегда указывай период (месяц) анализируемых данных"
    ),
    "wb": (
        "Ты — AI-аналитик дашборда WB Аутсорсинг. Анализируешь выработку, штрафы и операции складских сотрудников проекта Wildberries.\n\n"
        "Роль: помогаешь руководителю находить лидеров, отстающих и проблемные операции, чтобы вовремя реагировать.\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском языке. Без иероглифов\n"
        "- Используй ТОЛЬКО цифры из предоставленных данных. Никогда не придумывай\n"
        "- Если данных нет — прямо скажи об этом\n"
        "- Не повторяй вопрос, не пиши \"Конечно!\", \"Отличный вопрос!\"\n"
        "- Отвечай коротко и по делу, без воды\n\n"
        "Формат ответа:\n"
        "- Структура: Факты (конкретные цифры) → Вывод → Рекомендация\n"
        "- Длина: ровно столько, сколько нужно для пользы. Обычно 4-8 предложений\n"
        "- **Жирный** — для имён сотрудников и ключевых цифр\n"
        "- Списки — когда сравниваешь 3+ человека или операции\n"
        "- Markdown-таблицы — когда сравниваешь периоды или операции по разным метрикам\n\n"
        "Нормы для оценки (если в контексте нет своих):\n"
        "- Выработка: норма ≥100% плана, ниже 80% — зона риска\n"
        "- Штрафы: 0 — норма; >3 штрафов за период — критично\n"
        "- Операции: смотри на стабильность — резкое падение >20% м/м означает проблему\n"
        "- Всегда указывай период (месяц/неделю) данных, по которым делаешь вывод"
    ),
    "finance": (
        "Ты — AI-аналитик финансового дашборда STH Group. Анализируешь рентабельность, P&L, динамику по проектам, городам и клиентам.\n\n"
        "Роль: помогаешь руководству видеть, где компания зарабатывает, где теряет, и что менять в приоритетах.\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском языке. Без иероглифов\n"
        "- Используй ТОЛЬКО цифры из контекста. Если данных не видишь — прямо скажи и попроси уточнить вопрос\n"
        "- Все суммы — в рублях, если в контексте не указано иначе\n"
        "- Не повторяй вопрос, не пиши \"Конечно!\", \"Отличный вопрос!\"\n"
        "- Отвечай коротко и по делу, без воды\n\n"
        "Формат ответа:\n"
        "- Структура: Факты (цифры) → Вывод (что это значит) → Рекомендация (что делать)\n"
        "- Длина: ровно столько, сколько нужно для пользы. Обычно 4-8 предложений\n"
        "- **Жирный** — для названий проектов/клиентов и ключевых цифр\n"
        "- Markdown-таблицы — когда сравниваешь проекты, города или периоды\n\n"
        "Термины и нормы:\n"
        "- Маржа = (Выручка − Себестоимость) / Выручка × 100%\n"
        "- Маржа проекта: норма ≥15%, ниже 8% — зона риска, отрицательная — критично\n"
        "- При анализе тренда — обязательно сравни последний месяц с предыдущим и с тем же месяцем год назад, если данные есть\n"
        "- Всегда указывай период анализа"
    ),
    "kp": (
        "Ты — ассистент по коммерческим предложениям (КП) для аутсорсинга складского персонала STH.\n\n"
        "Роль: помогаешь менеджеру быстро понять структуру расчёта, найти, где можно оптимизировать стоимость, и подсветить риски.\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском языке. Без иероглифов\n"
        "- Используй ТОЛЬКО цифры из формы и расчёта. Не придумывай ставки\n"
        "- Не повторяй вопрос, не пиши \"Конечно!\", \"Отличный вопрос!\"\n"
        "- Объясняй формулы простым языком, без академизма\n"
        "- Отвечай коротко и по делу, без воды\n\n"
        "Формат ответа:\n"
        "- Структура: Что в расчёте сейчас → Что можно улучшить → Что насторожить\n"
        "- Длина: 4-8 предложений\n"
        "- **Жирный** — для ключевых цифр (ставка, маржа, итог)\n"
        "- Markdown-таблицы — когда сравниваешь варианты КП или раскладываешь стоимость по статьям\n\n"
        "Структура типового КП STH:\n"
        "- Фонд оплаты труда (ФОТ) = ставка × часы × кол-во людей\n"
        "- Налоги и страховые = ФОТ × коэффициент (обычно 30-43% в зависимости от формы оформления)\n"
        "- Накладные расходы = СИЗ, форма, логистика, проживание (если есть)\n"
        "- Маржа компании = наценка сверху, норма ≥15%\n"
        "- Итог клиенту = ФОТ + налоги + накладные + маржа\n\n"
        "Красные линии:\n"
        "- Маржа <10% — указывай, что предложение рискованное\n"
        "- Ставка ниже МРОТ региона — критично, нельзя\n"
        "- Часы >168 в месяц на человека — нарушение ТК"
    ),
    "hr-game": (
        "Ты — AI-наставник по найму складского персонала в STH. Тренируешь рекрутера на типовых ситуациях: возражения кандидата, тонкости оформления, адаптация в первые дни.\n\n"
        "Роль: ты не просто отвечаешь на вопросы — ты разбираешь действия рекрутера, указываешь ошибки и подсказываешь, как сделать лучше.\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском языке. Без иероглифов\n"
        "- Опирайся на стандарты найма STH и здравый смысл. Если вопрос не связан с наймом — мягко верни в тему\n"
        "- Не повторяй вопрос, не пиши \"Конечно!\", \"Отличный вопрос!\"\n"
        "- Тон — наставник: спокойный, конкретный, без морализаторства\n"
        "- Отвечай коротко и по делу, без воды\n\n"
        "Формат ответа:\n"
        "- Структура: Что сделано хорошо → Что улучшить → Что сказать кандидату дословно (если уместно)\n"
        "- Если рекрутер описал ситуацию — сначала кратко её квалифицируй (тип возражения / этап воронки), потом давай разбор\n"
        "- Длина: 4-8 предложений\n"
        "- **Жирный** — для ключевых техник и фраз, которые стоит запомнить\n"
        "- Списки — когда даёшь 3+ варианта формулировок\n\n"
        "Принципы найма STH:\n"
        "- Главная цель звонка — довести кандидата до выхода на стажировку, а не до подписания\n"
        "- Факты (зарплата, график, транспорт) — только из ТЗ проекта\n"
        "- Возражения отрабатывай, не игнорируй — но не уговаривай дольше 2 итераций\n"
        "- Если кандидат явно не подходит (возраст, документы, мотивация) — корректно завершай разговор, не теряя время"
    ),
    "tasks": (
        "Ты — AI-ассистент Задачника STH-group. Помогаешь руководителю управлять задачами, целями (roadmap) и weekly-обзорами.\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском языке. Без иероглифов\n"
        "- Используй ТОЛЬКО данные из контекста (списки задач, weekly, roadmap). Если задача не найдена — честно скажи\n"
        "- Не пиши вступлений типа \"Конечно!\", \"Отличный вопрос!\"\n"
        "- Отвечай коротко и по делу\n\n"
        "ДЕЙСТВИЯ — когда пользователь просит создать/изменить/перенести/удалить задачу, добавь в конец ответа маркер действия в формате:\n"
        "<<ACTION:НАЗВАНИЕ ключ=\"значение\" ключ=\"значение\">>\n"
        "Можно несколько маркеров подряд. Пользователь увидит карточку с ОК/Отмена — выполнится только после ОК.\n\n"
        "Список ACTION:\n"
        "- create_task title=\"...\" deadline=\"YYYY-MM-DD\" priority=\"low|med|high\" assignee=\"...\"\n"
        "- update_task id=\"T-NN\" field=\"...\" value=\"...\"  (field — одно из: title, deadline, priority, assignee, status, desc)\n"
        "- move_task id=\"T-NN\" deadline=\"YYYY-MM-DD\"\n"
        "- change_status id=\"T-NN\" status=\"Не начато|В работе|Выполнена|Отложена\"\n"
        "- delete_task id=\"T-NN\"  (мягкое удаление, восстановимо 7 дней)\n\n"
        "Правила для ACTION:\n"
        "- Если пользователь говорит \"завтра/на следующей неделе/через месяц\" — посчитай конкретную дату от сегодняшней (она есть в контексте) и подставь YYYY-MM-DD\n"
        "- Если id не назван явно — найди задачу по названию в контексте. Если не нашёл — спроси уточнение, ACTION не добавляй\n"
        "- На удаление обязательно подтверди вопросом «удалить задачу X?» в тексте перед маркером\n\n"
        "Формат текстового ответа (перед ACTION-маркерами): 2-4 предложения — что собираешься сделать и почему."
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
    "month": (
        "Ты — AI-аналитик дашборда «Итоги месяца» STH. Видишь помесячные отчёты по проектам: "
        "выручка, штрафы, % закрытия заявок, проверка СУПР, ФОТ, воронка подбора (приглашенные → "
        "оформленные → склад → 1 смена → 10 смен), маркетинг (отклики, целевые лиды, стоимость).\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском.\n"
        "- Используй ТОЛЬКО цифры из контекста. Если данных нет — прямо скажи.\n"
        "- Не повторяй вопрос, без вступлений.\n\n"
        "Формат: Факты → Вывод → Рекомендация. 4-8 предложений. **Жирным** — названия проектов и ключевые цифры.\n\n"
        "Нормы (если в контексте нет своих): закрытие заявки ≥95%, штрафы ≤3%, СУПР ≥80, "
        "% целевых ≥22%, стоимость целевого ≤900р."
    ),
    "sales": (
        "Ты — AI-аналитик CRM-дашборда (импорт лидов и сделок из Битрикс24).\n\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском.\n"
        "- Используй ТОЛЬКО цифры из контекста.\n"
        "- Если данных нет — прямо скажи «нет данных».\n"
        "- Без приветствий и повторов вопроса.\n\n"
        "Формат: Факты → Вывод → Рекомендация. 4-8 предложений. **Жирным** — имена менеджеров и ключевые числа.\n\n"
        "Знай схему: лиды и сделки — это массивы объектов вида {id, data: {STAGE_ID, ASSIGNED_BY_NAME, "
        "OPPORTUNITY, DATE_CREATE, ...}}. Стадии связаны через stageMapping. Импорты в коллекции imports."
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


_ALLOWED_AI_HOST_SUFFIXES = (
    ".ngrok-free.dev", ".ngrok-free.app", ".ngrok.io", ".ngrok.app",
    ".trycloudflare.com", ".cfargotunnel.com",
    ".portalsth.ru",  # для собственного поддомена через Cloudflare Tunnel (ai.portalsth.ru)
)


@app.post("/ai/url")
def set_ai_url(payload: AIUrlPayload):
    """Сохранить новый адрес Ollama (вызывается скриптом start-ai.ps1).

    Принимаем только https-туннели ngrok или null (выключить ИИ).
    """
    url = payload.url
    if url is not None:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise HTTPException(400, "URL must start with https://")
        # Извлечь host
        try:
            host = url.split("//", 1)[1].split("/", 1)[0].lower()
        except IndexError:
            raise HTTPException(400, "Invalid URL")
        if not any(host.endswith(sfx) for sfx in _ALLOWED_AI_HOST_SUFFIXES):
            raise HTTPException(400, f"Host not allowed. Must end with one of: {_ALLOWED_AI_HOST_SUFFIXES}")
    AI_URL_FILE.write_text(
        json.dumps({"url": url}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"ok": True, "url": url}


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


# ═══════════════════════════════════════════════
# AUTH — серверная проверка паролей.
# Пароли НЕ передаются клиенту, проверяются здесь.
# Клиент после успешной проверки получает opaque-токен
# и хранит его в sessionStorage. Без знания пароля
# токен подобрать нельзя (HMAC от server-secret).
# ═══════════════════════════════════════════════

import hmac as _hmac
import hashlib as _hashlib
import secrets as _secrets

# Серверный секрет — генерируется один раз, хранится рядом с БД.
_SECRET_FILE = Path(__file__).parent / "auth_secret.bin"
if _SECRET_FILE.exists():
    _AUTH_SECRET = _SECRET_FILE.read_bytes()
else:
    _AUTH_SECRET = _secrets.token_bytes(32)
    try:
        _SECRET_FILE.write_bytes(_AUTH_SECRET)
    except Exception:
        pass

def _make_token(kind: str) -> str:
    """Детерминированный токен от (secret + kind). Один токен на все валидные сессии."""
    return _hmac.new(_AUTH_SECRET, kind.encode("utf-8"), _hashlib.sha256).hexdigest()

@app.post("/auth/check")
async def auth_check(request: Request):
    """Проверить пароль и вернуть opaque-токен."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Bad JSON")
    pwd  = str(body.get("password", ""))
    kind = str(body.get("kind", "portal"))
    if kind == "admin":
        expected = ADMIN_PASSWORD
    elif kind == "portal":
        expected = PORTAL_PASSWORD
    else:
        raise HTTPException(400, "Unknown kind")
    if not expected:
        raise HTTPException(503, "Server password not configured")
    if not _hmac.compare_digest(pwd, expected):
        raise HTTPException(403, "Wrong password")
    return {"ok": True, "token": _make_token(kind)}


def _verify_token(token: str, kind: str) -> bool:
    if not token:
        return False
    return _hmac.compare_digest(token, _make_token(kind))


@app.get("/admin/prompts")
def admin_get_prompts(password: str = "", request: Request = None):
    """Вернуть все промпты. Принимает либо ?password=..., либо заголовок X-Auth-Token."""
    token = request.headers.get("X-Auth-Token", "") if request else ""
    if not (ADMIN_PASSWORD and _hmac.compare_digest(password, ADMIN_PASSWORD) or _verify_token(token, "admin")):
        raise HTTPException(403, "Неверный пароль")
    return _load_prompts()


@app.post("/admin/prompts")
async def admin_save_prompts(request: Request, password: str = ""):
    """Сохранить все промпты (требует пароль или токен)."""
    token = request.headers.get("X-Auth-Token", "")
    if not (ADMIN_PASSWORD and _hmac.compare_digest(password, ADMIN_PASSWORD) or _verify_token(token, "admin")):
        raise HTTPException(403, "Неверный пароль")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Ожидается JSON-объект")
    # Сохранить все известные ключи (включая recruiter)
    to_save = {k: str(v) for k, v in body.items() if k in DEFAULT_PROMPTS}
    PROMPTS_FILE.write_text(json.dumps(to_save, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "saved": list(to_save.keys())}


@app.get("/admin/ai-logs")
def admin_ai_logs(
    request: Request,
    password: str = "",
    dashboard: str = "",
    limit: int = 200,
    offset: int = 0,
):
    """Список последних ИИ-запросов с фильтром по дашборду."""
    token = request.headers.get("X-Auth-Token", "")
    if not (ADMIN_PASSWORD and _hmac.compare_digest(password, ADMIN_PASSWORD) or _verify_token(token, "admin")):
        raise HTTPException(403, "Неверный пароль")
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    with db() as conn:
        if dashboard:
            rows = conn.execute(
                "SELECT id,ts,dashboard,user_token_short,question,response,"
                "prompt_tokens,completion_tokens,latency_ms,cache_hit,error "
                "FROM ai_logs WHERE dashboard=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (dashboard, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id,ts,dashboard,user_token_short,question,response,"
                "prompt_tokens,completion_tokens,latency_ms,cache_hit,error "
                "FROM ai_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return {"items": [dict(r) for r in rows]}


@app.get("/admin/ai-logs/stats")
def admin_ai_logs_stats(request: Request, password: str = ""):
    """Сводная статистика: всего запросов, токенов за сутки, по дашбордам, hit/miss кэша."""
    token = request.headers.get("X-Auth-Token", "")
    if not (ADMIN_PASSWORD and _hmac.compare_digest(password, ADMIN_PASSWORD) or _verify_token(token, "admin")):
        raise HTTPException(403, "Неверный пароль")
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM ai_logs").fetchone()["n"]
        today_24h = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(prompt_tokens),0) AS pt, "
            "COALESCE(SUM(completion_tokens),0) AS ct, "
            "COALESCE(SUM(cache_hit),0) AS hits, COALESCE(AVG(latency_ms),0) AS avg_lat "
            "FROM ai_logs WHERE ts >= datetime('now','-1 day')"
        ).fetchone()
        by_dash = conn.execute(
            "SELECT dashboard, COUNT(*) AS n FROM ai_logs "
            "WHERE ts >= datetime('now','-7 day') GROUP BY dashboard ORDER BY n DESC"
        ).fetchall()
        return {
            "total": int(total),
            "last_24h": {
                "count": int(today_24h["n"]),
                "prompt_tokens": int(today_24h["pt"]),
                "completion_tokens": int(today_24h["ct"]),
                "cache_hits": int(today_24h["hits"]),
                "cache_hit_rate": round(today_24h["hits"]/today_24h["n"], 3) if today_24h["n"] else 0,
                "avg_latency_ms": round(float(today_24h["avg_lat"]), 0),
            },
            "by_dashboard_7d": [dict(r) for r in by_dash],
        }


# ═══════════════════════════════════════════════
# AI SMOKE TESTS — 2 раза в день дёргаем каждый дашборд каноническим
# вопросом, чтобы убедиться что ИИ отвечает. Результат — в ai_test_runs.
# Запуск: localhost — без авторизации (для cron),
#         извне — нужен X-Auth-Token admin.
# ═══════════════════════════════════════════════

AI_SMOKE_CASES = {
    "okk":      "Кратко в 2 предложениях: какие зоны контроля качества ты помогаешь анализировать?",
    "wb":       "Кратко в 2 предложениях: какие метрики Wildberries ты помогаешь анализировать?",
    "finance":  "Кратко в 2 предложениях: какие финансовые показатели ты помогаешь анализировать?",
    "kp":       "Кратко в 2 предложениях: что ты помогаешь делать с коммерческими предложениями?",
    "hr-game":  "Кратко в 2 предложениях: какие типы возражений кандидатов ты помогаешь отрабатывать?",
    "recruiter":"Кандидат говорит «мало платят» — кратко отработай возражение в 2-3 предложения.",
}


def _run_one_smoke(dashboard: str, question: str, system_prompt: str, model: str = "") -> dict:
    """Один тест: дёргает Ollama через тот же путь, что и фронт, не пишет в ai_logs."""
    import time as _t
    base_url = None
    if AI_URL_FILE.exists():
        try:
            base_url = json.loads(AI_URL_FILE.read_text(encoding="utf-8")).get("url")
        except Exception:
            base_url = None
    if not base_url:
        return {"dashboard": dashboard, "question": question, "ok": 0, "latency_ms": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "response_snippet": "", "error": "AI offline"}

    payload = {
        "model": model or "qwen3:8b",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": question},
        ],
        "stream": True,
        "options": {"temperature": 0.3, "num_predict": 200},
    }
    req = _urllib.Request(
        f"{base_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "STH-Smoke/1.0", "ngrok-skip-browser-warning": "true"},
    )
    t0 = _t.time()
    text = ""
    thinking = ""
    pt = ct = 0
    err = None
    last_obj_keys = ""
    try:
        with _urllib.urlopen(req, timeout=180) as resp:
            for raw in resp:
                if not raw.strip():
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                last_obj_keys = ",".join(list(obj.keys())[:6])
                msg = obj.get("message") or {}
                if msg.get("content"):
                    text += msg["content"]
                # qwen3/думающие модели — в отдельном поле
                if msg.get("thinking"):
                    thinking += msg["thinking"]
                if obj.get("prompt_eval_count"):
                    pt = int(obj["prompt_eval_count"])
                if obj.get("eval_count"):
                    ct = int(obj["eval_count"])
    except Exception as e:
        err = str(e)[:300]
    lat = int((_t.time() - t0) * 1000)
    # Главное — есть ли осмысленный контент.
    # Если модель только «подумала» (thinking) и не выдала ответ — это тоже падение,
    # но для диагностики сохраним thinking в snippet.
    body_clean = text.strip()
    ok = 1 if (err is None and len(body_clean) >= 20) else 0
    snippet = body_clean[:500] if body_clean else (f"[ONLY_THINKING last_keys={last_obj_keys}] " + thinking[:400])
    return {
        "dashboard": dashboard, "question": question, "ok": ok,
        "latency_ms": lat, "prompt_tokens": pt, "completion_tokens": ct,
        "response_snippet": snippet, "error": err,
    }


def _run_ai_smoke_all() -> dict:
    """Прогон по всем дашбордам + запись результатов в ai_test_runs."""
    import time as _t, uuid as _uuid
    run_id = _uuid.uuid4().hex[:12]
    prompts = _load_prompts()
    results = []
    for dash, q in AI_SMOKE_CASES.items():
        sp = prompts.get(dash, "")
        if not sp:
            results.append({"dashboard": dash, "question": q, "ok": 0, "latency_ms": 0,
                            "prompt_tokens": 0, "completion_tokens": 0,
                            "response_snippet": "", "error": "no system prompt"})
            continue
        results.append(_run_one_smoke(dash, q, sp))
    # запись пачкой
    with db() as conn:
        conn.executemany(
            "INSERT INTO ai_test_runs "
            "(run_id,dashboard,question,ok,latency_ms,prompt_tokens,completion_tokens,response_snippet,error) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(run_id, r["dashboard"], r["question"], r["ok"], r["latency_ms"],
              r["prompt_tokens"], r["completion_tokens"], r["response_snippet"], r["error"]) for r in results],
        )
    ok_count = sum(1 for r in results if r["ok"])
    return {"run_id": run_id, "total": len(results), "ok": ok_count,
            "failed": len(results) - ok_count, "results": results}


def _is_local_request(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


@app.post("/admin/ai-smoke-run")
def admin_ai_smoke_run(request: Request):
    """Запуск smoke-теста. С localhost — без авторизации (для cron).
       Снаружи — нужен X-Auth-Token администратора."""
    token = request.headers.get("X-Auth-Token", "")
    if not (_is_local_request(request) or _verify_token(token, "admin")):
        raise HTTPException(403, "Доступ запрещён")
    return _run_ai_smoke_all()


@app.get("/admin/ai-test-runs")
def admin_ai_test_runs(request: Request, limit: int = 200):
    """История прогонов smoke. Группированно по run_id."""
    token = request.headers.get("X-Auth-Token", "")
    if not _verify_token(token, "admin"):
        raise HTTPException(403, "Доступ запрещён")
    limit = max(1, min(int(limit or 200), 1000))
    with db() as conn:
        runs = conn.execute(
            "SELECT run_id, MIN(ts) AS ts, COUNT(*) AS total, "
            "SUM(ok) AS ok_count, SUM(latency_ms) AS total_lat "
            "FROM ai_test_runs WHERE run_id IS NOT NULL "
            "GROUP BY run_id ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        latest_run = runs[0]["run_id"] if runs else None
        latest_items = []
        if latest_run:
            latest_items = [dict(r) for r in conn.execute(
                "SELECT dashboard,question,ok,latency_ms,prompt_tokens,completion_tokens,"
                "response_snippet,error,ts FROM ai_test_runs WHERE run_id=? ORDER BY id ASC",
                (latest_run,),
            ).fetchall()]
    return {
        "runs": [dict(r) for r in runs],
        "latest": {"run_id": latest_run, "items": latest_items},
    }


# ═══════════════════════════════════════════════
# AI PROXY — VPS проксирует запросы к Ollama-туннелю.
# Браузер обращается к тому же серверу (без CORS),
# VPS сам запрашивает Ollama через туннель.
# ═══════════════════════════════════════════════

# Кэш health-проверки (фронт дёргает каждые 60с, не хотим долбить туннель).
# Разный TTL для онлайн/оффлайн состояний:
#   - online → кэш 30с (туннель жив, грузить не надо)
#   - offline → кэш 5с (хотим быстро увидеть восстановление после рестарта ВПН)
# Плюс grace-период: одиночный таймаут не объявляем оффлайном, делаем второй
# короткий retry — гасит блипы туннеля длительностью <1с.
_AI_HEALTH_CACHE = {"ts": 0.0, "data": None}
_AI_HEALTH_TTL_OK = 30      # секунд — успешный ответ кэшируем дольше
_AI_HEALTH_TTL_FAIL = 5     # секунд — провал перепроверяем часто


def _probe_ai_once(base: str, timeout: float):
    """Один HTTP-зонд /api/tags. Возвращает dict-результат либо бросает исключение."""
    req = _urllib.Request(
        f"{base}/api/tags",
        headers={"User-Agent": "STH-Health/1.0", "ngrok-skip-browser-warning": "true"},
    )
    with _urllib.urlopen(req, timeout=timeout) as resp:
        d = json.loads(resp.read())
        n = len((d or {}).get("models") or [])
        return {"online": True, "models": n}


@app.get("/ai/health")
def ai_health():
    """Доступность Ollama. Используется фронтом для плашки «ИИ оффлайн».

    Поведение:
      - При успехе → кэш 30 секунд.
      - При неудаче → один retry через 0.7с (гасит блипы туннеля при рестарте ВПН).
      - Если и retry неудачен → offline, но кэш всего 5с (быстро ловим восстановление).
    """
    import time as _t
    now = _t.time()
    cached = _AI_HEALTH_CACHE["data"]
    if cached is not None:
        age = now - _AI_HEALTH_CACHE["ts"]
        ttl = _AI_HEALTH_TTL_OK if cached.get("online") else _AI_HEALTH_TTL_FAIL
        if age < ttl:
            return cached

    data = get_ai_url()
    base = data.get("url") if isinstance(data, dict) else None
    if not base:
        result = {"online": False, "reason": "no_url"}
    else:
        try:
            result = _probe_ai_once(base, timeout=4)
        except Exception as e1:
            # Retry один раз — короткая пауза, короткий таймаут.
            # Это гасит мгновенные блипы туннеля (при VPN-switch ssh реконнектится за ~2-7с,
            # но отдельные пакеты могут теряться раньше).
            try:
                _t.sleep(0.7)
                result = _probe_ai_once(base, timeout=3)
            except Exception as e2:
                result = {"online": False, "reason": "unreachable", "error": str(e2)[:120]}
    _AI_HEALTH_CACHE["ts"] = now
    _AI_HEALTH_CACHE["data"] = result
    return result


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


def _detect_dashboard_from_messages(messages: list) -> str:
    """Определить дашборд по системному промпту (матчит начало текста с DEFAULT_PROMPTS/saved)."""
    try:
        sys_text = "\n".join(
            str(m.get("content", "")) for m in (messages or []) if m.get("role") == "system"
        )
        if not sys_text:
            return "unknown"
        prompts = _load_prompts()
        # Сначала смотрим по началу — это надёжнее
        for key, val in prompts.items():
            head = (val or "")[:60].strip()
            if head and head in sys_text:
                return key
        return "unknown"
    except Exception:
        return "unknown"


def _extract_question(messages: list) -> str:
    """Последнее user-сообщение из чата."""
    try:
        for m in reversed(messages or []):
            if m.get("role") == "user":
                return str(m.get("content", ""))[:4000]
        return ""
    except Exception:
        return ""


def _log_ai(dashboard: str, token: str, question: str, response: str,
            prompt_tokens: int, completion_tokens: int, latency_ms: int,
            cache_hit: bool = False, error: str = "") -> None:
    """Записать строку в ai_logs. Никогда не ломает основной поток (try/except)."""
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO ai_logs(dashboard,user_token_short,question,response,"
                "prompt_tokens,completion_tokens,latency_ms,cache_hit,error) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    dashboard or "unknown",
                    (token or "")[:8],
                    (question or "")[:4000],
                    (response or "")[:20000],
                    int(prompt_tokens or 0),
                    int(completion_tokens or 0),
                    int(latency_ms or 0),
                    1 if cache_hit else 0,
                    (error or "")[:500],
                ),
            )
    except Exception as e:
        # Логи не должны валить ИИ — печатаем в stdout и идём дальше
        print(f"[ai_logs] insert failed: {e}")


@app.post("/ai/proxy/chat/stream")
async def proxy_ai_chat_stream(request: Request):
    """Стриминг-прокси к Ollama /api/chat. Возвращает NDJSON (stream:true).
    Параллельно: накапливает ответ и пишет лог в ai_logs после завершения стрима."""
    import time as _time
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
        body_json = {}
        body_bytes = raw_body

    # Извлекаем метаданные для логирования (до старта стрима)
    messages = body_json.get("messages", []) if isinstance(body_json, dict) else []
    dashboard = _detect_dashboard_from_messages(messages)
    question = _extract_question(messages)
    token_short = request.headers.get("X-Auth-Token", "") or request.headers.get("x-auth-token", "")

    def generate():
        resp = None
        t0 = _time.time()
        collected_response_parts: list[str] = []
        prompt_tokens = 0
        completion_tokens = 0
        last_error = ""
        client_aborted = False
        try:
            req = _urllib.Request(
                f"{base}/api/chat",
                data=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "STH-Portal/1.0",
                    "ngrok-skip-browser-warning": "true",
                    "Connection": "keep-alive",
                },
                method="POST",
            )
            # 10 минут — модель qwen3:8b может писать длинный ответ на слабом железе
            resp = _urllib.urlopen(req, timeout=600)
            for line in resp:
                if not line:
                    continue
                # Парсим строку для метрик, но шлём оригинал клиенту
                try:
                    obj = json.loads(line)
                    msg = obj.get("message") or {}
                    chunk = msg.get("content")
                    if chunk:
                        collected_response_parts.append(chunk)
                    if obj.get("done"):
                        prompt_tokens = int(obj.get("prompt_eval_count") or 0)
                        completion_tokens = int(obj.get("eval_count") or 0)
                except Exception:
                    pass
                yield line
        except GeneratorExit:
            client_aborted = True
            last_error = "client_aborted"
            try:
                if resp is not None:
                    resp.close()
            except Exception:
                pass
            raise
        except Exception as e:
            last_error = str(e)[:500]
            yield json.dumps({"error": str(e)}, ensure_ascii=False).encode() + b"\n"
        finally:
            try:
                if resp is not None:
                    resp.close()
            except Exception:
                pass
            latency_ms = int((_time.time() - t0) * 1000)
            full_response = "".join(collected_response_parts)
            _log_ai(
                dashboard=dashboard,
                token=token_short,
                question=question,
                response=full_response,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                cache_hit=False,
                error=last_error,
            )

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
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
    system_prompt = _load_prompts().get("recruiter", "")
    prompt = rl.build_prompt(req.tz, handbook, req.objection, system_prompt=system_prompt)

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
    system_prompt = _load_prompts().get("recruiter", "")
    prompt = rl.build_chat_prompt(
        req.tz, handbook, req.objection,
        req.first_answer, req.history, req.message,
        system_prompt=system_prompt,
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
