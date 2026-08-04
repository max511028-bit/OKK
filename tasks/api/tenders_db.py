"""База Тендер-радара: схема, миграции, доступ.

Сознательно на голом sqlite3, как и весь остальной портал, — SQLAlchemy
из отдельного приложения не переносим: на VPS одно ядро и 960 МБ, лишняя
ORM в двух процессах там ни к чему.

Схема:
    groups   ──< directions ──< keywords     — что ищем (правится в админке)
    sources                                  — где ищем (реестр коннекторов)
    tenders  ──< matches >── directions      — что нашли и подо что подошло
    runs                                     — журнал прогонов
    settings                                 — периодичность и прочие настройки

Фильтры направления (cities/customers добавлены по просьбе владельца
31.07): пустой список = фильтр не применяется.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from tenders_core.config import DB_PATH, DEFAULT_SCAN_INTERVAL_MINUTES

_SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#4b7bec',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS directions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id     INTEGER REFERENCES groups(id) ON DELETE SET NULL,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    is_active    INTEGER NOT NULL DEFAULT 1,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    min_score    REAL NOT NULL DEFAULT 1.0,
    min_price    REAL,
    max_price    REAL,
    regions      TEXT NOT NULL DEFAULT '[]',
    cities       TEXT NOT NULL DEFAULT '[]',
    customers    TEXT NOT NULL DEFAULT '[]',
    laws         TEXT NOT NULL DEFAULT '[]',
    okpd2        TEXT NOT NULL DEFAULT '[]',
    source_codes TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_directions_group ON directions(group_id);

CREATE TABLE IF NOT EXISTS keywords (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    direction_id INTEGER NOT NULL REFERENCES directions(id) ON DELETE CASCADE,
    phrase       TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'include',   -- include | exclude | require
    weight       REAL NOT NULL DEFAULT 1.0,
    match_mode   TEXT NOT NULL DEFAULT 'stem',      -- stem | exact | regex
    is_active    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_keywords_direction ON keywords(direction_id);

CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    site_url      TEXT NOT NULL DEFAULT '',
    location      TEXT NOT NULL DEFAULT 'any',      -- vps | home | any
    is_enabled    INTEGER NOT NULL DEFAULT 1,
    requires_auth INTEGER NOT NULL DEFAULT 0,
    login         TEXT NOT NULL DEFAULT '',
    password      TEXT NOT NULL DEFAULT '',
    settings      TEXT NOT NULL DEFAULT '{}',
    last_run_at   TEXT,
    last_status   TEXT NOT NULL DEFAULT '',
    last_message  TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tenders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_code    TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    url            TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    customer       TEXT NOT NULL DEFAULT '',
    region         TEXT NOT NULL DEFAULT '',
    price          REAL,
    currency       TEXT NOT NULL DEFAULT 'RUB',
    law            TEXT NOT NULL DEFAULT '',
    okpd2          TEXT NOT NULL DEFAULT '',
    purchase_method TEXT NOT NULL DEFAULT '',
    published_at   TEXT,
    deadline_at    TEXT,
    status         TEXT NOT NULL DEFAULT 'new',     -- new|interesting|in_work|rejected
    note           TEXT NOT NULL DEFAULT '',
    best_score     REAL NOT NULL DEFAULT 0,
    raw            TEXT NOT NULL DEFAULT '{}',
    first_seen_at  TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE(source_code, external_id)
);
CREATE INDEX IF NOT EXISTS ix_tenders_published ON tenders(published_at);
CREATE INDEX IF NOT EXISTS ix_tenders_status    ON tenders(status);
CREATE INDEX IF NOT EXISTS ix_tenders_seen      ON tenders(first_seen_at);

CREATE TABLE IF NOT EXISTS matches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id    INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
    direction_id INTEGER NOT NULL REFERENCES directions(id) ON DELETE CASCADE,
    score        REAL NOT NULL DEFAULT 0,
    matched      TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL,
    UNIQUE(tender_id, direction_id)
);
CREATE INDEX IF NOT EXISTS ix_matches_tender    ON matches(tender_id);
CREATE INDEX IF NOT EXISTS ix_matches_direction ON matches(direction_id);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_code  TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',   -- running|ok|error|partial
    triggered_by TEXT NOT NULL DEFAULT 'scheduler', -- scheduler|manual
    direction_id INTEGER,
    depth_days   INTEGER,
    fetched      INTEGER NOT NULL DEFAULT 0,
    matched      INTEGER NOT NULL DEFAULT 0,
    created      INTEGER NOT NULL DEFAULT 0,
    message      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_runs_started ON runs(started_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

# Колонки, добавленные после первого выпуска. Тот же идиом, что в main.py:
# пытаемся добавить, «уже существует» — не ошибка.
_MIGRATIONS = [
    ("directions", "cities", "TEXT NOT NULL DEFAULT '[]'"),
    ("directions", "customers", "TEXT NOT NULL DEFAULT '[]'"),
    ("sources", "location", "TEXT NOT NULL DEFAULT 'any'"),
    ("runs", "triggered_by", "TEXT NOT NULL DEFAULT 'scheduler'"),
    ("runs", "direction_id", "INTEGER"),
    ("runs", "depth_days", "INTEGER"),
]

_DEFAULT_SETTINGS = {
    "scan_interval_minutes": str(DEFAULT_SCAN_INTERVAL_MINUTES),
    "scan_enabled": "1",
}

JSON_FIELDS_DIRECTION = ("regions", "cities", "customers", "laws", "okpd2", "source_codes")


def now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """Соединение с базой тендеров. WAL — чтобы сборщик писал, а портал
    в это же время читал, не упираясь в блокировку."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(_SCHEMA)
        for table, column, ddl in _MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            except sqlite3.OperationalError:
                pass  # колонка уже есть
        for key, value in _DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                         (key, value))


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def _loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def row_to_direction(row: sqlite3.Row) -> dict[str, Any]:
    """Строка БД → словарь с распакованным JSON. Именно такой объект
    (обёрнутый в SimpleNamespace) ждёт tenders_core.matching."""
    d = dict(row)
    for field in JSON_FIELDS_DIRECTION:
        d[field] = _loads(d.get(field), [])
    d["is_active"] = bool(d.get("is_active", 1))
    return d


def row_to_tender(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["raw"] = _loads(d.get("raw"), {})
    return d


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
