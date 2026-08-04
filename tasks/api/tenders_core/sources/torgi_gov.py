"""ГИС Торги — torgi.gov.ru.

Имущественные торги: аренда и продажа государственного имущества, реализация
имущества должников. К услугам по персоналу отношения почти не имеет, поэтому
по умолчанию площадка выключена — включается в админке, если понадобится.
Единственный источник с полноценным открытым JSON API.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterator

from ..regions import subject_name
from .base import BaseSource, RawTender, SourceUnavailable, register

log = logging.getLogger(__name__)

API_URL = "https://torgi.gov.ru/new/api/public/lotcards/search"
CARD_URL = "https://torgi.gov.ru/new/public/lots/lot/{id}"


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(cleaned)
        return parsed.replace(tzinfo=None)
    except ValueError:
        return None


@register
class TorgiGovSource(BaseSource):
    location = "any"
    code = "torgi_gov"
    title = "ГИС Торги — torgi.gov.ru"
    site_url = "https://torgi.gov.ru"
    query_driven = True
    enabled_by_default = False
    notes = (
        "Аренда и продажа госимущества, торги по банкротству. Открытый JSON API. "
        "Для закупок услуг и персонала не подходит — включайте, только если "
        "интересует имущество."
    )
    default_settings: dict[str, Any] = {"max_pages": 3, "page_size": 50, "max_queries": 10}

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
            return

        seen: set[str] = set()
        with self.new_client(base_headers={"Accept": "application/json"}) as client:
            for query in queries:
                for page in range(int(cfg["max_pages"])):
                    params = {
                        "text": query,
                        "page": page,
                        "size": int(cfg["page_size"]),
                        "sort": "firstVersionPublicationDate,desc",
                    }
                    response = client.get(API_URL, params=params)
                    if response.status_code != 200:
                        raise SourceUnavailable(f"torgi.gov.ru вернул HTTP {response.status_code}")
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise SourceUnavailable(f"torgi.gov.ru отдал не JSON: {exc}") from exc

                    content = payload.get("content") or []
                    if not content:
                        break
                    for lot in content:
                        lot_id = str(lot.get("id") or "")
                        if not lot_id or lot_id in seen:
                            continue
                        seen.add(lot_id)
                        published = _parse_iso(lot.get("createDate"))
                        if published and published < since:
                            continue
                        yield self._to_tender(lot, lot_id, published)
                    if payload.get("last"):
                        break

    @staticmethod
    def _to_tender(lot: dict[str, Any], lot_id: str, published: dt.datetime | None) -> RawTender:
        bidd_type = (lot.get("biddType") or {}).get("name", "")
        bidd_form = (lot.get("biddForm") or {}).get("name", "")
        category = (lot.get("category") or {}).get("name", "")
        return RawTender(
            external_id=lot_id,
            title=lot.get("lotName") or "",
            url=CARD_URL.format(id=lot_id),
            description=" ".join(
                x for x in (lot.get("lotDescription") or "", category, bidd_type) if x
            ),
            customer=(lot.get("sellerName") or ""),
            region=subject_name(lot.get("subjectRFCode")),
            price=lot.get("priceMin"),
            law="commercial",
            purchase_method=bidd_form or bidd_type,
            published_at=published,
            deadline_at=_parse_iso(lot.get("biddEndTime")),
            raw={
                "noticeNumber": lot.get("noticeNumber"),
                "lotStatus": lot.get("lotStatus"),
                "etpCode": lot.get("etpCode"),
                "category": category,
            },
        )
