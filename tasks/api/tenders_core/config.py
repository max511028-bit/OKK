"""Настройки Тендер-радара на портале.

Отличия от отдельного приложения, из которого это перенесено 31.07.2026:
  • база лежит рядом с tasks.db (`tasks/api/tenders.db`) и ИСКЛЮЧЕНА из
    rsync — деплой её не затирает (см. .github/workflows/deploy.yml);
  • собственного HTTP-сервера нет: API живёт в общем приложении портала;
  • сборщик — отдельный процесс, чтобы разбор чужих страниц не мог
    уронить портал (у VPS одно ядро и 960 МБ).
"""
from __future__ import annotations

import os
from pathlib import Path

# tasks/api/ — рядом с main.py и tasks.db
BASE_DIR = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.environ.get("TR_DB_PATH", BASE_DIR / "tenders.db"))

# Умолчание для периодичности обхода (минуты). Реальное значение правится
# в админке и хранится в таблице settings — здесь только запасной вариант.
DEFAULT_SCAN_INTERVAL_MINUTES = int(os.environ.get("TR_SCAN_INTERVAL", "180"))

# Сетевые параметры коннекторов.
HTTP_TIMEOUT = float(os.environ.get("TR_HTTP_TIMEOUT", "40"))
HTTP_RETRIES = int(os.environ.get("TR_HTTP_RETRIES", "2"))
# Пауза между запросами к одной площадке — чтобы не ловить баны.
POLITE_DELAY_SECONDS = float(os.environ.get("TR_POLITE_DELAY", "1.2"))
# Браузерный UA обязателен: B2B-Center отдаёт «голому» клиенту 403, а с
# этим заголовком тот же адрес отвечает 200 (замер с VPS 31.07).
USER_AGENT = os.environ.get(
    "TR_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
)

# Глубина при первом (холодном) запуске площадки и при обычных прогонах, дни.
COLD_START_DEPTH_DAYS = int(os.environ.get("TR_COLD_START_DAYS", "14"))
INCREMENTAL_DEPTH_DAYS = int(os.environ.get("TR_INCREMENTAL_DAYS", "3"))

# ГДЕ работает этот экземпляр сборщика. Сети портала и домашнего ПК
# дополняют друг друга (замер 31.07): ЕИС отвечает только из домашней
# сети, а Роселторг, Фабрикант, ЭТП ГПБ и ЗаказРФ — только с VPS.
# Коннектор объявляет своё место в атрибуте `location`, сборщик берёт
# только «свои» площадки.
LOCATION = os.environ.get("TR_LOCATION", "vps")  # vps | home | any
