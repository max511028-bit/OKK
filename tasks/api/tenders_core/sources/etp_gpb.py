"""ЭТП ГПБ — etpgpb.ru.

Площадка Газпромбанка: промышленность, ТЭК, крупные корпоративные
заказчики. Для аутсорсинга складского и производственного персонала —
профильный сегмент.

Как нашёлся источник данных (разведка 04.08.2026). Сайт на Nuxt, в HTML
закупок нет — подгружаются скриптом. В JS-бандлах видна схема адресов
`/api/v2/...`, рабочий эндпоинт — `/api/v2/procedures/`, формат JSON:API.

Два важных наблюдения, определивших устройство коннектора:

1. Параметр `search=` работает (438 тыс. записей сжимаются до 1253 по
   слову «персонал»), НО выдача идёт по релевантности и никакие sort-
   параметры её не меняют — проверял `sort`, `order`, `sort_by`. Для
   мониторинга это плохо: сегодняшняя закупка может оказаться на сороковой
   странице. Поэтому поиском НЕ пользуемся.

2. Обычный список, наоборот, строго упорядочен по дате публикации:
   стр.1 — сегодня, стр.120 — пять дней назад (≈24 страницы в день).
   Значит работаем как с Bidzaar: листаем ленту и останавливаемся, когда
   ушли глубже нужного периода.

Отдаёт то, чего нет у B2B-Center и Bidzaar: цену и регион.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterator

from .base import BaseSource, RawTender, SourceUnavailable, register

log = logging.getLogger(__name__)

BASE = "https://etpgpb.ru"
API = f"{BASE}/api/v2/procedures/"

# kind из ответа → наш признак закона
_KIND_TO_LAW = {
    "fz44": "44",
    "fz223": "223",
    "price_request": "commercial",
    "commercial": "commercial",
}


def _parse_dt(value: str | None) -> dt.datetime | None:
    """«2026-08-04T14:53:48.000+03:00» → наивный datetime."""
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = dt.datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    return parsed.replace(tzinfo=None)


def _price(value: Any) -> float | None:
    """Цена приходит строкой: «2477.99» или «1 639 344,26»."""
    if value in (None, "", "0", "0.00"):
        return None
    text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        amount = float(text)
    except ValueError:
        return None
    return amount or None


@register
class EtpGpbSource(BaseSource):
    location = "vps"          # из домашней сети площадка не открывается
    code = "etp_gpb"
    title = "ЭТП ГПБ"
    site_url = BASE
    query_driven = False      # берём ленту целиком, см. пункт 1 в шапке
    notes = (
        "Газпромбанк: промышленность, ТЭК, крупные корпоративные заказчики. "
        "Отдаёт цену и регион. По умолчанию госзакупки 44-ФЗ отбрасываются "
        "(настройка skip_kinds) — остаются коммерческие и 223-ФЗ."
    )
    default_settings: dict[str, Any] = {
        "page_size": 30,       # размер страницы задаёт сама площадка
        "max_pages": 400,      # ≈24 страницы в день, хватает на две недели
        # Владелец 04.08: «гос тендеры не нужны». 44-ФЗ отсекаем здесь,
        # чтобы не тащить их через всю цепочку отбора.
        "skip_kinds": ["fz44"],
    }

    def fetch(
        self,
        since: dt.datetime,
        settings: dict[str, Any],
        credentials: tuple[str, str] | None = None,
        queries: list[str] | None = None,
    ) -> Iterator[RawTender]:
        cfg = {**self.default_settings, **(settings or {})}
        skip = {str(k).lower() for k in (cfg.get("skip_kinds") or [])}
        seen: set[str] = set()

        with self.new_client(base_headers={"Accept": "application/json"}) as client:
            for page in range(1, int(cfg["max_pages"]) + 1):
                response = client.get(API, params={"page": page})
                if response.status_code != 200:
                    if page == 1:
                        raise SourceUnavailable(
                            f"ЭТП ГПБ вернул HTTP {response.status_code}")
                    break                      # лента кончилась
                try:
                    payload = response.json()
                except Exception as e:         # noqa: BLE001
                    raise SourceUnavailable(f"ЭТП ГПБ вернул не JSON: {e}")

                items = payload.get("data") or []
                if not items:
                    break

                reached_depth = False
                for item in items:
                    attrs = item.get("attributes") or {}
                    published = _parse_dt(attrs.get("date_published"))
                    # Лента строго по убыванию даты — как только ушли глубже
                    # периода, дальше листать незачем.
                    if published and published < since:
                        reached_depth = True
                        continue

                    kind = str(attrs.get("kind") or "").lower()
                    if kind in skip:
                        continue

                    external_id = str(attrs.get("registry_number")
                                      or item.get("id") or "").strip()
                    if not external_id or external_id in seen:
                        continue
                    seen.add(external_id)

                    regions = attrs.get("lot_regions") or []
                    if isinstance(regions, str):
                        regions = [regions]

                    yield RawTender(
                        external_id=external_id,
                        title=attrs.get("title") or "",
                        url=attrs.get("platform_url") or BASE,
                        description=attrs.get("procedure_type_name") or "",
                        customer=attrs.get("company_name") or "",
                        region=", ".join(str(r) for r in regions if r),
                        price=_price(attrs.get("amount")),
                        currency=attrs.get("currency_name") or "RUB",
                        law=_KIND_TO_LAW.get(kind, "commercial"),
                        purchase_method=attrs.get("procedure_type_name") or "",
                        published_at=published,
                        deadline_at=_parse_dt(attrs.get("end_registration")),
                        raw={"kind": kind, "stage": attrs.get("stage"),
                             "section": attrs.get("section_category_name"),
                             "lots_count": attrs.get("lots_count")},
                    )

                if reached_depth:
                    break
