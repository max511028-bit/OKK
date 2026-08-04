"""B2B-Center — b2b-center.ru.

Крупнейшая коммерческая площадка РФ. Результаты поиска отдаются гостю обычным
HTML, без авторизации; цена и часть реквизитов видны только участникам, поэтому
price здесь обычно пустой — отбор идёт по тексту.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any, Iterator
from urllib.parse import urlencode, urljoin

from selectolax.parser import HTMLParser

from .base import BaseSource, RawTender, SourceUnavailable, register

log = logging.getLogger(__name__)

BASE = "https://www.b2b-center.ru"
SEARCH_URL = f"{BASE}/market/"
_ID_RE = re.compile(r"tender-(\d+)")
_NUM_RE = re.compile(r"№\s*(\d+)")


def _parse_dt(value: str) -> dt.datetime | None:
    value = value.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


@register
class B2BCenterSource(BaseSource):
    location = "any"
    code = "b2b_center"
    title = "B2B-Center"
    site_url = BASE
    query_driven = True
    notes = (
        "Коммерческие закупки. Список процедур доступен без входа; НМЦ и "
        "документация — только авторизованным участникам, поэтому цена в "
        "таблице обычно пустая."
    )
    default_settings: dict[str, Any] = {
        "max_pages": 3,
        "max_queries": 40,
        "include_archive": False,
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
            return

        seen: set[str] = set()
        pages_ok = 0          # сколько страниц реально пришло
        with self.new_client() as client:
            for query in queries:
                for page in range(1, int(cfg["max_pages"]) + 1):
                    # Форма поиска площадки ждёт именно f_keyword + searching=1;
                    # привычный q= молча игнорируется и отдаёт просто свежий список.
                    params = {
                        "f_keyword": query,
                        "searching": 1,
                        "trade": "all",
                        "pagesize": 100,
                        "page": page,
                    }
                    if cfg.get("include_archive"):
                        params["show"] = "all"
                    response = client.get(f"{SEARCH_URL}?{urlencode(params)}")
                    if response.status_code != 200:
                        raise SourceUnavailable(f"B2B-Center вернул HTTP {response.status_code}")

                    rows = list(self._parse_rows(response.text))
                    if not rows:
                        break
                    pages_ok += 1
                    stop = False
                    for tender in rows:
                        if tender.external_id in seen:
                            continue
                        seen.add(tender.external_id)
                        # Список отсортирован по дате: как только ушли глубже
                        # интересующего периода, дальше листать смысла нет.
                        if tender.published_at and tender.published_at < since:
                            stop = True
                            continue
                        yield tender
                    if stop:
                        break

        # Ни одна из десятков поисковых фраз не дала НИ ОДНОЙ карточки —
        # это не «ничего не подошло», а «нас не пустили». 04.08 площадка
        # начала отвечать 200 OK с пустой выдачей после интенсивных
        # прогонов, и коннектор бодро отрапортовал «ok, найдено 0» —
        # молчаливый ноль, неотличимый от честного результата.
        # Одна пустая фраза — норма, все сразу — сбой.
        if queries and pages_ok == 0:
            raise SourceUnavailable(
                f"B2B-Center: ни одна из {len(queries)} поисковых фраз не вернула "
                f"результатов. Похоже на ограничение по частоте — обычно снимается "
                f"за несколько часов.")

    # ------------------------------------------------------------------
    def _parse_rows(self, html_text: str) -> Iterator[RawTender]:
        tree = HTMLParser(html_text)
        for link in tree.css("a.search-results-title"):
            href = link.attributes.get("href") or ""
            m = _ID_RE.search(href)
            title_text = link.text(separator=" ", strip=True)
            if not m:
                m = _NUM_RE.search(title_text)
            if not m:
                continue
            external_id = m.group(1)

            desc_node = link.css_first(".search-results-title-desc")
            description = desc_node.text(separator=" ", strip=True) if desc_node else ""
            # В заголовке ссылки лежит «<Тип процедуры> № 123 <описание>».
            head = title_text.replace(description, "").strip()
            purchase_method = re.sub(r"\s*№.*$", "", head).strip()

            row = link.parent
            while row is not None and row.tag != "tr":
                row = row.parent
            customer, published_at, deadline_at, category = "", None, None, ""
            if row is not None:
                cells = row.css("td")
                if cells:
                    small = cells[0].css_first("small")
                    category = small.text(strip=True) if small else ""
                if len(cells) > 1:
                    customer = cells[1].text(separator=" ", strip=True)
                dates = [
                    _parse_dt(c.text(strip=True))
                    for c in cells[2:5]
                    if _parse_dt(c.text(strip=True))
                ]
                if dates:
                    published_at = dates[0]
                if len(dates) > 1:
                    deadline_at = dates[1]

            yield RawTender(
                external_id=external_id,
                title=description or head,
                url=urljoin(BASE, href.split("#")[0]),
                description=" ".join(x for x in (head, description, category) if x),
                customer=customer,
                law="commercial",
                purchase_method=purchase_method,
                published_at=published_at,
                deadline_at=deadline_at,
                raw={"category": category},
            )
