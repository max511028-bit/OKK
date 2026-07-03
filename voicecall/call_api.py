"""Тонкая обёртка над Call API и Data API Novofon (JSON-RPC 2.0).

Используется для обхода ограничения SIP-линии типа "in" (только входящие):
вместо того чтобы звонить наружу напрямую через SIP (что физически
запрещено для этой линии на стороне Novofon), мы просим Call API
перезвонить НАМ (входящий звонок на нашу линию — разрешён) и уже своей
инфраструктурой дозвониться до кандидата и свести разговор.
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

# По умолчанию ходим не напрямую в Novofon, а через прокси на нашем VPS
# (postgresql.ru... портал, статический IP 195.208.119.67). Причина:
# Call API Novofon требует IP-whitelist, а IP ПК-агента обзвона —
# динамический (меняется провайдером), из-за чего whitelist регулярно
# "слетает" и обзвон падает с ip_not_whitelisted. IP сервера-прокси не
# меняется, поэтому в личном кабинете Novofon достаточно один раз
# разрешить его и больше не возвращаться к этой проблеме.
# Если прокси недоступен — можно временно вернуться на прямые URL через
# переменные окружения NOVOFON_CALL_API_URL / NOVOFON_DATA_API_URL.
#
# ВАЖНО: URL обязательно со слэшем на конце — иначе nginx отвечает
# 301-редиректом на .../ (location задан с trailing slash), а
# urllib.request при следовании за 301 на POST-запрос переигрывает его
# как GET без тела (историческое поведение, совместимое с браузерами) —
# JSON-RPC тело терялось, и Novofon получал пустой запрос вместо
# start.employee_call, отвечая непонятной ошибкой валидации.
CALL_API_URL = os.environ.get("NOVOFON_CALL_API_URL", "https://portalsth.ru/novofon-callapi/")
DATA_API_URL = os.environ.get("NOVOFON_DATA_API_URL", "https://portalsth.ru/novofon-dataapi/")

# Состояние "ноги" звонка в list.calls, означающее что стороны реально
# разговаривают (мост установлен, идёт живое аудио).
TALKING_STATE = "Разговор"


class NovofonAPIError(Exception):
    pass


def _rpc(url: str, method: str, params: dict) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=UTF-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise NovofonAPIError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")
    if "error" in data:
        raise NovofonAPIError(f"{method}: {data['error']}")
    return data["result"]


def start_employee_call(access_token: str, contact: str, employee_id: int,
                         employee_phone_number: str, virtual_phone_number: str) -> int:
    """Просит Novofon сначала позвонить на нашу линию (employee), а когда
    мы ответим — дозвониться до contact и свести разговор.
    Возвращает call_session_id."""
    result = _rpc(CALL_API_URL, "start.employee_call", {
        "access_token": access_token,
        "first_call": "employee",
        "switch_at_once": True,
        "show_virtual_phone_number": True,
        "virtual_phone_number": virtual_phone_number,
        "contact": contact,
        "employee": {"id": employee_id, "phone_number": employee_phone_number},
    })
    return result["data"]["call_session_id"]


def list_calls(access_token: str) -> list:
    result = _rpc(CALL_API_URL, "list.calls", {"access_token": access_token})
    return result.get("data", [])


def wait_for_contact_talking(access_token: str, call_session_id: int,
                              timeout: float = 45.0, poll_interval: float = 1.0) -> bool:
    """Опрашивает list.calls, пока одна из «ног» звонка не перейдёт в
    состояние TALKING_STATE — это значит что кандидат ответил и мост
    установлен. Если звонок вообще пропал из списка активных (кандидат
    не ответил, звонок завершился) — тоже прекращаем ждать.

    Возвращает (bridged, last_states):
      bridged — поймали ли состояние «Разговор»;
      last_states — список состояний ног звонка на ПОСЛЕДНЕМ успешном
        опросе (для диагностики: реальный случай 2026-07-03 — кандидат
        говорил «алло алло» в трубку, а Talking так и не увидели за 45с;
        без этих состояний невозможно было понять, что именно
        происходило со звонком по мнению Novofon)."""
    deadline = time.time() + timeout
    seen_at_least_once = False
    last_states: list = []
    while time.time() < deadline:
        try:
            calls = list_calls(access_token)
        except (NovofonAPIError, urllib.error.URLError, TimeoutError):
            # Единичный сетевой сбой при опросе не должен обрывать весь
            # звонок — сам звонок в это время продолжается независимо от
            # того, смогли ли мы прямо сейчас спросить его статус.
            # Пробуем ещё раз на следующей итерации.
            time.sleep(poll_interval)
            continue
        found = None
        for call in calls:
            if call.get("call_session_id") == call_session_id:
                found = call
                break
        if found:
            seen_at_least_once = True
            last_states = [leg.get("state") for leg in found.get("legs", [])]
            for leg in found.get("legs", []):
                if leg.get("state") == TALKING_STATE:
                    return True, last_states
        elif seen_at_least_once:
            # звонок был в списке, а потом пропал — значит уже завершился
            return False, last_states
        time.sleep(poll_interval)
    return False, last_states


# Прямая ссылка на прослушку/скачивание записи — playback URL Novofon,
# найдено эмпирически по документации Data API (get.calls_report,
# поле call_records): id из этого отчёта совпадает с call_session_id,
# который возвращает start.employee_call. Отдельного метода на
# "получить ссылку по id звонка" в API нет — только полный отчёт за
# период, в котором нужно найти свою строку.
RECORDING_URL_TEMPLATE = "https://app.novofon.ru/system/media/talk/{communication_id}/{record_id}/"
WAV_URL_TEMPLATE = "https://app.novofon.ru/system/media/wav/{communication_id}/{record_id}/"


def _find_call_report_row(access_token: str, call_session_id: int,
                           lookback_minutes: float = 30.0) -> "dict | None":
    """Общий поиск строки отчёта по call_session_id — используется и
    get_recording_url(), и get_wav_track_urls(). Отдельного метода
    "получить по id звонка" в API нет, только полный отчёт за период."""
    now = datetime.now()
    date_from = (now - timedelta(minutes=lookback_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    date_till = (now + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    result = _rpc(DATA_API_URL, "get.calls_report", {
        "access_token": access_token,
        "date_from": date_from,
        "date_till": date_till,
    })
    for row in result.get("data", []):
        if row.get("id") == call_session_id:
            return row
    return None


def get_recording_url(access_token: str, call_session_id: int,
                       lookback_minutes: float = 30.0) -> "str | None":
    """Ищет запись разговора для конкретного call_session_id за последние
    lookback_minutes минут через get.calls_report (Data API) и строит
    прямую ссылку на прослушку/скачивание. Возвращает None если записи
    ещё нет (Novofon обрабатывает её не мгновенно после звонка — вызывающий
    код должен повторить попытку через несколько секунд) или если звонок
    не был реально установлен (недозвон/автоответчик без разговора —
    записи в принципе не будет)."""
    row = _find_call_report_row(access_token, call_session_id, lookback_minutes)
    if not row:
        return None
    records = row.get("call_records") or []
    if not records:
        return None
    return RECORDING_URL_TEMPLATE.format(
        communication_id=row["communication_id"], record_id=records[0])


def get_wav_track_urls(access_token: str, call_session_id: int,
                        lookback_minutes: float = 30.0) -> "list[str] | None":
    """Ссылки на WAV-дорожки разговора (wav_call_records) — обычно ДВЕ,
    по одной на каждую «ногу» звонка (эмпирически проверено: одна почти
    целиком голос кандидата, вторая — наша сторона/шум). Пригождаются
    для повторного пакетного распознавания после звонка (см.
    dispatch_agent._recheck_recording) — WAV не нужно перекодировать в
    отличие от call_records (mp3)."""
    row = _find_call_report_row(access_token, call_session_id, lookback_minutes)
    if not row:
        return None
    records = row.get("wav_call_records") or []
    if not records:
        return None
    return [WAV_URL_TEMPLATE.format(communication_id=row["communication_id"], record_id=r)
            for r in records]
