"""Тонкая обёртка над Call API и Data API Novofon (JSON-RPC 2.0).

Используется для обхода ограничения SIP-линии типа "in" (только входящие):
вместо того чтобы звонить наружу напрямую через SIP (что физически
запрещено для этой линии на стороне Novofon), мы просим Call API
перезвонить НАМ (входящий звонок на нашу линию — разрешён) и уже своей
инфраструктурой дозвониться до кандидата и свести разговор.
"""
import json
import time
import urllib.error
import urllib.request

CALL_API_URL = "https://callapi-jsonrpc.novofon.ru/v4.0"
DATA_API_URL = "https://dataapi-jsonrpc.novofon.ru/v2.0"

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
    не ответил, звонок завершился) — тоже прекращаем ждать."""
    deadline = time.time() + timeout
    seen_at_least_once = False
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
            for leg in found.get("legs", []):
                if leg.get("state") == TALKING_STATE:
                    return True
        elif seen_at_least_once:
            # звонок был в списке, а потом пропал — значит уже завершился
            return False
        time.sleep(poll_interval)
    return False
