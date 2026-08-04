"""Сбор тендеров: опрос площадок, дедупликация, отбор по направлениям.

Вызывается из двух мест — из сборщика по расписанию и из API по кнопке
«Искать сейчас». Поэтому здесь нет ни планировщика, ни HTTP: только
«опросить площадку X за N дней и разложить результат по базе».

Про местоположение: сети портала и домашнего ПК дополняют друг друга
(замер 31.07), поэтому каждый экземпляр берёт только те площадки, чей
`location` совпадает с его собственным. Площадка с location='any'
опрашивается откуда угодно — кто первым дошёл, тот и записал.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
import threading
from types import SimpleNamespace
from typing import Any

from tenders_core.config import (
    COLD_START_DEPTH_DAYS,
    INCREMENTAL_DEPTH_DAYS,
    LOCATION,
)
from tenders_core.matching import match_tender, stem as _stem
from tenders_core.sources import all_sources, get_source
from tenders_core.sources.base import RawTender, SourceRequiresAuth, SourceUnavailable
from tenders_db import db, dumps, now_iso, row_to_direction

log = logging.getLogger("tenders.pipeline")

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(code: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(code, threading.Lock())


def is_running(code: str) -> bool:
    return _lock_for(code).locked()


# --------------------------------------------------------------------------
# Реестр площадок
# --------------------------------------------------------------------------
def sync_sources() -> None:
    """Привести список площадок в базе в соответствие с кодом.

    Реестр коннекторов — источник правды в обе стороны: появился коннектор —
    появилась строка в админке, удалили коннектор — строка уходит.
    Второе добавлено 04.08 вместе с чисткой заглушек: площадка, которая
    ничего не приносит, только засоряет список.

    Найденные тендеры при этом НЕ трогаем — они уже собраны и остаются
    в выдаче, даже если площадку потом отключили."""
    known = set(all_sources())
    with db() as conn:
        stale = [r["code"] for r in conn.execute("SELECT code FROM sources").fetchall()
                 if r["code"] not in known]
        for code in stale:
            conn.execute("DELETE FROM sources WHERE code=?", (code,))
        if stale:
            log.info("Площадки убраны из списка (нет коннектора): %s", ", ".join(stale))

        for code, cls in all_sources().items():
            row = conn.execute("SELECT id FROM sources WHERE code=?", (code,)).fetchone()
            location = getattr(cls, "location", "any")
            if row:
                conn.execute("UPDATE sources SET title=?, site_url=?, location=? WHERE code=?",
                             (cls.title, getattr(cls, "site_url", ""), location, code))
            else:
                conn.execute(
                    "INSERT INTO sources(code, title, site_url, location, is_enabled, "
                    "requires_auth, settings) VALUES(?,?,?,?,?,?,'{}')",
                    (code, cls.title, getattr(cls, "site_url", ""), location,
                     1 if getattr(cls, "enabled_by_default", True) else 0,
                     1 if getattr(cls, "requires_auth", False) else 0))


def _load_directions(conn, only_direction_id: int | None = None) -> list[SimpleNamespace]:
    """Направления с ключевыми словами в виде, который понимает matching."""
    where = "WHERE d.is_active=1"
    params: list[Any] = []
    if only_direction_id:
        where = "WHERE d.id=?"
        params = [only_direction_id]
    rows = conn.execute(f"SELECT d.* FROM directions d {where} ORDER BY d.sort_order, d.id",
                        params).fetchall()
    out = []
    for row in rows:
        d = row_to_direction(row)
        kws = conn.execute(
            "SELECT * FROM keywords WHERE direction_id=? ORDER BY id", (d["id"],)).fetchall()
        d["keywords"] = [SimpleNamespace(**{**dict(k), "is_active": bool(k["is_active"])})
                         for k in kws]
        out.append(SimpleNamespace(**d))
    return out


# Слова, которые есть в половине закупок страны: искать по ним бессмысленно,
# выдача будет случайной. Сравниваем ПО ОСНОВЕ, а не по буквам: список
# точных форм ломался на падежах — «предоставление» в нём было, а
# «предоставлению» из живой фразы проскакивало в запросы.
_GENERIC_QUERY_STEMS = {
    _stem(w) for w in (
        "работы", "услуги", "оказание", "выполнение", "поставка", "закупка",
        "предоставление", "организация", "обеспечение", "проведение",
        "комплекс", "право", "заключение", "договор", "выбор", "предмет",
    )
}


def search_queries_for(directions, source_code: str, limit: int = 40,
                       split_words: bool = False) -> list[str]:
    """Поисковые фразы для площадки: берём include/require-слова тех
    направлений, которые эту площадку не исключили.

    split_words — для площадок с поиском по ФРАЗЕ (query_driven). Замер
    04.08 на B2B-Center: «персонал» находит 20 закупок, а «аутсорсинг
    персонала» — НОЛЬ, и «погрузочно-разгрузочные работы» тоже ноль. Их
    поиск требует точного вхождения всей строки. Мы слали длинные фразы и
    сами себе резали выдачу: 13 карточек вместо 34 по тем же правилам.

    Поэтому таким площадкам отдаём отдельные слова, а точную фразу
    проверяем уже у себя при отборе — площадка нужна как широкое сито,
    а не как финальный фильтр."""
    scored: dict[str, float] = {}

    def add(term: str, weight: float) -> None:
        term = term.strip().lower()
        if len(term) < 4 or _stem(term) in _GENERIC_QUERY_STEMS:
            return
        scored[term] = max(scored.get(term, 0), weight)

    for d in directions:
        if d.source_codes and source_code not in d.source_codes:
            continue
        for kw in d.keywords:
            if not kw.is_active or kw.kind == "exclude":
                continue
            phrase = (kw.phrase or "").strip()
            if len(phrase) < 3:
                continue
            weight = kw.weight or 1.0
            if not split_words:
                scored[phrase] = max(scored.get(phrase, 0), weight)
                continue
            for word in re.split(r"[\s,;/]+", phrase):
                # «погрузочно-разгрузочные» → ищем и по половинкам: площадки
                # часто пишут через дефис иначе или вовсе раздельно
                add(word, weight)
                for part in word.split("-"):
                    add(part, weight)
    return [p for p, _ in sorted(scored.items(), key=lambda x: -x[1])][:limit]


# --------------------------------------------------------------------------
# Запись результата
# --------------------------------------------------------------------------
def _iso(value) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat(timespec="seconds")


def _upsert(conn, source_code: str, raw: RawTender, directions) -> str:
    """Вставить или обновить тендер и пересчитать его совпадения.
    Возвращает 'created' | 'updated' | 'skipped'."""
    ext = str(raw.external_id or "").strip()
    if not ext:
        return "skipped"
    payload = {
        "source_code": source_code,
        "external_id": ext,
        "url": raw.url or "",
        "title": raw.title or "",
        "description": raw.description or "",
        "customer": raw.customer or "",
        "region": raw.region or "",
        "price": raw.price,
        "currency": raw.currency or "RUB",
        "law": raw.law or "",
        "okpd2": raw.okpd2 or "",
        "purchase_method": raw.purchase_method or "",
        "published_at": _iso(raw.published_at),
        "deadline_at": _iso(raw.deadline_at),
    }
    results = match_tender(payload, directions)
    if not results:
        return "skipped"          # не наш профиль — в базе не храним
    best = max(r.score for r in results)

    existing = conn.execute(
        "SELECT id FROM tenders WHERE source_code=? AND external_id=?",
        (source_code, ext)).fetchone()
    if existing:
        tender_id = existing["id"]
        conn.execute(
            "UPDATE tenders SET url=?, title=?, description=?, customer=?, region=?, "
            "price=?, currency=?, law=?, okpd2=?, purchase_method=?, published_at=?, "
            "deadline_at=?, best_score=?, raw=?, updated_at=? WHERE id=?",
            (payload["url"], payload["title"], payload["description"], payload["customer"],
             payload["region"], payload["price"], payload["currency"], payload["law"],
             payload["okpd2"], payload["purchase_method"], payload["published_at"],
             payload["deadline_at"], best, dumps(raw.raw or {}), now_iso(), tender_id))
        outcome = "updated"
    else:
        cur = conn.execute(
            "INSERT INTO tenders(source_code, external_id, url, title, description, customer, "
            "region, price, currency, law, okpd2, purchase_method, published_at, deadline_at, "
            "status, best_score, raw, first_seen_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?,?)",
            (source_code, ext, payload["url"], payload["title"], payload["description"],
             payload["customer"], payload["region"], payload["price"], payload["currency"],
             payload["law"], payload["okpd2"], payload["purchase_method"],
             payload["published_at"], payload["deadline_at"], best,
             dumps(raw.raw or {}), now_iso(), now_iso()))
        tender_id = cur.lastrowid
        outcome = "created"

    # Совпадения переписываем целиком: правила могли поменяться.
    conn.execute("DELETE FROM matches WHERE tender_id=?", (tender_id,))
    for r in results:
        conn.execute(
            "INSERT OR REPLACE INTO matches(tender_id, direction_id, score, matched, created_at) "
            "VALUES(?,?,?,?,?)",
            (tender_id, r.direction_id, r.score, dumps(r.matched), now_iso()))
    return outcome


# --------------------------------------------------------------------------
# Прогон площадки
# --------------------------------------------------------------------------
def run_source(code: str, *, triggered_by: str = "scheduler",
               direction_id: int | None = None,
               depth_days: int | None = None) -> dict[str, Any]:
    """Опросить одну площадку.

    direction_id — искать только по одному направлению (кнопка «Искать
    сейчас» в карточке направления). depth_days — за сколько дней назад,
    задаётся вручную; иначе холодный старт или обычная глубина."""
    lock = _lock_for(code)
    if not lock.acquire(blocking=False):
        return {"source": code, "status": "busy", "message": "прогон уже идёт"}
    try:
        return _run_source_locked(code, triggered_by, direction_id, depth_days)
    finally:
        lock.release()


def _run_source_locked(code: str, triggered_by: str, direction_id: int | None,
                       depth_days: int | None) -> dict[str, Any]:
    with db() as conn:
        src = conn.execute("SELECT * FROM sources WHERE code=?", (code,)).fetchone()
        if not src:
            return {"source": code, "status": "error", "message": "площадка не заведена"}
        if not src["is_enabled"]:
            return {"source": code, "status": "skipped", "message": "площадка выключена"}
        directions = _load_directions(conn, direction_id)
        if not directions:
            return {"source": code, "status": "skipped", "message": "нет активных направлений"}
        if depth_days is None:
            depth_days = (COLD_START_DEPTH_DAYS if not src["last_run_at"]
                          else INCREMENTAL_DEPTH_DAYS)
        settings = src["settings"]
        cur = conn.execute(
            "INSERT INTO runs(source_code, started_at, status, triggered_by, direction_id, "
            "depth_days) VALUES(?,?,'running',?,?,?)",
            (code, now_iso(), triggered_by, direction_id, depth_days))
        run_id = cur.lastrowid

    since = _dt.datetime.now() - _dt.timedelta(days=depth_days)
    cls_for_queries = get_source(code)
    queries = search_queries_for(
        directions, code,
        split_words=bool(getattr(cls_for_queries, "query_driven", False)))
    fetched = created = updated = failed = 0
    status, message = "ok", ""

    cls = get_source(code)
    if cls is None:
        status, message = "error", "коннектор не найден в коде"
    else:
        try:
            import json as _json
            creds = ((src["login"], src["password"])
                     if (src["login"] or src["password"]) else None)
            source = cls()
            batch: list[RawTender] = []
            for raw in source.fetch(since=since, settings=_json.loads(settings or "{}"),
                                    credentials=creds, queries=queries):
                fetched += 1
                batch.append(raw)
                if len(batch) >= 50:
                    c, u, f = _flush(code, batch, directions)
                    created += c; updated += u; failed += f
                    batch = []
            if batch:
                c, u, f = _flush(code, batch, directions)
                created += c; updated += u; failed += f
        except SourceRequiresAuth as e:
            status, message = "error", f"нужна авторизация: {e}"
        except SourceUnavailable as e:
            status, message = "error", str(e)
        except Exception as e:  # noqa: BLE001 — журнал прогона важнее аккуратности типов
            log.exception("площадка %s упала", code)
            status, message = "error", f"{type(e).__name__}: {e}"

    if failed and status == "ok":
        # Скачали, но не смогли положить в базу — это не «ok».
        status = "partial"
        message = f"не записано {failed} из {fetched}: смотри лог сборщика"

    with db() as conn:
        conn.execute(
            "UPDATE runs SET finished_at=?, status=?, fetched=?, matched=?, created=?, message=? "
            "WHERE id=?", (now_iso(), status, fetched, created + updated, created, message, run_id))
        conn.execute("UPDATE sources SET last_run_at=?, last_status=?, last_message=? WHERE code=?",
                     (now_iso(), status, message, code))
    return {"source": code, "status": status, "fetched": fetched,
            "created": created, "updated": updated, "message": message,
            "depth_days": depth_days}


def _flush(code: str, batch: list[RawTender], directions) -> tuple[int, int, int]:
    """Записать пачку. Третьим числом — сколько записей УПАЛО.

    Считать падения обязательно: 31.07 при переносе я ошибся в имени поля
    RawTender, каждая запись падала — а прогон бодро рапортовал «ok» и
    «найдено 0», потому что исключения глотались поштучно. Молчаливый
    ноль неотличим от честного «ничего не подошло», и это худший вид
    поломки: выглядит как рабочая система."""
    created = updated = failed = 0
    with db() as conn:
        for raw in batch:
            try:
                outcome = _upsert(conn, code, raw, directions)
            except Exception:  # noqa: BLE001
                log.exception("не смог записать тендер %s/%s", code, raw.external_id)
                failed += 1
                continue
            if outcome == "created":
                created += 1
            elif outcome == "updated":
                updated += 1
    return created, updated, failed


def run_all(*, triggered_by: str = "scheduler", direction_id: int | None = None,
            depth_days: int | None = None, location: str | None = None) -> list[dict[str, Any]]:
    """Обойти все включённые площадки, доступные из этой точки."""
    here = location or LOCATION
    with db() as conn:
        rows = conn.execute(
            "SELECT code, location FROM sources WHERE is_enabled=1 ORDER BY id").fetchall()
    out = []
    for row in rows:
        if row["location"] not in ("any", here):
            continue
        out.append(run_source(row["code"], triggered_by=triggered_by,
                              direction_id=direction_id, depth_days=depth_days))
    return out


def rematch_all() -> dict[str, int]:
    """Пересчитать совпадения по всем сохранённым тендерам — после правки
    направлений.

    Тендеры, переставшие подходить, НЕ удаляются: у них просто не остаётся
    совпадений, и выдача их не показывает (см. tenders_api.list_tenders —
    он требует хотя бы одно совпадение). Так правка фильтра обратима:
    сузил город, передумал, вернул обратно — всё на месте, повторно
    обходить площадки не нужно.

    Раньше здесь стоял DELETE, и это была тихая потеря данных: снятие
    фильтра уже ничего не возвращало (поймано при проверке 31.07)."""
    with db() as conn:
        directions = _load_directions(conn)
        rows = conn.execute("SELECT * FROM tenders").fetchall()
        kept = dropped = 0
        for row in rows:
            results = match_tender(dict(row), directions)
            conn.execute("DELETE FROM matches WHERE tender_id=?", (row["id"],))
            if not results:
                conn.execute("UPDATE tenders SET best_score=0 WHERE id=?", (row["id"],))
                dropped += 1
                continue
            for r in results:
                conn.execute(
                    "INSERT OR REPLACE INTO matches(tender_id, direction_id, score, matched, "
                    "created_at) VALUES(?,?,?,?,?)",
                    (row["id"], r.direction_id, r.score, dumps(r.matched), now_iso()))
            conn.execute("UPDATE tenders SET best_score=? WHERE id=?",
                         (max(r.score for r in results), row["id"]))
            kept += 1
    return {"kept": kept, "dropped": dropped}
