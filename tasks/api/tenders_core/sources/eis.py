"""ЕИС — zakupki.gov.ru (44-ФЗ и 223-ФЗ).

Самый ценный источник: все государственные и корпоративные закупки, размещаемые
на РТС-тендере, Росэлторге, Сбер-АСТ, ТЭК-Торге, ЭТП ГПБ и прочих федеральных
площадках, обязаны дублироваться здесь. Одна интеграция закрывает госсегмент
всех этих ЭТП сразу.

Данные берём из RSS расширенного поиска: он отдаёт те же результаты, что и
веб-интерфейс, но в стабильном машиночитаемом виде.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import re
from typing import Any, Iterator
from urllib.parse import urlencode

from .base import BaseSource, RawTender, SourceUnavailable, register

log = logging.getLogger(__name__)

RSS_URL = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html"
_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
# Значение поля тянется до конца строки, а не до первого тега: ЕИС подсвечивает
# найденные слова через <span class='highlightColor'>, и обрыв на '<' резал текст.
_FIELD_RE = re.compile(r"^\s*<strong>\s*([^<:]{2,80}?)\s*:\s*</strong>\s*(.*)$", re.S)


def _text(pattern: str, source: str) -> str:
    m = re.search(pattern, source, re.S)
    return html.unescape(m.group(1)).strip() if m else ""


def _parse_date(value: str) -> dt.datetime | None:
    value = value.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_price(value: str) -> float | None:
    """Первое число в строке. Цена и валюта у ЕИС приходят одной строкой."""
    m = re.search(r"\d[\d\s ]*(?:[.,]\d+)?", value)
    if not m:
        return None
    cleaned = m.group(0).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


@register
class EisSource(BaseSource):
    # ЕИС не отвечает с VPS даже с отключённой проверкой TLS —
    # блокирует датацентровые адреса (замер 31.07). Только из дома.
    location = "home"
    code = "eis"
    title = "ЕИС — zakupki.gov.ru (44-ФЗ / 223-ФЗ)"
    site_url = "https://zakupki.gov.ru"
    query_driven = True
    notes = (
        "Госзакупки и закупки госкомпаний. Покрывает процедуры, размещённые на "
        "РТС-тендер, Росэлторг, Сбер-АСТ, ТЭК-Торг, ЭТП ГПБ и др. — они обязаны "
        "публиковаться в ЕИС. Сайт использует сертификат Минцифры, которого нет "
        "в стандартных хранилищах, поэтому проверка сертификата по умолчанию "
        "выключена (verify_ssl). Установите корневой сертификат с gosuslugi.ru/crt "
        "и включите проверку обратно. Срок подачи заявок RSS не отдаёт."
    )
    default_settings: dict[str, Any] = {
        "verify_ssl": False,
        "fz44": True,
        "fz223": True,
        # Этапы: af — подача заявок, ca — работа комиссии, pc — завершена, pa — отменена
        "stages": ["af", "ca"],
        # 200 — потолок выдачи RSS, больше отдать площадка не умеет.
        "records_per_page": 200,
        # Фраз в направлениях обычно несколько десятков — берём их все, один
        # запрос к RSS занимает пару секунд.
        "max_queries": 60,
        # Коды регионов ЕИС (customerPlace). Пусто = вся страна.
        "region_codes": [],
        "fetch_details": False,
    }

    def fetch(
        self,
        since: dt.datetime,
        settings: dict[str, Any],
        credentials: tuple[str, str] | None = None,
        queries: list[str] | None = None,
    ) -> Iterator[RawTender]:
        cfg = {**self.default_settings, **(settings or {})}
        queries = [q for q in (queries or []) if q.strip()][: int(cfg["max_queries"])]
        if not queries:
            log.info("ЕИС: нет поисковых фраз — направления не настроены")
            return

        seen: set[str] = set()
        verify = False if cfg.get("verify_ssl") is False else None
        with self.new_client(verify=verify) as client:
            for query in queries:
                # pageNumber в RSS не работает — вторая страница повторяет первую,
                # поэтому за один запрос забираем максимум и листать не пытаемся.
                url = f"{RSS_URL}?{urlencode(self._params(query, 1, since, cfg))}"
                response = client.get(url)
                if response.status_code != 200:
                    raise SourceUnavailable(f"ЕИС вернул HTTP {response.status_code}")
                for item in _ITEM_RE.findall(response.text):
                    tender = self._parse_item(item)
                    if tender is None or tender.external_id in seen:
                        continue
                    seen.add(tender.external_id)
                    if tender.published_at and tender.published_at < since:
                        continue
                    yield tender

    # ------------------------------------------------------------------
    def _params(self, query: str, page: int, since: dt.datetime, cfg: dict) -> dict[str, Any]:
        params: dict[str, Any] = {
            "searchString": query,
            "morphology": "on",
            "search-filter": "Дате размещения",
            "pageNumber": page,
            "sortDirection": "false",
            "recordsPerPage": f"_{int(cfg['records_per_page'])}",
            "showLotsInfoHidden": "false",
            "sortBy": "UPDATE_DATE",
        }
        # publishDateFrom/publishDateTo в RSS-выдаче обнуляют результат (проверено
        # на живых запросах), поэтому период отсекаем у себя после разбора.
        if cfg.get("fz44"):
            params["fz44"] = "on"
        if cfg.get("fz223"):
            params["fz223"] = "on"
        for stage in cfg.get("stages") or ["af"]:
            params[stage] = "on"
        region_codes = cfg.get("region_codes") or []
        if region_codes:
            params["customerPlace"] = ",".join(str(c) for c in region_codes)
        return params

    def _parse_item(self, item: str) -> RawTender | None:
        link = _text(r"<link>(.*?)</link>", item)
        title_raw = _text(r"<title>(.*?)</title>", item)
        description_raw = html.unescape(_text(r"<description>(.*?)</description>", item))

        reg_number = ""
        m = re.search(r"regNumber=(\d+)", link) or re.search(r"№\s*(\d{10,})", title_raw)
        if m:
            reg_number = m.group(1)
        if not reg_number:
            return None

        # «Найденный результат» отделяет карточку от блока с параметрами поиска.
        body = description_raw.split("Найденный результат:", 1)[-1]
        fields: dict[str, str] = {}
        for line in _BR_RE.split(body):
            m = _FIELD_RE.match(line)
            if not m:
                continue
            key = re.sub(r"\s+", " ", _TAG_RE.sub("", m.group(1))).strip().lower()
            value = re.sub(r"\s+", " ", _TAG_RE.sub("", m.group(2))).strip()
            # Цена и валюта лежат в одной строке двумя <strong> подряд.
            if key not in fields:
                fields[key] = value

        subject = fields.get("наименование объекта закупки", "")

        law_raw = fields.get("размещение выполняется по", "")
        law = "44" if "44" in law_raw else "223" if "223" in law_raw else ""

        purchase_method = re.sub(r"\s*№.*$", "", title_raw).strip()

        return RawTender(
            external_id=reg_number,
            title=subject or title_raw,
            url=link or f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={reg_number}",
            description=subject,
            customer=fields.get("наименование заказчика", "") or _text(r"<author>(.*?)</author>", item),
            price=_parse_price(fields.get("начальная цена контракта", "")),
            currency="RUB",
            law=law or "44",
            purchase_method=purchase_method,
            published_at=_parse_date(fields.get("размещено", "")),
            raw={
                "stage": fields.get("этап размещения", ""),
                "ikz": fields.get("идентификационный код закупки (икз)", ""),
                "updated": fields.get("обновлено", ""),
            },
        )
