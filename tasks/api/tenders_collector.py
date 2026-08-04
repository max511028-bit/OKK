"""Сборщик тендеров по расписанию. Отдельный процесс.

Почему не поток внутри портала: разбор чужих HTML — это чужой ввод,
непредсказуемая память и время. У VPS одно ядро и 960 МБ, и портал не
должен падать из-за того, что площадка отдала мегабайтную страницу.
Упавший сборщик перезапустит systemd, портал этого даже не заметит.

Интервал берётся из базы (правится во вкладке) и перечитывается на
каждом круге — поменял в интерфейсе, применилось со следующего цикла,
перезапуск не нужен.

Запуск на VPS:  systemctl start tenders-collector
Вручную:        python tenders_collector.py --once
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tenders_core.config import DEFAULT_SCAN_INTERVAL_MINUTES, LOCATION  # noqa: E402
import tenders_pipeline as pipeline  # noqa: E402
from tenders_db import db, get_setting, init_db, now_iso, set_setting  # noqa: E402

log = logging.getLogger("tenders.collector")

# Нижняя граница цикла: даже если в настройках кто-то поставит 1 минуту,
# долбить площадки чаще — верный способ получить бан по IP.
MIN_INTERVAL_MINUTES = 5
# Пауза между кругами опроса настроек, когда автопоиск выключен.
IDLE_SLEEP_SEC = 60


def _interval_minutes() -> int:
    with db() as conn:
        raw = get_setting(conn, "scan_interval_minutes", str(DEFAULT_SCAN_INTERVAL_MINUTES))
    try:
        return max(MIN_INTERVAL_MINUTES, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_SCAN_INTERVAL_MINUTES


def _enabled() -> bool:
    with db() as conn:
        return get_setting(conn, "scan_enabled", "1") == "1"


def run_cycle() -> list[dict]:
    """Один обход всех включённых площадок, доступных из этой точки."""
    started = _dt.datetime.now()
    log.info("Обход площадок начат (location=%s)", LOCATION)
    results = pipeline.run_all(triggered_by="scheduler")
    created = sum(r.get("created", 0) or 0 for r in results)
    fetched = sum(r.get("fetched", 0) or 0 for r in results)
    with db() as conn:
        set_setting(conn, "last_scan_at", now_iso())
        set_setting(conn, "next_scan_at",
                    (_dt.datetime.now()
                     + _dt.timedelta(minutes=_interval_minutes())).isoformat(timespec="seconds"))
    log.info("Обход закончен за %.0fс: площадок %d, скачано %d, новых %d",
             (_dt.datetime.now() - started).total_seconds(), len(results), fetched, created)
    for r in results:
        if r.get("status") not in ("ok", "skipped"):
            log.warning("  %s: %s — %s", r.get("source"), r.get("status"), r.get("message"))
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="Сборщик тендеров")
    ap.add_argument("--once", action="store_true", help="один обход и выход")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    init_db()
    pipeline.sync_sources()

    if args.once:
        run_cycle()
        return

    log.info("Сборщик запущен. Интервал берётся из настроек вкладки.")
    # Первый обход сразу: после перезапуска сервиса ждать три часа
    # неправильно — за это время могли появиться новые закупки.
    next_run = 0.0
    while True:
        try:
            if not _enabled():
                time.sleep(IDLE_SLEEP_SEC)
                continue
            if time.time() >= next_run:
                run_cycle()
                next_run = time.time() + _interval_minutes() * 60
            # Спим короткими отрезками, чтобы смена интервала во вкладке
            # применилась быстро, а не после текущего долгого сна.
            time.sleep(min(IDLE_SLEEP_SEC, max(1.0, next_run - time.time())))
        except KeyboardInterrupt:
            log.info("Остановлен вручную.")
            return
        except Exception:  # noqa: BLE001 — цикл не должен умирать от одной ошибки
            log.exception("Обход упал, повтор через %dс", IDLE_SLEEP_SEC)
            time.sleep(IDLE_SLEEP_SEC)


if __name__ == "__main__":
    main()
