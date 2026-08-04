"""Площадки, к которым сейчас нет автоматического доступа.

Каждая из них либо закрыта антибот-защитой (JS-челлендж, капча), либо не
открывается из текущей сети вообще. Выдумывать для них парсер бессмысленно —
он бы просто врал. Вместо этого коннектор честно проверяет доступ и пишет в
журнал, что именно мешает: сменится сеть или появится официальный API —
это сразу будет видно, и коннектор можно будет дописать.

Госзакупки всех этих площадок и так приходят через ЕИС: 44-ФЗ и 223-ФЗ обязаны
публиковаться в zakupki.gov.ru. Недостаёт только их коммерческих секций.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterator

from .base import BaseSource, RawTender, SourceUnavailable, register

log = logging.getLogger(__name__)

# Маркеры антибот-заглушек в теле ответа.
BLOCK_MARKERS = ("captcha", "ddos-guard", "checking your browser", "проверка браузера")


class UnreachableSource(BaseSource):
    """Проверяет доступность площадки и объясняет, почему сбор невозможен."""

    probe_url: str = ""
    reason: str = ""
    enabled_by_default = False

    def fetch(
        self,
        since: dt.datetime,
        settings: dict[str, Any],
        credentials: tuple[str, str] | None = None,
        queries: list[str] | None = None,
    ) -> Iterator[RawTender]:
        cfg = {**self.default_settings, **(settings or {})}
        url = cfg.get("probe_url") or self.probe_url
        try:
            with self.new_client(verify=False) as client:
                response = client.get(url)
        except Exception as exc:  # noqa: BLE001 — нам важен сам факт и текст причины
            raise SourceUnavailable(
                f"{self.title}: сайт не открывается из этой сети ({type(exc).__name__}). {self.reason}"
            ) from exc

        body = response.text.lower()
        blocked = [marker for marker in BLOCK_MARKERS if marker in body]
        if blocked:
            raise SourceUnavailable(
                f"{self.title}: площадка отдаёт антибот-заглушку ({', '.join(blocked)}). "
                f"{self.reason} Обход защиты не выполняется."
            )
        raise SourceUnavailable(
            f"{self.title}: сайт открылся (HTTP {response.status_code}, {len(response.text)} байт) — "
            "автоматической защиты не видно, коннектор для разбора списка ещё не написан. "
            f"{self.reason}"
        )
        yield  # pragma: no cover — делает метод генератором


@register
class RtsTenderSource(UnreachableSource):
    code = "rts_tender"
    location = "vps"
    title = "РТС-тендер"
    site_url = "https://www.rts-tender.ru"
    probe_url = "https://www.rts-tender.ru/"
    reason = "Госзакупки этой площадки приходят через ЕИС."
    notes = (
        "Одна из крупнейших федеральных ЭТП. Из текущей сети сайт не открывается "
        "(соединение обрывается). Её процедуры по 44-ФЗ и 223-ФЗ собираются через ЕИС."
    )
    default_settings: dict[str, Any] = {"probe_url": "https://www.rts-tender.ru/"}


@register
class RoseltorgSource(UnreachableSource):
    code = "roseltorg"
    location = "vps"
    title = "Росэлторг (ЕЭТП)"
    site_url = "https://www.roseltorg.ru"
    probe_url = "https://www.roseltorg.ru/"
    reason = "Госзакупки этой площадки приходят через ЕИС."
    notes = (
        "Единая электронная торговая площадка. Из текущей сети не открывается "
        "(таймаут TLS-рукопожатия). Госсегмент покрыт через ЕИС."
    )
    default_settings: dict[str, Any] = {"probe_url": "https://www.roseltorg.ru/"}


@register
class SberbankAstSource(UnreachableSource):
    code = "sberbank_ast"
    location = "any"
    title = "Сбербанк-АСТ (УТП)"
    site_url = "https://utp.sberbank-ast.ru"
    probe_url = "https://utp.sberbank-ast.ru/Trade/List/PurchaseList"
    reason = (
        "Список грузится защищённым POST-запросом, параметры которого площадка не публикует. "
        "Госзакупки идут через ЕИС."
    )
    notes = (
        "Универсальная торговая платформа Сбербанк-АСТ. Сайт открывается, но список "
        "процедур отдаётся закрытым внутренним запросом. 44-ФЗ и 223-ФЗ этой площадки "
        "собираются через ЕИС."
    )
    default_settings: dict[str, Any] = {
        "probe_url": "https://utp.sberbank-ast.ru/Trade/List/PurchaseList"
    }


@register
class TekTorgSource(UnreachableSource):
    code = "tektorg"
    location = "vps"
    title = "ТЭК-Торг"
    site_url = "https://www.tektorg.ru"
    probe_url = "https://www.tektorg.ru/sale/procedures"
    reason = "Госзакупки этой площадки приходят через ЕИС."
    notes = (
        "Площадка Роснефти и госзакупок. Программным клиентам отдаёт страницу с "
        "антибот-проверкой, хотя в браузере открывается нормально."
    )
    default_settings: dict[str, Any] = {"probe_url": "https://www.tektorg.ru/sale/procedures"}


@register
class OtcSource(UnreachableSource):
    code = "otc"
    location = "vps"
    title = "ОТС (otc.ru)"
    site_url = "https://otc.ru"
    probe_url = "https://otc.ru/tender"
    reason = "У площадки есть платный официальный API — через него доступ возможен легально."
    notes = (
        "Крупный агрегатор коммерческих закупок. Закрыт капчей для любых "
        "программных обращений; капчу мы не обходим."
    )
    default_settings: dict[str, Any] = {"probe_url": "https://otc.ru/tender"}


@register
class EtpGpbSource(UnreachableSource):
    code = "etp_gpb"
    location = "vps"
    title = "ЭТП ГПБ"
    site_url = "https://etpgpb.ru"
    probe_url = "https://etpgpb.ru/"
    reason = "Госзакупки этой площадки приходят через ЕИС."
    notes = "Площадка Газпромбанка. Из текущей сети не открывается (таймаут TLS)."
    default_settings: dict[str, Any] = {"probe_url": "https://etpgpb.ru/"}


@register
class FabrikantSource(UnreachableSource):
    code = "fabrikant"
    location = "vps"
    title = "Фабрикант"
    site_url = "https://www.fabrikant.ru"
    probe_url = "https://www.fabrikant.ru/"
    reason = "Коммерческая площадка, в ЕИС не дублируется."
    notes = "Из текущей сети не открывается (таймаут TLS-рукопожатия)."
    default_settings: dict[str, Any] = {"probe_url": "https://www.fabrikant.ru/"}


@register
class ZakazRfSource(UnreachableSource):
    code = "zakazrf"
    location = "vps"
    title = "ЗаказРФ (АГЗ РТ)"
    site_url = "https://etp.zakazrf.ru"
    probe_url = "https://etp.zakazrf.ru/"
    reason = "Госзакупки этой площадки приходят через ЕИС."
    notes = "Из текущей сети не открывается (таймаут TLS-рукопожатия)."
    default_settings: dict[str, Any] = {"probe_url": "https://etp.zakazrf.ru/"}
