"""Каркас коннекторов к площадкам.

Каждая площадка — отдельный класс-наследник BaseSource, который умеет одно:
отдать список свежих закупок за период. Всё остальное (фильтрация, дедуп,
запись в БД, журнал) делает пайплайн, коннекторам это знать не нужно.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from ..config import HTTP_RETRIES, HTTP_TIMEOUT, POLITE_DELAY_SECONDS, USER_AGENT

log = logging.getLogger(__name__)


class SourceUnavailable(RuntimeError):
    """Площадка временно недоступна (сеть, 5xx, блокировка). Прогон помечается как error."""


class SourceRequiresAuth(RuntimeError):
    """Площадка отдаёт данные только авторизованным. Нужны логин/пароль в админке."""


@dataclass
class RawTender:
    """Закупка в том виде, в каком её отдал коннектор."""

    external_id: str
    title: str
    url: str = ""
    description: str = ""
    customer: str = ""
    region: str = ""
    price: float | None = None
    currency: str = "RUB"
    law: str = "commercial"
    okpd2: str = ""
    purchase_method: str = ""
    published_at: dt.datetime | None = None
    deadline_at: dt.datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def default_ssl_context():
    """Контекст TLS на основе системного хранилища Windows.

    Через него работают сертификаты, установленные в систему вручную, — в том
    числе российский корневой сертификат Минцифры, без которого не открываются
    госплощадки. Если truststore недоступен, откатываемся на бандл certifi.
    """
    try:
        import ssl

        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001 — отсутствие truststore не должно ронять сбор
        return True


class HttpClient:
    """Обёртка над httpx: ретраи, вежливые паузы, общий User-Agent."""

    def __init__(self, base_headers: dict[str, str] | None = None, verify: Any = None):
        if verify is None:
            verify = default_ssl_context()
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }
        headers.update(base_headers or {})
        self._client = httpx.Client(
            headers=headers,
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            verify=verify,
        )
        self._last_call = 0.0

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        delay = POLITE_DELAY_SECONDS + random.uniform(0, 0.4)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_call = time.monotonic()

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(HTTP_RETRIES + 1):
            self._wait()
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                log.warning("%s %s — ошибка сети (%s), попытка %s", method, url, exc, attempt + 1)
                time.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = SourceUnavailable(f"HTTP {response.status_code} на {url}")
                log.warning("%s %s — HTTP %s, попытка %s", method, url, response.status_code, attempt + 1)
                time.sleep(2.0 * (attempt + 1))
                continue
            return response
        raise SourceUnavailable(str(last_error) if last_error else f"не удалось получить {url}")

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class BaseSource:
    """Контракт коннектора."""

    code: str = ""
    title: str = ""
    site_url: str = ""
    requires_auth: bool = False
    # Включать ли площадку сразу при первой инициализации БД.
    enabled_by_default: bool = True
    # Короткая заметка для админки: что именно площадка отдаёт и чего не отдаёт.
    notes: str = ""
    default_settings: dict[str, Any] = {}

    # True — площадка ищет по фразам (им передаются ключевые слова направлений),
    # False — площадка отдаёт сплошной листинг за период, фильтруем уже у себя.
    query_driven: bool = False

    # Откуда площадка вообще достижима (замер 31.07.2026, см. 01-АРХИТЕКТУРА):
    #   'vps'  — только с портала: Роселторг, Фабрикант, ЭТП ГПБ, ЗаказРФ
    #   'home' — только из домашней сети: ЕИС (zakupki.gov.ru блокирует
    #            датацентровые адреса — не отвечает даже без проверки TLS)
    #   'any'  — отовсюду: B2B-Center, Bidzaar
    # Сборщик берёт только те площадки, чьё значение совпадает с его
    # собственным TR_LOCATION (или 'any').
    location: str = "any"

    def fetch(
        self,
        since: dt.datetime,
        settings: dict[str, Any],
        credentials: tuple[str, str] | None = None,
        queries: list[str] | None = None,
    ) -> Iterator[RawTender]:
        raise NotImplementedError

    # -- вспомогательное ---------------------------------------------------
    @staticmethod
    def new_client(**kwargs) -> HttpClient:
        return HttpClient(**kwargs)


_REGISTRY: dict[str, type[BaseSource]] = {}


def register(cls: type[BaseSource]) -> type[BaseSource]:
    if not cls.code:
        raise ValueError(f"{cls.__name__}: не задан code")
    _REGISTRY[cls.code] = cls
    return cls


def all_sources() -> dict[str, type[BaseSource]]:
    return dict(_REGISTRY)


def get_source(code: str) -> type[BaseSource] | None:
    return _REGISTRY.get(code)
