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
import threading
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from _sip_config import load_env, require
from dialog import all_reask_texts, FILLER_PHRASES
from tts import synthesize_telephony_pcm, prewarm_scenario, DEFAULT_VOICE
from stt import warmup as stt_warmup
from phone_call import run_call_via_bridge
import call_api

POLL_INTERVAL_SEC = 20
RESULT_POST_RETRIES = 3
RESULT_POST_RETRY_DELAY = 5
# Novofon обрабатывает запись не мгновенно после звонка — несколько
# попыток с паузами, не блокируя основной цикл обзвона (см. поток в
# _fetch_and_attach_recording).
RECORDING_FETCH_ATTEMPTS = 5
RECORDING_FETCH_DELAY_SEC = 8

# Перепроверка транскрипта (см. _recheck_transcript) реально грузит CPU —
# распознаёт ДВЕ WAV-дорожки той же тяжёлой Vosk-моделью, что использует
# ЖИВОЙ звонок в реальном времени. Фоновые потоки для НЕСКОЛЬКИХ подряд
# завершённых звонков могут запуститься одновременно (кампания раздаёт
# контакты быстро) — реальный случай на тесте 2026-07-03: пока 3-4 таких
# фоновых потока одновременно скачивали и распознавали записи, у
# ОДНОВРЕМЕННО идущего живого звонка не задетектировался ответ кандидата
# (Диана сказала "алло алло", а wait_for_contact_talking всё равно не
# поймал разговор за 45с) — похоже на нехватку CPU/сети в моменте.
# Семафор не блокирует ОЖИДАНИЕ готовности записи у Novofon (это лёгкий
# sleep+poll), только саму тяжёлую STT-перепроверку — она теперь всегда
# идёт по одной штуке за раз, не наваливаясь на живой звонок скопом.
_STT_RECHECK_BUSY = threading.Semaphore(1)


def _rpc(base_url: str, method: str, path: str, token: str = "",
         params: dict = None, json_body: dict = None, timeout: float = 30) -> dict:
    url = base_url.rstrip("/") + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _auth(base_url: str, password: str) -> str:
    result = _rpc(base_url, "POST", "/auth/check", json_body={"password": password, "kind": "portal"})
    return result["token"]


def _post_result_with_retries(base_url: str, token: str, contact_id: int, result: dict,
                               password: str) -> "tuple[bool, str]":
    """Возвращает (успех, актуальный_токен) — токен мог обновиться внутри
    (см. ниже про 401/403), вызывающий код должен продолжать работать
    с ВОЗВРАЩЁННЫМ токеном, а не с тем что передал."""
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
        "dropped_at_step": result.get("dropped_at_step"),
        "call_session_id": result.get("call_session_id"),
    }
    for attempt in range(1, RESULT_POST_RETRIES + 1):
        try:
            _rpc(base_url, "POST", "/voicecall/dispatch/result", token=token, json_body=body)
            return True, token
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                # Токен протух (например, после деплоя портала секрет
                # пересоздался) — раньше это просто убивало результат
                # звонка после 3 попыток с одним и тем же мёртвым
                # токеном. Переавторизуемся и пробуем снова.
                print("⚠️  Токен протух при отправке результата, переавторизуюсь.", flush=True)
                try:
                    token = _auth(base_url, password)
                except Exception as e2:
                    print(f"⚠️  Не смог переавторизоваться: {e2}", flush=True)
            else:
                print(f"⚠️  Не удалось отправить результат (попытка {attempt}/{RESULT_POST_RETRIES}): {e}",
                      flush=True)
            if attempt < RESULT_POST_RETRIES:
                time.sleep(RESULT_POST_RETRY_DELAY)
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
    return False, token


def _normalize_free_answers(base_url: str, scenario: dict, result: dict) -> None:
    """Ответы на свободные вопросы («Когда выйти?», «Опыт?») приходят
    сырым STT-текстом («от дата», «опять так», «дабы») — рекрутеру
    приходится гадать, что имел в виду кандидат. Прогоняем каждый такой
    ответ через LLM портала и записываем понятную формулировку, сохраняя
    сырьё рядом в скобках. Лучшее старание: любой сбой/таймаут — просто
    оставляем сырой текст как был. Вызывается ДО отправки результата,
    добавляет секунду-две между звонками — приемлемо."""
    free_steps = {}
    for st in scenario.get("steps", []):
        if st.get("expect") == "free":
            free_steps[st.get("crit", st.get("id"))] = st.get("bot", "")
    if not free_steps:
        return
    for store_name in ("answers", "notes"):
        store = result.get(store_name) or {}
        for crit, raw_val in list(store.items()):
            if crit not in free_steps:
                continue
            if not isinstance(raw_val, str) or not raw_val.strip():
                continue
            if raw_val.startswith("не распознано"):
                continue
            prompt = (
                "Телефонный опрос кандидата на вакансию. Бот спросил: "
                f"«{free_steps[crit]}»\n"
                f"Распознанный (возможно с ошибками STT) ответ кандидата: «{raw_val}»\n\n"
                "Сформулируй КРАТКО (2-6 слов), что кандидат скорее всего имел в виду. "
                "Если из текста смысл извлечь нельзя — ответь ровно: неразборчиво. "
                "Ответь только самой формулировкой, без пояснений и кавычек."
            )
            try:
                data = _rpc(base_url, "POST", "/ai/proxy/chat", json_body={
                    "model": "qwen3:1.7b",
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False, "think": False,
                    "options": {"temperature": 0.2, "num_predict": 40},
                }, timeout=20)
                norm = ((data.get("message") or {}).get("content") or "").strip().strip('"«»')
            except Exception:
                continue
            if not norm or len(norm) > 80:
                continue
            if norm.lower() == "неразборчиво":
                store[crit] = f"не распознано: {raw_val}"
            elif norm.lower() != raw_val.lower():
                store[crit] = f"{norm} (дословно: {raw_val})"
            print(f"✨ Нормализован ответ «{crit}»: {store[crit]}", flush=True)


def _recheck_verdict(base_url: str, verdict: str, stop_reason: str, combined_transcript: str):
    """Сверяет причину автоотказа (verdict='stopped') с более точной
    пакетной перепроверкой записи — пункт 7 доработок 2026-07: реальный
    случай раньше (кандидат сказал "не было судимостей", live-STT
    услышал "глебова", LLM с одного слова решила что это "да" — кандидата
    ошибочно отклонили). Штатный reask-before-LLM фикс снижает такие
    случаи в реальном времени, но не отменяет их полностью — это вторая,
    более медленная и точная линия обороны ПОСЛЕ звонка: если пакетная
    перепроверка (без ограничений словаря, по чистой записи) не
    подтверждает причину отказа, помечаем попытку на ручную проверку
    рекрутёром вместо слепого доверия live-результату.

    Возвращает (needs_review: bool, review_note: str|None). При любой
    ошибке/недоступности LLM — (False, None): лучшее старание, не
    подменяет собой отказоустойчивость всего пайплайна."""
    if verdict != "stopped" or not stop_reason or not combined_transcript.strip():
        return False, None
    prompt = (
        "Автоматический телефонный опрос кандидата на вакансию. Бот прервал "
        f"разговор и отказал кандидату по причине: «{stop_reason}». Решение "
        "принято по распознаванию речи В РЕАЛЬНОМ ВРЕМЕНИ, которое иногда "
        "путает слова на плохой связи.\n\n"
        "Вот более точная перепроверка ОБЕИХ дорожек записи разговора "
        "(бот и кандидат вперемешку, без ограничения словаря):\n"
        f"{combined_transcript}\n\n"
        "Подтверждает ли эта перепроверка причину отказа, или похоже что "
        "live-распознавание ошиблось (кандидат говорил что-то другое, а "
        "услышали не то)? Ответь строго одним словом: подтверждено — если "
        "текст явно подтверждает причину отказа; проверить — если "
        "не подтверждает, противоречит или разговор в этом месте неразборчив."
    )
    try:
        data = _rpc(base_url, "POST", "/ai/proxy/chat", json_body={
            "model": "qwen3:1.7b",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False, "think": False,
            "options": {"temperature": 0.1, "num_predict": 12},
        }, timeout=15)
        answer = ((data.get("message") or {}).get("content") or "").strip().lower()
    except Exception as e:
        print(f"⚠️  Сверка вердикта с записью пропущена: {e}", flush=True)
        return False, None
    if "провер" in answer:
        return True, f"Автосверка: перепроверка записи не однозначно подтверждает причину «{stop_reason}» — проверьте вручную."
    return False, None


def _recheck_transcript(base_url: str, token: str, contact_id: int,
                         api_secret: str, call_session_id: int,
                         verdict: str = "", stop_reason: str = "") -> None:
    """Пакетная перепроверка распознавания по WAV-дорожкам разговора (без
    потоковых огрехов реального времени — эмпирически заметно точнее).
    Novofon хранит ОТДЕЛЬНУЮ дорожку на каждую «ногу» звонка, но
    надёжного способа автоматически определить какая из них кандидата
    не нашлось (пробовали сравнивать с текстом бота по пересечению
    слов — на реальной записи ошиблось: короткие частые слова дают
    случайные совпадения). Поэтому просто прикладываем ОБЕ дорожки с
    пометкой — обычно человеку с одного взгляда понятно, где бот
    (гладкие книжные фразы), а где кандидат (короткие живые ответы)."""
    import tempfile
    import urllib.request as _ur
    import os as _os
    from stt import recognize_wav_file

    urls = call_api.get_wav_track_urls(api_secret, call_session_id)
    if not urls:
        return

    # Скачивание — лёгкое, распознавание — тяжёлое (та же Vosk-модель,
    # что и живой звонок). Держим только STT-часть под семафором, чтобы
    # несколько параллельных перепроверок не наваливались на CPU разом
    # и не мешали текущему живому разговору (см. комментарий у
    # _STT_RECHECK_BUSY).
    transcripts = []
    for url in urls:
        tmp_path = tempfile.mktemp(suffix=".wav")
        try:
            _ur.urlretrieve(url, tmp_path)
            with _STT_RECHECK_BUSY:
                transcripts.append(recognize_wav_file(tmp_path))
        except Exception as e:
            print(f"⚠️  Не смог обработать дорожку записи: {e}", flush=True)
            transcripts.append("")
        finally:
            try: _os.remove(tmp_path)
            except Exception: pass

    if not any(t.strip() for t in transcripts):
        return
    combined = "\n\n".join(
        f"[Дорожка {i+1}] {t.strip() or '(тишина/не распознано)'}"
        for i, t in enumerate(transcripts)
    )
    needs_review, review_note = _recheck_verdict(base_url, verdict, stop_reason, combined)
    _rpc(base_url, "POST", "/voicecall/dispatch/recheck-transcript", token=token,
         json_body={"contact_id": contact_id, "recheck_transcript": combined,
                    "needs_review": needs_review, "review_note": review_note}, timeout=15)
    print(f"🔍 Перепроверенный транскрипт contact_id={contact_id} прикреплён."
          + (" ⚠️ ПОМЕЧЕН НА ПРОВЕРКУ." if needs_review else ""), flush=True)


def _fetch_and_attach_recording(base_url: str, token: str, contact_id: int,
                                 call_session_id, verdict: str = "", stop_reason: str = "") -> None:
    """Фоновый поток (не блокирует основной цикл обзвона): Novofon
    обрабатывает запись разговора не мгновенно после звонка, поэтому
    пробуем несколько раз с паузой. Лучшее старание — если записи нет
    вообще (недозвон/автоответчик) или Novofon так и не отдал её за все
    попытки, просто молча сдаёмся. Как только запись готова — пробуем
    заодно и перепроверку транскрипта (см. _recheck_transcript), т.к.
    обе WAV-дорожки обычно готовы одновременно с mp3."""
    if not call_session_id:
        return
    try:
        env = load_env()
        api_secret = require(env, "NOVOFON_API_SECRET")
    except SystemExit:
        return
    for attempt in range(1, RECORDING_FETCH_ATTEMPTS + 1):
        time.sleep(RECORDING_FETCH_DELAY_SEC)
        try:
            url = call_api.get_recording_url(api_secret, call_session_id)
        except Exception:
            url = None
        if url:
            try:
                _rpc(base_url, "POST", "/voicecall/dispatch/recording", token=token,
                     json_body={"contact_id": contact_id, "recording_url": url}, timeout=10)
                print(f"🎙 Запись звонка contact_id={contact_id} прикреплена.", flush=True)
            except Exception as e:
                print(f"⚠️  Не смог отправить ссылку на запись: {e}", flush=True)
            try:
                _recheck_transcript(base_url, token, contact_id, api_secret, call_session_id,
                                     verdict=verdict, stop_reason=stop_reason)
            except Exception as e:
                print(f"⚠️  Перепроверка транскрипта не удалась: {e}", flush=True)
            return


def _load_scenario_from_portal(base_url: str, scenario_id: str) -> dict:
    """Сценарии из конструктора живут в БД портала, не в локальных файлах
    этого ПК — грузим через тот же эндпоинт, что и фронтенд/билдер."""
    from urllib.parse import quote
    return _rpc(base_url, "GET", f"/voicecall/scripts/{quote(scenario_id, safe='')}")


def _run_campaign(base_url: str, token: str, campaign_id: int, scenario_id: str,
                   password: str) -> str:
    """Возвращает актуальный токен — он мог обновиться внутри (см. ниже
    про 401/403 в claim()/result()), вызывающий код (main()) должен
    продолжать опрос с ВОЗВРАЩЁННЫМ токеном."""
    scenario = _load_scenario_from_portal(base_url, scenario_id)
    # Настройки голоса сценария (Часть 2 доработок 2026-07) — прогрев
    # ОБЯЗАН использовать ТОТ ЖЕ voice/rate, что реально прозвучит в
    # звонке (см. _run_dialog_loop в phone_call.py), иначе ключ TTS-кэша
    # не совпадёт и каждая фраза будет синтезироваться вживую посреди
    # разговора вместо мгновенной отдачи из кэша.
    _settings = scenario.get("settings") or {}
    _voice = _settings.get("voice") or DEFAULT_VOICE
    _rate = _settings.get("rate") or "+0%"
    print(f"Прогрев TTS для сценария «{scenario['name']}» (голос: {_voice}, скорость: {_rate})...", flush=True)
    prewarm_scenario(scenario, voice=_voice, rate=_rate, verbose=False,
                      extra_texts=FILLER_PHRASES if _settings.get("fillers") else None)
    for t in all_reask_texts():
        synthesize_telephony_pcm(t, voice=_voice, rate=_rate)

    while True:
        try:
            claim = _rpc(base_url, "POST", "/voicecall/dispatch/claim", token=token,
                         params={"campaign_id": campaign_id})
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                # Тот самый баг: токен протухает (например, портал
                # передеплоился и секрет пересоздался) прямо посреди
                # обзвона кампании — раньше этот цикл слал один и тот же
                # мёртвый токен вечно и ни один контакт больше не
                # забирался, пока агент не перезапустят руками.
                print("⚠️  Токен протух, переавторизуюсь.", flush=True)
                try:
                    token = _auth(base_url, password)
                    print("Авторизован на портале.", flush=True)
                except Exception as e2:
                    print(f"⚠️  Не смог переавторизоваться: {e2}", flush=True)
                    time.sleep(RESULT_POST_RETRY_DELAY)
                continue
            print(f"⚠️  Не смог забрать следующий контакт: {e}", flush=True)
            time.sleep(RESULT_POST_RETRY_DELAY)
            continue
        except Exception as e:
            print(f"⚠️  Не смог забрать следующий контакт: {e}", flush=True)
            time.sleep(RESULT_POST_RETRY_DELAY)
            continue

        if not claim.get("contact_id"):
            if claim.get("paused"):
                print(f"Кампания {campaign_id} на паузе, возвращаюсь к опросу.", flush=True)
            else:
                print(f"Кампания {campaign_id} обзвонена, возвращаюсь к опросу.", flush=True)
            return token

        contact_id = claim["contact_id"]
        phone = claim["phone"]
        name = claim.get("name") or ""
        known_answers = claim.get("known_answers") or {}
        print(f"→ Звоню contact_id={contact_id}, телефон +{phone}"
              + (f", известно заранее: {known_answers}" if known_answers else ""), flush=True)

        def push_live(transcript, _cid=contact_id):
            # Живой мониторинг на портале — лучшее старание, не должен
            # мешать самому звонку: сеть шлём с коротким таймаутом,
            # ошибки молча проглатываем (см. on_transcript_update в
            # phone_call.py, там тоже есть try/except с той же логикой).
            try:
                _rpc(base_url, "POST", "/voicecall/dispatch/live", token=token,
                     json_body={"contact_id": _cid, "transcript": transcript}, timeout=4)
            except Exception:
                pass

        result = run_call_via_bridge(
            phone, scenario_id, candidate_name=name,
            known_answers=known_answers, on_log=print,
            scenario=scenario, on_transcript_update=push_live,
        )
        print(f"← Итог: status={result.get('status')} verdict={result.get('verdict')}", flush=True)
        if result.get("status") == "answered_completed":
            try:
                _normalize_free_answers(base_url, scenario, result)
            except Exception as e:
                print(f"⚠️  Нормализация ответов пропущена: {e}", flush=True)
        posted, token = _post_result_with_retries(base_url, token, contact_id, result, password)
        if posted and result.get("call_session_id"):
            # В фоне — следующий контакт в очереди не должен ждать, пока
            # Novofon обработает запись разговора (может занять десятки секунд).
            threading.Thread(
                target=_fetch_and_attach_recording,
                args=(base_url, token, contact_id, result["call_session_id"]),
                kwargs={"verdict": result.get("verdict") or "",
                        "stop_reason": result.get("stop_reason") or ""},
                daemon=True,
            ).start()


def _raise_process_priority() -> None:
    """Пункт 3 доработок 2026-07: агент держит реал-тайм аудио звонка
    (SIP/RTP, Vosk-распознавание, AGC) — если параллельно на этом же ПК
    что-то грузит CPU (браузер, антивирус и т.п.), это реально может
    срывать звонок (реальный случай: контакт ответил "алло алло", а
    wait_for_contact_talking не поймал разговор — подозрение на нехватку
    CPU в моменте, см. коммит про троттлинг фоновой перепроверки записей).
    HIGH_PRIORITY_CLASS = 0x00000080 (Windows). Лучшее старание — на
    не-Windows или без прав просто идём дальше без приоритета.

    ВАЖНО: GetCurrentProcess() возвращает 64-битный псевдо-хэндл (-1 /
    0xFFFFFFFFFFFFFFFF). Без явного restype ctypes по умолчанию трактует
    возврат как 32-битный int и ОБРЕЗАЕТ хэндл — SetPriorityClass после
    этого получает мусор и падает с ERROR_INVALID_HANDLE, при этом
    ctypes.GetLastError() (без use_last_error=True в WinDLL) тоже не
    покажет реальный код ошибки. Проверено вживую на этом ПК: без
    restype/argtypes вызов молча проваливался КАЖДЫЙ раз."""
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.SetPriorityClass.restype = ctypes.c_int
        HIGH_PRIORITY_CLASS = 0x00000080
        handle = kernel32.GetCurrentProcess()
        ok = kernel32.SetPriorityClass(handle, HIGH_PRIORITY_CLASS)
        if ok:
            print("Приоритет процесса: HIGH", flush=True)
        else:
            err = ctypes.get_last_error()
            print(f"⚠️  Не смог поднять приоритет процесса (код ошибки {err}: {ctypes.FormatError(err)})", flush=True)
    except Exception as e:
        print(f"⚠️  Не смог поднять приоритет процесса: {type(e).__name__}: {e}", flush=True)


def main():
    _raise_process_priority()
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
        token = _run_campaign(base_url, token, campaign_id, poll["scenario_id"], password)


if __name__ == "__main__":
    main()
