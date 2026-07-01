"""Локальный агент обзвона — постоянно (но почти всегда простаивая) живёт
на этом ПК и опрашивает портал: не попросили ли начать обзвон какую-то
кампанию (кнопка «Начать обзвон» на портале). Как только просят — сам
забирает контакты по одному (SIP-линия — 1 канал, без параллелизма),
реально звонит через run_call_via_bridge(), результат отправляет обратно
на портал, и когда очередь кампании пуста — возвращается к тихому опросу.

Не CLI-скрипт с ручным запуском под конкретную кампанию — предполагается
что этот процесс просто всегда работает в фоне (Планировщик заданий
Windows, автозапуск при входе, по образцу scripts/sth-ai-watchdog.ps1).

Запуск (ручной, для теста):
    python voicecall/dispatch_agent.py
"""
import json
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from _sip_config import load_env, require
from dialog import load_scenario, DEFAULT_SCENARIO, all_reask_texts
from tts import synthesize_telephony_pcm, prewarm_scenario, DEFAULT_VOICE
from stt import warmup as stt_warmup
from phone_call import run_call_via_bridge

POLL_INTERVAL_SEC = 20
RESULT_POST_RETRIES = 3
RESULT_POST_RETRY_DELAY = 5


def _rpc(base_url: str, method: str, path: str, token: str = "",
         params: dict = None, json_body: dict = None) -> dict:
    url = base_url.rstrip("/") + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _auth(base_url: str, password: str) -> str:
    result = _rpc(base_url, "POST", "/auth/check", json_body={"password": password, "kind": "portal"})
    return result["token"]


def _post_result_with_retries(base_url: str, token: str, contact_id: int, result: dict) -> None:
    body = {
        "contact_id": contact_id,
        "status": result.get("status"),
        "verdict": result.get("verdict"),
        "stop_reason": result.get("stop_reason"),
        "answers": result.get("answers") or {},
        "notes": result.get("notes") or {},
        "transcript": result.get("transcript") or [],
        "duration_s": result.get("duration_s"),
        "error": result.get("error"),
    }
    for attempt in range(1, RESULT_POST_RETRIES + 1):
        try:
            _rpc(base_url, "POST", "/voicecall/dispatch/result", token=token, json_body=body)
            return
        except Exception as e:
            print(f"⚠️  Не удалось отправить результат (попытка {attempt}/{RESULT_POST_RETRIES}): {e}",
                  flush=True)
            if attempt < RESULT_POST_RETRIES:
                time.sleep(RESULT_POST_RETRY_DELAY)
    # Все попытки исчерпаны — не теряем данные звонка молча, пишем в файл
    # рядом со скриптом, чтобы можно было довнести вручную.
    try:
        with open("dispatch_agent_failed_results.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"contact_id": contact_id, "result": body}, ensure_ascii=False) + "\n")
        print(f"❌ Результат для contact_id={contact_id} сохранён локально "
              f"в dispatch_agent_failed_results.jsonl — отправь вручную позже.", flush=True)
    except Exception as e:
        print(f"❌ Не смог даже сохранить результат локально: {e}", flush=True)


def _run_campaign(base_url: str, token: str, campaign_id: int, scenario_id: str) -> None:
    scenario = load_scenario(scenario_id)
    print(f"Прогрев TTS для сценария «{scenario['name']}»...", flush=True)
    prewarm_scenario(scenario, voice=DEFAULT_VOICE, verbose=False)
    for t in all_reask_texts():
        synthesize_telephony_pcm(t, voice=DEFAULT_VOICE)

    while True:
        try:
            claim = _rpc(base_url, "POST", "/voicecall/dispatch/claim", token=token,
                         params={"campaign_id": campaign_id})
        except Exception as e:
            print(f"⚠️  Не смог забрать следующий контакт: {e}", flush=True)
            time.sleep(RESULT_POST_RETRY_DELAY)
            continue

        if not claim.get("contact_id"):
            print(f"Кампания {campaign_id} обзвонена, возвращаюсь к опросу.", flush=True)
            return

        contact_id = claim["contact_id"]
        phone = claim["phone"]
        name = claim.get("name") or ""
        known_answers = claim.get("known_answers") or {}
        print(f"→ Звоню contact_id={contact_id}, телефон +{phone}"
              + (f", известно заранее: {known_answers}" if known_answers else ""), flush=True)

        result = run_call_via_bridge(
            phone, scenario_id, candidate_name=name,
            known_answers=known_answers, on_log=print,
        )
        print(f"← Итог: status={result.get('status')} verdict={result.get('verdict')}", flush=True)
        _post_result_with_retries(base_url, token, contact_id, result)


def main():
    env = load_env()
    base_url = require(env, "PORTAL_URL")
    password = require(env, "PORTAL_PASSWORD")

    print("Агент обзвона запущен.")
    print("Прогрев Vosk-модели...")
    stt_warmup()

    token = None
    while True:
        if token is None:
            try:
                token = _auth(base_url, password)
                print("Авторизован на портале.", flush=True)
            except Exception as e:
                print(f"⚠️  Не смог авторизоваться на портале: {e}. Повтор через {POLL_INTERVAL_SEC}с.",
                      flush=True)
                time.sleep(POLL_INTERVAL_SEC)
                continue

        try:
            poll = _rpc(base_url, "GET", "/voicecall/dispatch/poll", token=token)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                print("Токен протух/неверен, переавторизуюсь.", flush=True)
                token = None
            else:
                print(f"⚠️  Ошибка опроса портала: {e}", flush=True)
            time.sleep(POLL_INTERVAL_SEC)
            continue
        except Exception as e:
            print(f"⚠️  Портал недоступен: {e}. Повтор через {POLL_INTERVAL_SEC}с.", flush=True)
            time.sleep(POLL_INTERVAL_SEC)
            continue

        campaign_id = poll.get("campaign_id")
        if not campaign_id:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        print(f"Портал попросил начать обзвон кампании {campaign_id}.", flush=True)
        _run_campaign(base_url, token, campaign_id, poll["scenario_id"])


if __name__ == "__main__":
    main()
