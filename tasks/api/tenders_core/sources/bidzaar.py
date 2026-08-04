"""Bidzaar — bidzaar.com.

Коммерческая площадка с открытым JSON API: публичный листинг процедур доступен
без авторизации и отсортирован по дате публикации. Поэтому берём его сплошным
потоком и останавливаемся, дойдя до нужной глубины, — так надёжнее поиска по
подстроке, который не понимает русских окончаний.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterator

from .base import BaseSource, RawTender, SourceUnavailable, register

log = logging.getLogger(__name__)

API_URL = "https://bidzaar.com/api/process/light/procedures/available"
CARD_URL = "https://bidzaar.com/app/process/light/{id}"

# Типы процедур: 1 — закупки, 2 — продажи, 3 — прочие запросы.
PROCEDURE_TYPES = {1: "Закупка", 2: "Продажа", 3: "Запрос"}


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _region_of(addresses: list[dict[str, Any]] | None) -> str:
    if not addresses:
        return ""
    parts = []
    for address in addresses:
        region = (address.get("region") or "").strip()
        city = (address.get("city") or "").strip()
        label = ", ".join(x for x in (region, city) if x)
        if label and label not in parts:
            parts.append(label)
    return "; ".join(parts[:3])


@register
class BidzaarSource(BaseSource):
    location = "any"
    code = "bidzaar"
    title = "Bidzaar"
    site_url = "https://bidzaar.com"
    notes = (
        "Коммерческие закупки крупных компаний. Публичный API отдаёт список без "
        "авторизации, но без начальной цены — она видна только участникам."
    )
    # Замер 04.08 по API (статус 1 = идёт приём заявок): всего 5463
    # процедуры, из них тип 1 — 2786, тип 2 — 631, тип 3 — 2046. Раньше
    # брали только 1 и 3, то есть 4832 из 5463: каждая девятая действующая
    # закупка проходила мимо. Тип 2 добавлен.
    #
    # Статусы 2 и 3 (14 тыс. и 227 тыс.) — завершённые процедуры, подать
    # заявку туда уже нельзя. Для мониторинга они не нужны и берутся
    # только при явной надобности через настройку `statuses`.
    default_settings: dict[str, Any] = {
        "page_size": 100,
        "max_pages": 25,
        "procedure_types": [1, 2, 3],
        "statuses": [1],
    }

    def fetch(
        self,
        since: dt.datetime,
        settings: dict[str, Any],
        credentials: tuple[str, str] | None = None,
        queries: list[str] | None = None,
    ) -> Iterator[RawTender]:
        cfg = {**self.default_settings, **(settings or {})}
        page_size = int(cfg["page_size"])

        with self.new_client(base_headers={"Accept": "application/json"}) as client:
            for procedure_type in cfg.get("procedure_types") or [1]:
                for page in range(1, int(cfg["max_pages"]) + 1):
                    params = {
                        "paging.page": page,
                        "paging.size": page_size,
                        "sorting.key": "publishDate",
                        "sorting.direction": "desc",
                        "logic": "and",
                        "filters[0].operator": "in",
                        "filters[0].field": "status",
                        "filters[0].value": "[%s]" % ",".join(
                            str(s) for s in (cfg.get("statuses") or [1])),
                        "filters[1].operator": "eq",
                        "filters[1].field": "procedureType",
                        "filters[1].value": str(procedure_type),
                    }
                    response = client.get(API_URL, params=params)
                    if response.status_code != 200:
                        raise SourceUnavailable(f"Bidzaar вернул HTTP {response.status_code}")
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise SourceUnavailable(f"Bidzaar отдал не JSON: {exc}") from exc

                    items = payload.get("items") or []
                    if not items:
                        break

                    reached_depth = False
                    for item in items:
                        published = _parse_iso(item.get("publishDate"))
                        if published and published < since:
                            reached_depth = True
                            continue
                        yield self._to_tender(item, procedure_type)
                    # Список отсортирован по убыванию даты — дальше только старее.
                    if reached_depth or len(items) < page_size:
                        break

    @staticmethod
    def _to_tender(item: dict[str, Any], procedure_type: int) -> RawTender:
        name = item.get("name") or ""
        return RawTender(
            external_id=str(item.get("id") or item.get("number") or ""),
            title=name,
            url=CARD_URL.format(id=item.get("id")),
            description=name,
            customer=item.get("companyName") or "",
            region=_region_of(item.get("deliveryAddresses")),
            law="commercial",
            purchase_method=PROCEDURE_TYPES.get(procedure_type, ""),
            published_at=_parse_iso(item.get("publishDate")),
            deadline_at=_parse_iso(item.get("acceptanceEndDate")),
            raw={
                "number": item.get("number"),
                "tradingType": item.get("tradingType"),
                "procedureType": procedure_type,
            },
        )
