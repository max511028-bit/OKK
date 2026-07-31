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
import re
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
RECORDING_FETCH_ATTEMPTS = 8
RECORDING_FETCH_DELAY_SEC = 10
# Вторая серия попыток забрать запись (17.07: у 4 «годен» запись так и не
# пришла за первую серию → финальный статус стоял вслепую). После второй
# серии — принудительная финализация с пометкой «без записи».
RECORDING_RETRY_ROUND2_DELAY_SEC = 120

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


def _llm_ask(base_url: str, prompt: str, num_predict: int = 12,
             model: str = "qwen3:1.7b") -> str:
    """Один короткий запрос к LLM портала. Возвращает строку ответа
    (lower/strip) или "" при любой ошибке (лучшее старание)."""
    try:
        data = _rpc(base_url, "POST", "/ai/proxy/chat", json_body={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False, "think": False,
            "options": {"temperature": 0.1, "num_predict": num_predict},
        }, timeout=15)
        return ((data.get("message") or {}).get("content") or "").strip().lower()
    except Exception as e:
        print(f"⚠️  LLM-запрос пропущен: {e}", flush=True)
        return ""


# Минимум слов в дорожке кандидата, чтобы вообще судить «человек/робот» по
# записи (28.07). Меньше — не переклассифицируем: на тесте «НДЗ Пермь» живые
# уехали в автоответчики по «алло» и «добрый какой». В live-проверке такой
# порог был изначально (dialog.llm_is_robot_live).
ROBOT_CHECK_MIN_WORDS = 5


def _llm_is_robot_secretary(base_url: str, combined_transcript: str) -> bool:
    """LLM-классификация: собеседник в записи — живой кандидат или робот-
    секретарь/голосовой ассистент/автоответчик? Нужна там, где точные
    фразы бьют мимо из-за искажений STT или новых формулировок робота.

    Промпт заточен под мимикрию (17.07): ключевой различитель — робот НЕ
    даёт конкретных данных о себе (возраст, город), а переспрашивает и
    предлагает передать сообщение.

    Модель — qwen3:1.7b. Проверено на записях 17.07: БОЛЬШАЯ qwen3:8b тут
    ХУЖЕ (с think=False отвечает «робот» на всё → потеря живых; с think=True
    рассуждение часто не укладывается в бюджет → пустой ответ). Ловлю
    мимикрирующих роботов отдельным ДЕТЕРМИНИРОВАННЫМ сигналом
    _passed_but_no_age (см. ниже) — на данных 17.07 он дал 0 ложных на
    живых и поймал всех роботов, в отличие от любой LLM.

    Осторожно: по умолчанию считаем ЧЕЛОВЕКОМ — реклассим в робота только
    при явном «робот», чтобы не потерять живого кандидата.

    28.07: добавлен порог МИНИМУМА СЛОВ. В live-проверке (llm_is_robot_live)
    он был с самого начала («на паре слов не судим»), а здесь его не было —
    и на тесте «НДЗ Пермь» в автоответчики уехали живые люди по огрызкам
    записи: «алло» (1 слово) и «добрый какой» (2 слова). Под удар попадали
    именно те, у кого плохая связь или короткий ответ."""
    is_robot, _ = _llm_robot_verdict(base_url, combined_transcript, votes=1)
    return is_robot


# Сколько раз опрашивать модель, когда есть косвенное подозрение на робота
# (30.07, контакт 564 «Мария»). Причина: на её дорожке тот же промпт даёт
# «робот» 3 раза из 3 при ручном прогоне, а в бою ответил «человек» — на
# пограничных записях qwen3:1.7b нестабильна, и один опрос это монетка.
# Проверка фоновая, ПОСЛЕ звонка (num_predict=6) — задержка никому не мешает.
ROBOT_CHECK_VOTES = 3


def _robot_check_is_suspicious(live_answers: dict, candidate_track: str) -> bool:
    """Стоит ли переспросить модель несколько раз. Косвенные признаки:
    возраст за разговор не прозвучал, либо часть ответов не распозналась —
    ровно та картина, на которой модель и «плавает»."""
    if not _candidate_stated_age(candidate_track or ""):
        return True
    for v in (live_answers or {}).values():
        if isinstance(v, str) and "не распознано" in v:
            return True
    return False


def _llm_robot_verdict(base_url: str, transcript: str,
                       votes: int = 1) -> "tuple[bool, bool]":
    """(робот?, удалось ли вообще спросить).

    Второе значение важнее, чем кажется. Раньше любая ошибка LLM молча
    возвращала False, то есть «человек» — а в логе кампаний это 20 ответов
    «Bad Gateway» и 14 таймаутов. Автоответчик при сетевом сбое тихо
    оставался «годным». Теперь вызывающий код различает «модель сказала
    человек» и «спросить не удалось» и во втором случае ставит ⚠.

    votes>1 — опрашиваем несколько раз и берём большинство (см.
    ROBOT_CHECK_VOTES). Пустой ответ моделью не считается голосом."""
    if not transcript.strip():
        return False, True  # судить не о чем — это ответ, а не сбой
    if len(transcript.split()) < ROBOT_CHECK_MIN_WORDS:
        return False, True  # слишком мало речи, чтобы судить
    prompt = _robot_check_prompt(transcript)
    robot = human = 0
    for _ in range(max(1, votes)):
        ans = _llm_ask(base_url, prompt, num_predict=6)
        if not ans:
            continue  # сбой или пустой ответ — не голос
        if "робот" in ans and "человек" not in ans:
            robot += 1
        else:
            human += 1
    if robot + human == 0:
        return False, False
    return robot > human, True


def _robot_check_prompt(combined_transcript: str) -> str:
    return (
        "Ты анализируешь расшифровку телефонного звонка. Бот-рекрутёр (Диана) "
        "задаёт кандидату короткие вопросы про вакансию: ищет ли работу, "
        "сколько лет, гражданство, город. С ботом кто-то говорит.\n\n"
        f"Расшифровка (обе стороны вперемешку):\n{combined_transcript}\n\n"
        "ОПРЕДЕЛИ, кто отвечал боту:\n"
        "• ЖИВОЙ кандидат — называет КОНКРЕТНЫЕ данные о СЕБЕ: свой возраст "
        "числом, свой город, «да, ищу работу», отвечает по существу вопроса.\n"
        "• РОБОТ (голосовой ассистент/секретарь Яндекса/автоответчик) — даже "
        "если звучит как живой человек и поддакивает («да, говорите»): НЕ "
        "называет свой возраст и город, вместо ответов задаёт встречные "
        "вопросы («кто звонит?», «откуда у вас мой номер?», «представьтесь»), "
        "тянет время («нужно подумать», «посоветуюсь», «затрудняюсь ответить»), "
        "предлагает ПЕРЕДАТЬ сообщение или записать обращение, говорит что "
        "абонент не может ответить.\n\n"
        "Главный признак робота: за весь разговор так и НЕ прозвучало ни "
        "возраста, ни города кандидата — одни встречные вопросы и вода.\n\n"
        "Ответь ОДНИМ словом: человек / робот.")


# Возраст, названный кандидатом: числительные-стемы (без trailing \b — ломается
# на кириллице: «тридцать» = «тридцат»+«ь») ИЛИ двузначное число 16..70.
_AGE_WORD_RE = re.compile(
    r"(семнадцат|восемнадцат|девятнадцат|двадцат|тридцат|сорок|сорока|"
    r"пятьдесят|шестьдесят|семьдесят)")
_AGE_NUM_RE = re.compile(r"\b(1[6-9]|[2-6]\d|70)\b")


def _candidate_stated_age(candidate_track: str) -> bool:
    """Назвал ли собеседник возраст числом. Живой кандидат, отвечая
    рекрутёру на «сколько лет?», возраст называет; робот-секретарь —
    уклоняется (встречные вопросы, «передать сообщение»). Детерминированно."""
    t = candidate_track or ""
    return bool(_AGE_WORD_RE.search(t) or _AGE_NUM_RE.search(t))


# Конкретные факты о себе, которые называет живой кандидат и не называет
# робот-секретарь: возраст числом (см. _candidate_stated_age), гражданство,
# наличие медкнижки. Города списком не берём — их слишком много, а ошибка
# тут дорогая (лишний контакт уедет в автоответчики).
_PERSONAL_FACT_RE = re.compile(
    r"(\bрф\b|росси\w*|российск\w*|русск\w*|"
    r"узбек\w*|таджик\w*|киргиз\w*|кыргыз\w*|казах\w*|армен\w*|азербайдж\w*|"
    r"грузин\w*|украин\w*|белорус\w*|молдав\w*|туркмен\w*|"
    r"медкнижк\w*|медицинск\w+ книжк\w*|санитарн\w+ книжк\w*)"
)


def _candidate_gave_personal_data(candidate_track: str) -> bool:
    """Назвал ли собеседник хоть один конкретный факт о СЕБЕ (возраст,
    гражданство, медкнижка). Контр-сигнал против переклассификации в
    роботы: робот-секретарь таких данных не сообщает — он их спрашивает.
    Расширение _candidate_stated_age на случаи, когда до вопроса про
    возраст разговор не дошёл."""
    t = (candidate_track or "").lower().replace("ё", "е")
    if _candidate_stated_age(t):
        return True
    return bool(_PERSONAL_FACT_RE.search(t))


def _passed_but_no_age(scenario: dict, verdict: str, transcripts: list) -> bool:
    """Сигнатура «мимикрирующего робота-секретаря» (тест 17.07): вердикт
    ГОДЕН, сценарий СПРАШИВАЛ возраст, кандидат реально что-то говорил, но
    возраст числом за весь разговор так и не прозвучал → почти наверняка
    робот-секретарь, которого малая realtime-модель ошибочно провела как
    годного. НЕ переклассифицируем (вдруг живой с дефектной записью) —
    только помечаем ⚠. Проверено на 17.07: 0 ложных на живых, поймал всех
    мимикрирующих роботов (Янис/Наталья/Джамила и др.)."""
    if verdict != "passed":
        return False
    has_age_step = any((st.get("expect") == "age") for st in (scenario or {}).get("steps", []))
    if not has_age_step:
        return False
    cand = (transcripts[0] if transcripts else "").strip()
    if not cand or cand == "(тишина/не распознано)" or len(cand) < 8:
        return False  # кандидат толком не говорил — это другой случай
    return not _candidate_stated_age(cand)


def _llm_summary_for_review(base_url: str, combined_transcript: str) -> str:
    """Короткое саммари записи для контактов с ⚠ (16.07): рекрутёр читает
    два предложения вместо прослушивания. Лучшее старание — пусто при сбое.

    Защита от галлюцинаций (тест 21.07: на скудном тексте qwen3:1.7b
    сочинила «возраст — 25 лет, город — москва, согласие — да» для
    кандидата, который вообще не разговаривал): (1) на коротком/пустом
    транскрипте саммари не делаем; (2) промпт запрещает выдумывать;
    (3) числа в саммари сверяются с транскриптом — упомянута цифра,
    которой в разговоре не было, → саммари отбрасывается целиком."""
    words = combined_transcript.split()
    if len(words) < 15:
        return ""
    ans = _llm_ask(
        base_url,
        "Расшифровка телефонного звонка бота-рекрутёра (обе дорожки):\n"
        f"{combined_transcript[:2500]}\n\n"
        "Одним-двумя короткими предложениями для рекрутёра: кто отвечал "
        "(живой кандидат / робот / непонятно) и какие ключевые данные "
        "прозвучали. СТРОГО: упоминай возраст/город/согласие ТОЛЬКО если "
        "они буквально есть в расшифровке выше; если данных нет — напиши "
        "«данных мало». НИЧЕГО не выдумывай. Без вступлений.",
        num_predict=70)
    ans = ans[:300]
    # Grounding: каждое число из саммари должно звучать в транскрипте
    # (цифрой или словом — реюз _age_grounded: «25» ← «двадцать пять»).
    for num in re.findall(r"\d+", ans):
        try:
            if not _age_grounded(int(num), combined_transcript):
                return ""  # LLM упомянула число, которого в разговоре не было
        except Exception:
            return ""
    return ans


def _norm_answer(s: str) -> str:
    """Нормализация для сравнения/поиска: нижний регистр, только буквы/цифры,
    схлопнутые пробелы."""
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", str(s).lower()).split())


def _answer_grounded(ans: str, transcript: str) -> bool:
    """Защита от галлюцинаций LLM (реальный случай 2026-07-09: на мусорной
    записи модель выдумала «29»): принимаем верифицированный ответ, только
    если он реально звучит в расшифровке. Длинные слова (≥4 букв) — хотя бы
    одно есть в тексте; короткий ответ (да/нет/из 3 букв) — целиком."""
    t = _norm_answer(transcript)
    words = [w for w in _norm_answer(ans).split() if len(w) >= 4]
    if not words:
        return _norm_answer(ans) in t
    # Сверяем по ОСНОВЕ (первые 5 букв), а не точным совпадением: STT и LLM
    # часто дают слово в разной форме («российское» vs звучавшее «российская»).
    return any(w[:5] in t for w in words)


_RU_UNITS = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_RU_TEENS = {10: "десять", 11: "одиннадцать", 12: "двенадцать", 13: "тринадцать",
             14: "четырнадцать", 15: "пятнадцать", 16: "шестнадцать", 17: "семнадцать",
             18: "восемнадцать", 19: "девятнадцать"}
_RU_TENS = {20: "двадцать", 30: "тридцать", 40: "сорок", 50: "пятьдесят",
            60: "шестьдесят", 70: "семьдесят", 80: "восемьдесят", 90: "девяносто"}


def _age_grounded(age: int, transcript: str) -> bool:
    """Звучит ли возраст в записи (защита от галлюцинации LLM — реальный
    случай 2026-07-10: Еладжу поставили 29, хотя в записи внятного числа
    нет). Проверяем цифрами ИЛИ русскими числительными: 10-19 — одно слово;
    20-99 — десяток обязателен, единица (если есть) тоже должна звучать."""
    t = _norm_answer(transcript)
    if str(age) in t:
        return True
    if 10 <= age <= 19:
        return _RU_TEENS[age] in t
    tens, unit = (age // 10) * 10, age % 10
    tens_w = _RU_TENS.get(tens)
    if not tens_w or tens_w not in t:
        return False
    return unit == 0 or _RU_UNITS[unit] in t


def _recheck_critical_answers(base_url: str, scenario: dict, live_answers: dict,
                               combined_transcript: str):
    """Пере-валидация ответов по записи. Маленькая Vosk-модель в реальном
    времени теряет/путает слова (кандидат сказал «двадцать девять» → записалось
    «двадцать»; «российское» → «красивая»), а БОЛЬШАЯ модель по чистой записи
    берёт их верно. Логика по типам поля:
      - возраст: проверяем ВСЕГДА (числа путаются чаще всего), ⚠ на расхождение;
      - критичные да/нет (crime/gender_male/shifts/стоп-yesno): добираем ТОЛЬКО
        потерянные (live «не распознано»), ⚠; доверяем реалтайму иначе;
      - ВСЕ прочие достигнутые вопросы (город, гражданство и т.п., 2026-07-10):
        верифицируем по записи и ставим выверенное значение основным, realtime
        оставляем в скобках как аудит-след (citizen_rf ⚠, остальное молча чистим).
    Возвращает (corrected_answers: dict, needs_review: bool, notes: list).
    Значения НЕ перезаписываются молча: в corrected сохраняется и live-вариант.
    Защита от галлюцинаций: берём выверенное значение, только если оно реально
    звучит в расшифровке (_answer_grounded) и вопрос был достигнут в звонке."""
    if not combined_transcript.strip():
        return {}, False, []
    corrected, notes, needs_review = {}, [], False

    def _live_unrecognized(v):
        return (not isinstance(v, (int, float))) and str(v).startswith("не распознано")

    # «Достигнут ли вопрос» — раньше только по live_answers, и это теряло
    # живых кандидатов (тест 16.07: Ахмединмухтор/Rohit называли возраст,
    # запись его содержит, но звонок оборвался по «3 без ответа» на первом
    # шаге → ключа возраста в live нет → восстановление скипалось). Теперь
    # вопрос считается достигнутым и если он РЕАЛЬНО ЗВУЧАЛ в записи
    # (дорожка бота в combined_transcript). Защита от галлюцинаций прежняя:
    # ответ принимается только если он сам звучит в записи (grounding).
    norm_transcript = _norm_answer(combined_transcript)

    def _question_was_asked(st):
        crit = st.get("crit") or st.get("id")
        if crit in live_answers:
            return True
        bot = st.get("bot") or ""
        words = [w for w in _norm_answer(bot).split() if len(w) >= 4][:4]
        if len(words) < 2:
            return False
        hits = sum(1 for w in words if w[:5] in norm_transcript)
        return hits >= max(2, len(words) - 1)

    for st in scenario.get("steps", []):
        crit = st.get("crit") or st.get("id")
        expect = st.get("expect")
        live = live_answers.get(crit)

        if expect == "age":
            # Восстанавливаем возраст ТОЛЬКО если вопрос реально задавался
            # (ключ в live_answers ИЛИ вопрос звучал в записи — см.
            # _question_was_asked). Иначе LLM галлюцинирует число на мусоре
            # (случай Ахмата 09.07: фейковое «29» бросившему трубку).
            if not _question_was_asked(st):
                continue
            # 26.07: СНАЧАЛА детерминированная попытка — вытащить возраст из
            # дорожки кандидата тем же парсером, что и в звонке. Проверено на
            # данных 40/41/47: возраст физически звучит в записи лишь у 7 из
            # 79 потерянных, но малая LLM восстановила всего 1 — на мусорных
            # расшифровках («алло на уютно девятнадцать мф москва») она
            # ломается, а парсер числительных берёт число уверенно и без
            # риска галлюцинации (значение читается ИЗ текста).
            ans = ""
            try:
                from dialog import parse_age_from_text as _pa
                cand_track = combined_transcript.split("\n")[0]
                if cand_track.startswith("[Дорожка 1]"):
                    cand_track = cand_track[11:]
                _det = _pa(cand_track)
                if _det is not None:
                    ans = str(_det)
            except Exception:
                pass
            if not ans:
                ans = _llm_ask(
                    base_url,
                    "Расшифровка телефонного разговора (обе дорожки — бот и кандидат "
                    f"вперемешку):\n{combined_transcript}\n\n"
                    "Бот спрашивал возраст кандидата. Сколько ПОЛНЫХ ЛЕТ назвал "
                    "кандидат? Ответь ТОЛЬКО числом (например: 29). Если возраст "
                    "не называется или неясен — ответь: нет.", num_predict=8)
            m = re.search(r"\d{1,3}", ans)
            if not m:
                if _live_unrecognized(live):
                    needs_review = True
                    notes.append(f"Возраст в реальном времени не распознан, по записи тоже неясен — проверьте.")
                continue
            rec_age = int(m.group())
            if not (10 <= rec_age <= 99):
                continue
            # Защита от галлюцинации: принимаем восстановленный возраст,
            # только если он реально звучит в записи. Иначе не подменяем —
            # если live не распознан, помечаем на ручную проверку.
            if not _age_grounded(rec_age, combined_transcript):
                if _live_unrecognized(live):
                    needs_review = True
                    notes.append("Возраст: реалтайм не распознал, по записи надёжно не определить — проверьте.")
                continue
            if _live_unrecognized(live):
                corrected[crit] = f"{rec_age} (восстановлено по записи)"
                needs_review = True
                notes.append(f"Возраст: реалтайм не распознал, по записи — {rec_age}.")
            elif str(live).strip() != str(rec_age):
                corrected[crit] = f"{rec_age} (по записи; в реальном времени распознано: {live})"
                needs_review = True
                notes.append(f"Возраст: реалтайм «{live}», по записи «{rec_age}» — проверьте.")

        elif (expect in ("crime", "gender_male", "shifts")
              or (expect == "yesno" and (st.get("end_on_yes") or st.get("end_on_no")))):
            # Критичный да/нет: добираем ТОЛЬКО потерянные ответы — live
            # «не распознано», либо (16.07) вопрос звучал в записи, а в live
            # его вообще нет (обрыв по «3 без ответа» раньше фиксации).
            asked_but_missing = live is None and _question_was_asked(st)
            if not (_live_unrecognized(live) or asked_but_missing):
                continue
            ans = _llm_ask(
                base_url,
                "Расшифровка телефонного разговора (обе дорожки — бот и кандидат):\n"
                f"{combined_transcript}\n\n"
                f"Бот спросил: «{st.get('bot', '')}». Что ответил кандидат по сути — "
                "да или нет? Ответь ТОЛЬКО одним словом: да / нет / неясно.", num_predict=6)
            if "да" in ans and "нет" not in ans:
                corrected[crit] = "да (восстановлено по записи)"
                needs_review = True
                notes.append(f"«{crit}»: реалтайм не распознал, по записи — да.")
            elif "нет" in ans:
                corrected[crit] = "нет (восстановлено по записи)"
                needs_review = True
                notes.append(f"«{crit}»: реалтайм не распознал, по записи — нет.")

        else:
            # Общая ВЕРИФИКАЦИЯ по записи всех прочих ДОСТИГНУТЫХ вопросов
            # (2026-07-10, по просьбе: в отчёте — итоговая, выверенная версия
            # данных). Чистит мусор realtime на свободных полях — реальный
            # случай: гражданство «красивая» → в записи «российское»; город,
            # гражданство (citizen_rf — это страна, а не да/нет) и т.п.
            # Только достигнутые вопросы (иначе LLM выдумывает); принимаем
            # ответ, лишь если он реально звучит в записи (_answer_grounded).
            # Верифицированное — основное значение, realtime — в скобках как
            # аудит-след. ⚠ на проверку ставим только для критичного
            # citizen_rf, для прочих свободных полей — просто чистим.
            # С 16.07 «достигнутость» — и по звучанию вопроса в записи.
            if not _question_was_asked(st):
                continue
            ans = _llm_ask(
                base_url,
                "Расшифровка телефонного разговора (бот и кандидат):\n"
                f"{combined_transcript}\n\n"
                f"Бот спросил: «{st.get('bot', '')}». Что ответил кандидат ПО СУТИ? "
                "Ответь очень коротко — только сам ответ (город, страна, да/нет, "
                "число), без пояснений. Если кандидат не ответил или неясно — "
                "ответь: не распознано.", num_predict=14).strip()
            if not ans or ans.startswith("не распозна") or "неясно" in ans:
                continue
            if not _answer_grounded(ans, combined_transcript):
                continue
            live_s = "" if live is None else str(live).strip()
            if _norm_answer(ans) == _norm_answer(live_s):
                continue  # realtime уже верен — не трогаем
            corrected[crit] = f"{ans} (по записи; в реальном времени: {live_s or '—'})"
            if expect == "citizen_rf":  # гражданство — критично для стоп-фактора
                needs_review = True
                notes.append(f"«{crit}»: реалтайм «{live_s or '—'}», по записи «{ans}» — проверьте.")

    return corrected, needs_review, notes


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

    # 26.07: у простых стопов вида «...: нет» (кандидат сам сказал «нет» на
    # закрытый вопрос) эта LLM-сверка систематически давала ЛОЖНУЮ тревогу:
    # 15 из 16 стопов «Актуальность поиска работы: нет» в тестах 40/41/47
    # улетели в спорные, хотя всё верно. Причина: стоп срабатывает на первом
    # же вопросе, запись короткая, и модель на вопрос «подтверждает ли текст»
    # честно отвечает «проверить» (=«неразборчиво»). Детерминированная
    # проверка надёжнее: если в дорожке кандидата ЗВУЧИТ отрицание — стоп
    # подтверждён, LLM не нужна. Защита от операторских заглушек («абонент
    # занят» → услышали «нет») теперь обеспечивается детекторами робота/
    # автоответчика, которые срабатывают ДО этой сверки (шаги 0/0в).
    if _norm_answer(stop_reason).endswith("нет"):
        cand_track = combined_transcript.split("\n")[0]
        if cand_track.startswith("[Дорожка 1]"):
            cand_track = cand_track[11:]
        t = " " + _norm_answer(cand_track) + " "
        if re.search(r"\b(нет|не|неа|нету|не ищу|не интересно|не надо|не нужно)\b", t):
            return False, None  # отрицание в записи есть — стоп подтверждён

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


def _recognize_mp3(url: str) -> str:
    """Резерв для пере-проверки (28.07): если Novofon не отдал WAV-дорожки,
    распознаём общую mp3-запись разговора. Конвертируем в WAV 16кГц моно
    тем же ffmpeg, что уже используется для телефонного аудио (пакет
    imageio-ffmpeg), и отдаём большой Vosk-модели. Лучшее старание — при
    любой ошибке возвращаем пустую строку."""
    import tempfile, os as _os, subprocess
    import urllib.request as _ur
    from stt import recognize_wav_file
    mp3_path = tempfile.mktemp(suffix=".mp3")
    wav_path = tempfile.mktemp(suffix=".wav")
    try:
        _ur.urlretrieve(url, mp3_path)
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run([ffmpeg, "-y", "-i", mp3_path, "-ac", "1", "-ar", "16000", wav_path],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=120)
        with _STT_RECHECK_BUSY:
            return recognize_wav_file(wav_path) or ""
    except Exception as e:
        print(f"⚠️  Не смог распознать mp3-запись: {e}", flush=True)
        return ""
    finally:
        for p in (mp3_path, wav_path):
            try: _os.remove(p)
            except Exception: pass


def _recheck_transcript(base_url: str, token: str, contact_id: int,
                         api_secret: str, call_session_id: int,
                         verdict: str = "", stop_reason: str = "",
                         scenario: dict = None, live_answers: dict = None,
                         live_status: str = "") -> None:
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

    # Скачивание — лёгкое, распознавание — тяжёлое (та же Vosk-модель,
    # что и живой звонок). Держим только STT-часть под семафором, чтобы
    # несколько параллельных перепроверок не наваливались на CPU разом
    # и не мешали текущему живому разговору (см. комментарий у
    # _STT_RECHECK_BUSY).
    transcripts = []
    for url in (urls or []):
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

    # 28.07, резерв: WAV-дорожек может не быть (реальный случай — контакт 477
    # «НДЗ Пермь»: mp3-запись есть, дорожек нет). Раньше тут был тихий
    # return — контакт навсегда застревал в «⏳ проверяется» И не проходил
    # НИ ОДНОЙ проверки на робота, из-за чего автоответчик оставался
    # «живым» вердиктом «не ищет работу». Берём общую mp3-запись и
    # распознаём её (одна смешанная дорожка — хуже для разбора «кто где»,
    # но детекторы робота работают и по ней).
    if not any(t.strip() for t in transcripts):
        mp3_url = None
        try:
            mp3_url = call_api.get_recording_url(api_secret, call_session_id)
        except Exception:
            pass
        if mp3_url:
            print(f"🎧 contact_id={contact_id}: WAV-дорожек нет, распознаю общую "
                  f"mp3-запись.", flush=True)
            txt = _recognize_mp3(mp3_url)
            if txt.strip():
                transcripts = [txt]

    if not any(t.strip() for t in transcripts):
        # Совсем нечего проверять — но контакт ОБЯЗАН быть финализирован,
        # иначе висит в «⏳ проверяется» вечно (баг 28.07). И честно
        # помечаем, что статусу нельзя доверять: проверки не было.
        blind = verdict in ("passed", "stopped")
        try:
            _rpc(base_url, "POST", "/voicecall/dispatch/recheck-transcript", token=token,
                 json_body={"contact_id": contact_id,
                            "recheck_transcript": "(запись есть, но распознать не удалось)",
                            "no_recording": True,
                            "needs_review": blind,
                            "review_note": ("Пере-проверка по записи не удалась (нет дорожек "
                                            "и mp3 не распознан) — статус получен только по "
                                            "распознаванию в реальном времени, проверьте вручную."
                                            if blind else None)},
                 timeout=15)
            print(f"🕳 contact_id={contact_id}: запись не распозналась — финализирован"
                  + (" с ⚠." if blind else "."), flush=True)
        except Exception as e:
            print(f"⚠️  Финализация после неудачной пере-проверки не удалась: {e}", flush=True)
        return
    combined = "\n\n".join(
        f"[Дорожка {i+1}] {t.strip() or '(тишина/не распознано)'}"
        for i, t in enumerate(transcripts)
    )

    # 0) переклассификация в АВТООТВЕТЧИК/робота по записи. Случаи:
    #    (2026-07-08) оператор «абонент занят, перезвоните позднее» услышан
    #    как «нет» → ложный ОТКАЗ; (2026-07-10) Яндекс-нейросекретарь ведёт
    #    диалог и ДОХОДИТ до конца → ложный «ГОДЕН» (verdict=passed!).
    #    Поэтому дискриминатор теперь — сама ФРАЗА-заглушка (специфичная для
    #    робота/оператора), а НЕ вердикт: раньше страховались условием
    #    verdict not in (passed, stopped), но именно оно и пропускало
    #    AI-ассистентов, доигравших сценарий. Фразы is_voicemail_phrase
    #    высокоспецифичны (проверено: 0 ложных срабатываний на реальных
    #    ответах кандидатов; дорожка бота тоже не триггерит), так что
    #    переклассифицируем при любом вердикте.
    from dialog import (is_voicemail_phrase, is_ringback_phrase, is_spamguard_phrase,
                        count_evasive_markers, EVASIVE_ROBOT_MIN, is_human_refusing_bot)

    # СТОП-КРАН ВЫШЕ ВСЕХ ДЕТЕКТОВ (31.07, контакт 687 «Светлана»): если
    # в записи звучит «я с ботом разговаривать не буду» — это доказанно
    # ЖИВОЙ человек, который понял, что перед ним машина. LLM записала её
    # в автоответчики, и кандидат потерялся. Ни одна ветка ниже не имеет
    # права переклассифицировать такой контакт в робота; максимум — ⚠.
    if any(is_human_refusing_bot(t) for t in transcripts):
        _rpc(base_url, "POST", "/voicecall/dispatch/recheck-transcript", token=token,
             json_body={"contact_id": contact_id, "recheck_transcript": combined,
                        "needs_review": True,
                        "review_note": "Живой кандидат отказался разговаривать с роботом "
                                       "(«с ботом разговаривать не буду»). В автоответчики "
                                       "НЕ переводим — нужен звонок живого рекрутёра."},
             timeout=15)
        print(f"🙋 contact_id={contact_id}: живой отказался говорить с ботом — "
              f"защищён от переклассификации, помечен ⚠.", flush=True)
        return

    if any(is_voicemail_phrase(t) for t in transcripts):
        _rpc(base_url, "POST", "/voicecall/dispatch/recheck-transcript", token=token,
             json_body={"contact_id": contact_id, "recheck_transcript": combined,
                        "reclassify_voicemail": True,
                        "review_note": "Переклассифицировано по записи: автоответчик/"
                                       "робот-секретарь (в реальном времени распознано "
                                       "как ответ кандидата)."},
             timeout=15)
        print(f"🔁 contact_id={contact_id} переклассифицирован в АВТООТВЕТЧИК/робота по "
              f"записи (live-вердикт был: {verdict or 'нет'}).", flush=True)
        return

    # 0а) СПАМ-ЗАЩИТА оператора (30.07, контакт 525 «Виталий»): робот
    #     Тинькофф/МТС/Мегафона не объявляет себя автоответчиком, а
    #     переспрашивает звонящего — «да, да, слушаю вас, а уточните
    #     пожалуйста, С КЕМ ГОВОРЮ... давайте позже». Фразы (0) его не
    #     знали, LLM (0в) на реальной дорожке дала «человек» 3/3, а
    #     реалтайм смял реплику в «потом» → ложный ОТКАЗ «не ищет работу».
    #     Сама по себе фраза неоднозначна (живой, которому позвонили с
    #     незнакомого номера, тоже спрашивает «а с кем я говорю?»), поэтому
    #     решаем ПО СОВОКУПНОСТИ — как в стоп-кране 0в, только зеркально:
    #       спам-фраза + НИ ОДНОГО факта о себе → робот, переклассифицируем;
    #       спам-фраза + факты названы      → живой, только ⚠.
    #     Только на РАЗДЕЛЬНЫХ дорожках: в смешанной mp3 (резерв выше)
    #     слышен и наш бот, а «представьтесь» рекрутёр может вписать в
    #     сценарий — на склейке это дало бы ложное срабатывание.
    #     30.07, второй сигнал той же природы (контакт 564 «Мария»): AI-
    #     секретарь, который не переспрашивает, а ВЕЖЛИВО ТЯНЕТ ВРЕМЯ —
    #     «расскажите побольше», «я бы ещё подумал», «паузу возьму чтоб
    #     подумать», «подъеду к вам лично». За 1м43с ни одного ответа по
    #     существу, но «да» на первый вопрос дало вердикт ГОДЕН.
    #     Считаем РАЗНЫЕ уклончивые обороты: три и больше — это уже не
    #     «человек задумался», а поведение робота. Решение — так же по
    #     совокупности с отсутствием данных о себе.
    behaviour_note = None
    if len(transcripts) >= 2:
        cand0 = (transcripts[0] or "").strip()
        hit_spam = is_spamguard_phrase(cand0)
        evasive_n = count_evasive_markers(cand0)
        hit_evasive = evasive_n >= EVASIVE_ROBOT_MIN
        if hit_spam or hit_evasive:
            why = ("переспрашивал «с кем говорю / цель звонка»" if hit_spam
                   else f"уклонялся от ответов ({evasive_n} разных оборотов: "
                        f"«расскажите побольше», «подумаю», «откуда у вас мой номер»)")
            if not _candidate_gave_personal_data(cand0):
                _rpc(base_url, "POST", "/voicecall/dispatch/recheck-transcript", token=token,
                     json_body={"contact_id": contact_id, "recheck_transcript": combined,
                                "reclassify_voicemail": True,
                                "review_note": f"Переклассифицировано по записи: собеседник "
                                               f"{why} и не сообщил о себе ничего конкретного — "
                                               f"робот-секретарь, а не живой кандидат."},
                     timeout=15)
                print(f"🛡 contact_id={contact_id} переклассифицирован в АВТООТВЕТЧИК/робота "
                      f"(поведение по записи: {'спам-защита' if hit_spam else f'{evasive_n} уклончивых'}"
                      f"; live-вердикт был: {verdict or 'нет'}).", flush=True)
                return
            behaviour_note = (f"Собеседник {why} — так ведёт себя робот-секретарь, но "
                              f"конкретные данные о себе он назвал. Статус оставлен, "
                              f"проверьте запись.")
            print(f"🕵 contact_id={contact_id}: поведение робота, но данные о себе названы — "
                  f"помечен ⚠, статус не меняю.", flush=True)

    # 0б) голосовой РИНГ-БЭК в записи («идёт дозвон, оставайтесь на линии»)
    #     без осмысленных ответов — не соединилось, исход «не взял трубку»
    #     (перезвон уместен), а не «не распознали» (тест Яндекс-3, Дмитрий-153).
    if verdict not in ("passed", "stopped") and any(is_ringback_phrase(t) for t in transcripts):
        _rpc(base_url, "POST", "/voicecall/dispatch/recheck-transcript", token=token,
             json_body={"contact_id": contact_id, "recheck_transcript": combined,
                        "reclassify_status": "no_answer",
                        "review_note": "По записи — голосовой ринг-бэк (дозвон), "
                                       "абонент не ответил."},
             timeout=15)
        print(f"📞 contact_id={contact_id} переклассифицирован в НЕ ВЗЯЛ ТРУБКУ "
              f"(ринг-бэк по записи).", flush=True)
        return

    # 0в) LLM-классификация робота-секретаря, которого НЕ поймали фразы
    #     (STT искажает «взять трубку»→«взять труп то»; роботы говорят по-
    #     новому). С 16.07 — для ВСЕХ исходов с записью, не только
    #     passed/stopped: Надир-кейс — AI-секретарь упал в low_recognition
    #     и не переклассифицировался, засоряя «спорных». По умолчанию
    #     LLM отвечает «человек» — живых не теряем.
    #     27.07 — ДВА ФИКСА после ложного срабатывания на живом кандидате
    #     (контакт 467: анкета собрана полностью — «да, двадцать девять,
    #     российская, казань, да подходит» — и всё равно ушёл в автоответчик):
    #       (1) в классификатор подаём ТОЛЬКО дорожку кандидата. Раньше шла
    #           склейка обеих, т.е. вместе с репликами НАШЕГО бота — модель
    #           честно видела там робота (нашего же) и отвечала «робот».
    #           Замер на этой записи: обе дорожки → робот 3/3, только
    #           кандидат → человек 3/3.
    #       (2) детерминированный стоп-кран: если кандидат назвал КОНКРЕТНЫЕ
    #           данные о себе (возраст числом), переклассификации по одному
    #           мнению маленькой LLM не делаем — робот-секретарь возраст не
    #           называет (тот же признак, что в _passed_but_no_age). Максимум
    #           помечаем ⚠, чтобы решил рекрутёр. Живого не теряем.
    #     30.07 — ТРЕТИЙ фикс: один опрос модели заменён голосованием, а
    #     сбой опроса больше не выдаётся за «человек». Контакт 564: ручной
    #     прогон того же промпта на той же дорожке даёт «робот» 3/3, а в
    #     бою вернулось «человек» — и автоответчик остался «годным».
    cand_track_for_robot = (transcripts[0] if transcripts else "").strip()
    robot_votes = (ROBOT_CHECK_VOTES
                   if _robot_check_is_suspicious(live_answers, cand_track_for_robot)
                   else 1)
    is_robot, robot_checked = _llm_robot_verdict(
        base_url, cand_track_for_robot or combined, votes=robot_votes)
    if is_robot:
        if _candidate_stated_age(cand_track_for_robot):
            print(f"🛡 contact_id={contact_id}: LLM сочла роботом, но кандидат назвал "
                  f"возраст — НЕ переклассифицирую, помечаю ⚠.", flush=True)
            _rpc(base_url, "POST", "/voicecall/dispatch/recheck-transcript", token=token,
                 json_body={"contact_id": contact_id, "recheck_transcript": combined,
                            "needs_review": True,
                            "review_note": "LLM заподозрила робота-секретаря, но кандидат "
                                           "назвал конкретные данные о себе (возраст) — "
                                           "статус оставлен, проверьте запись."},
                 timeout=15)
            return
        _rpc(base_url, "POST", "/voicecall/dispatch/recheck-transcript", token=token,
             json_body={"contact_id": contact_id, "recheck_transcript": combined,
                        "reclassify_voicemail": True,
                        "review_note": "Переклассифицировано по записи (LLM): собеседник — "
                                       "робот-секретарь/голосовой ассистент, не живой кандидат."},
             timeout=15)
        print(f"🤖 contact_id={contact_id} переклассифицирован в АВТООТВЕТЧИК/робота "
              f"(LLM по записи; live-вердикт был: {verdict}).", flush=True)
        return

    # 0г) «ПОЗДНИЙ ОТВЕТ» (16.07, Анастасия-кейс): live-статус «не взял
    #     трубку», но в дорожке кандидата слышна человеческая речь (не
    #     робот и не ринг-бэк — они отсеяны выше) — человек, вероятно,
    #     ответил в последний момент, после нашего таймаута. Статус не
    #     меняем (соединения не было), но помечаем ⚠ — рекрутёру стоит
    #     перезвонить лично.
    if live_status == "no_answer":
        cand_track = (transcripts[0] if transcripts else "").strip()
        if cand_track and cand_track != "(тишина/не распознано)" and len(cand_track) >= 4:
            _rpc(base_url, "POST", "/voicecall/dispatch/recheck-transcript", token=token,
                 json_body={"contact_id": contact_id, "recheck_transcript": combined,
                            "needs_review": True,
                            "review_note": "В записи слышна речь — возможно, кандидат "
                                           "взял трубку в последний момент. Стоит перезвонить."},
                 timeout=15)
            print(f"🕓 contact_id={contact_id}: недозвон, но в записи речь — помечен ⚠ "
                  f"(поздний ответ?).", flush=True)
            return

    # 1) сверка причины ОТКАЗА с записью (ложные отказы)
    needs_review, review_note = _recheck_verdict(base_url, verdict, stop_reason, combined)
    notes = [review_note] if review_note else []
    if behaviour_note:  # см. 0а — поведение робота, но данные о себе названы
        needs_review = True
        notes.append(behaviour_note)
    # Сбой проверки на робота — это НЕ «человек». Раньше падение LLM молча
    # давало «годен» без единого следа (см. 0в): в логе кампаний 20 ответов
    # «Bad Gateway» и 14 таймаутов. Теперь рекрутёр видит, что проверки не было.
    if not robot_checked and verdict in ("passed", "stopped"):
        needs_review = True
        notes.append("Проверка «робот или живой» не выполнена — модель не ответила. "
                     "Статус получен только по распознаванию в реальном времени, "
                     "проверьте запись.")
        print(f"❓ contact_id={contact_id}: проверка на робота не выполнена (LLM молчит) — ⚠.",
              flush=True)

    # 2) сверка КРИТИЧНЫХ ответов (возраст + потерянные стоп-факторы) с
    #    записью — восстанавливаем то, что маленькая модель потеряла в
    #    реальном времени на тихой линии (см. _recheck_critical_answers)
    corrected = {}
    if scenario is not None and live_answers is not None:
        try:
            corrected, crit_review, crit_notes = _recheck_critical_answers(
                base_url, scenario, live_answers, combined)
            needs_review = needs_review or crit_review
            notes.extend(crit_notes)
            # 31.07, контакт 688 «Илья»: запись исправила ответ («реалтайм не
            # распознал, по записи — да»), комментарий записался, а флаг ⚠ НЕ
            # поднялся — человек, сказавший «да», уехал в отказ молча. Если
            # пере-проверка меняет ответ на критичный вопрос, расхождение
            # обязано быть видно рекрутёру. Без исключений.
            if corrected:
                needs_review = True
        except Exception as e:
            print(f"⚠️  Сверка критичных ответов с записью пропущена: {e}", flush=True)

    # 2б) АНТИ-МИМИКРИЯ (17.07): «годен», сценарий спрашивал возраст, но
    #     кандидат его так и не назвал числом → почти наверняка робот-
    #     секретарь, которого малая realtime-модель провела как годного
    #     (Янис/Наталья/Джамила в тесте 17.07: 3 «чистых годен» оказались
    #     роботами). НЕ переклассифицируем (вдруг живой с плохой записью) —
    #     помечаем ⚠, чтобы рекрутёр глянул. Детерминированно, без LLM.
    if _passed_but_no_age(scenario, verdict, transcripts):
        needs_review = True
        notes.append("Возможно робот-секретарь: вердикт «годен», но за разговор "
                     "кандидат так и не назвал свой возраст — проверьте запись.")
        print(f"🕵 contact_id={contact_id}: «годен» без названного возраста — "
              f"помечен ⚠ (подозрение на робота-секретаря).", flush=True)

    # 3) LLM-САММАРИ записи для помеченных ⚠ (16.07): рекрутёр решает по
    #    двум предложениям вместо прослушивания записи. Только при
    #    needs_review — не жжём LLM на чистых контактах.
    if needs_review:
        summary = _llm_summary_for_review(base_url, combined)
        if summary:
            notes.append(f"📋 По записи: {summary}")

    _rpc(base_url, "POST", "/voicecall/dispatch/recheck-transcript", token=token,
         json_body={"contact_id": contact_id, "recheck_transcript": combined,
                    "needs_review": needs_review,
                    "review_note": " ".join(notes) if notes else None,
                    "corrected_answers": corrected}, timeout=15)
    print(f"🔍 Перепроверенный транскрипт contact_id={contact_id} прикреплён."
          + (f" ✏️ уточнено по записи: {list(corrected.keys())}" if corrected else "")
          + (" ⚠️ ПОМЕЧЕН НА ПРОВЕРКУ." if needs_review else ""), flush=True)


def _reconcile_campaign_cost(base_url: str, token: str, campaign_id: int,
                              session_ids: "set[int]", started_at) -> None:
    """Свести стоимость телефонии по кампании из финотчёта Novofon и
    отправить на портал (23.07). Фоновый поток: у биллинга Novofon есть
    задержка, поэтому ждём и делаем несколько попыток, пока сумма растёт.
    Лучшее старание — при сбое просто не обновляем стоимость."""
    from datetime import datetime, timedelta
    try:
        env = load_env()
        api_secret = require(env, "NOVOFON_API_SECRET")
    except SystemExit:
        return
    df = (started_at - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    prev_total, stable = -1.0, 0
    for attempt in range(6):
        time.sleep(60 if attempt else 20)
        dt = (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            charges = call_api.get_charges_by_session(api_secret, session_ids, df, dt)
        except Exception as e:
            print(f"⚠️  Стоимость кампании {campaign_id}: финотчёт не получен: {e}", flush=True)
            continue
        total = round(sum(charges.values()), 2)
        # Стабилизировалось (2 замера подряд одинаковы) ИЛИ все звонки учтены —
        # можно фиксировать. Иначе биллинг ещё «докручивает» — ждём дальше.
        if total == prev_total:
            stable += 1
        else:
            stable, prev_total = 0, total
        if stable >= 1 or len(charges) >= len(session_ids):
            try:
                _rpc(base_url, "POST", "/voicecall/dispatch/campaign-cost", token=token,
                     json_body={"campaign_id": campaign_id, "cost_rub": total}, timeout=15)
                print(f"💰 Стоимость кампании {campaign_id}: {total:.2f} ₽ "
                      f"({len(charges)}/{len(session_ids)} звонков учтено).", flush=True)
            except Exception as e:
                print(f"⚠️  Не смог отправить стоимость кампании: {e}", flush=True)
            return
    # Не стабилизировалось за все попытки — шлём что есть (лучше, чем ничего).
    if prev_total >= 0:
        try:
            _rpc(base_url, "POST", "/voicecall/dispatch/campaign-cost", token=token,
                 json_body={"campaign_id": campaign_id, "cost_rub": prev_total}, timeout=15)
            print(f"💰 Стоимость кампании {campaign_id}: ~{prev_total:.2f} ₽ (не финализ.).", flush=True)
        except Exception:
            pass


def _fetch_and_attach_recording(base_url: str, token: str, contact_id: int,
                                 call_session_id, verdict: str = "", stop_reason: str = "",
                                 scenario: dict = None, live_answers: dict = None,
                                 live_status: str = "") -> None:
    """Фоновый поток (не блокирует основной цикл обзвона): Novofon
    обрабатывает запись разговора не мгновенно после звонка, поэтому
    пробуем несколько раз с паузой, затем ВТОРУЮ серию через пару минут.
    Если записи так и нет — с 17.07 НЕ сдаёмся молча: контакт с двухфазным
    статусом должен быть финализирован, поэтому шлём рекчек-финализацию
    с пометкой «без записи» (для passed/stopped — с ⚠, финальный статус
    без пере-проверки нельзя считать надёжным). Как только запись готова —
    пере-проверка транскрипта (см. _recheck_transcript)."""
    if not call_session_id:
        return
    try:
        env = load_env()
        api_secret = require(env, "NOVOFON_API_SECRET")
    except SystemExit:
        return

    def _try_series(n_attempts, delay):
        """Возвращает (url|None, были_ли_сетевые_ошибки). Сетевая ошибка ≠
        «записи нет»: Novofon отвечает None, когда записи реально нет, а
        исключение — это недоступность сети/портала-прокси."""
        net_err = False
        for _ in range(n_attempts):
            time.sleep(delay)
            try:
                u = call_api.get_recording_url(api_secret, call_session_id)
            except Exception:
                u, net_err = None, True
            if u:
                return u, net_err
        return None, net_err

    url, net_err = _try_series(RECORDING_FETCH_ATTEMPTS, RECORDING_FETCH_DELAY_SEC)
    if not url:
        # вторая серия — Novofon иногда отдаёт запись через минуты
        print(f"🎙 Запись contact_id={contact_id} не готова, вторая серия через "
              f"{RECORDING_RETRY_ROUND2_DELAY_SEC}с...", flush=True)
        time.sleep(RECORDING_RETRY_ROUND2_DELAY_SEC)
        url, net_err = _try_series(RECORDING_FETCH_ATTEMPTS, RECORDING_FETCH_DELAY_SEC)

    # Тест 21.07: обе серии попали в сетевой обрыв ПК↔VPS, запись у Novofon
    # СУЩЕСТВОВАЛА (владелец скачал её руками), а контакт финализировался
    # «вслепую». Сетевые ошибки — повод ЖДАТЬ и повторять, а не сдаваться:
    # до 3 дополнительных раундов по 5 минут, пока ошибки именно сетевые.
    extra_round = 0
    while not url and net_err and extra_round < 3:
        extra_round += 1
        print(f"🌐 Запись contact_id={contact_id}: сеть недоступна, доп. раунд "
              f"{extra_round}/3 через 300с...", flush=True)
        time.sleep(300)
        url, net_err = _try_series(RECORDING_FETCH_ATTEMPTS, RECORDING_FETCH_DELAY_SEC)

    if url:
        try:
            _rpc(base_url, "POST", "/voicecall/dispatch/recording", token=token,
                 json_body={"contact_id": contact_id, "recording_url": url}, timeout=10)
            print(f"🎙 Запись звонка contact_id={contact_id} прикреплена.", flush=True)
        except Exception as e:
            print(f"⚠️  Не смог отправить ссылку на запись: {e}", flush=True)
        try:
            _recheck_transcript(base_url, token, contact_id, api_secret, call_session_id,
                                 verdict=verdict, stop_reason=stop_reason,
                                 scenario=scenario, live_answers=live_answers,
                                 live_status=live_status)
        except Exception as e:
            print(f"⚠️  Перепроверка транскрипта не удалась: {e}", flush=True)
        return

    # Записи нет совсем — финализируем «вслепую» с честной пометкой.
    # no_recording=True: бэкенд знает, что это плейсхолдер, и НЕ затирает
    # настоящий recheck-транскрипт, если его успел записать параллельный
    # поток другой попытки (тест 21.07: поток error-попытки затёр реальную
    # пере-проверку). Никаких LLM-этапов здесь нет и не должно быть —
    # автосверка/саммари на заглушке галлюцинируют («возраст 25, москва»
    # у кандидата, который вообще не разговаривал).
    try:
        blind = verdict in ("passed", "stopped")
        why = "сеть/портал были недоступны" if net_err else "Novofon не отдал запись"
        _rpc(base_url, "POST", "/voicecall/dispatch/recheck-transcript", token=token,
             json_body={"contact_id": contact_id,
                        "recheck_transcript": "(запись не получена от Novofon)",
                        "no_recording": True,
                        "needs_review": blind,
                        "review_note": (f"Финализирован БЕЗ пере-проверки записи "
                                        f"({why}) — статусу нельзя полностью "
                                        f"доверять, проверьте вручную." if blind else None)},
             timeout=15)
        print(f"🕳 contact_id={contact_id}: записи нет ({why}) — финализирован"
              + (" с ⚠ (вслепую)." if blind else "."), flush=True)
    except Exception as e:
        print(f"⚠️  Финализация без записи не удалась: {e}", flush=True)


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
    # Стоимость телефонии (23.07): собираем call_session_id всех звонков
    # кампании и время старта, чтобы в конце свести списания Novofon.
    from datetime import datetime as _dt
    _camp_started = _dt.now()
    _session_ids: "set[int]" = set()

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
                # Свести стоимость телефонии в фоне (у биллинга Novofon —
                # задержка, поэтому с паузой и ретраями; не блокируем опрос).
                if _session_ids:
                    threading.Thread(
                        target=_reconcile_campaign_cost,
                        args=(base_url, token, campaign_id, set(_session_ids),
                              _camp_started),
                        daemon=True).start()
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
        if result.get("call_session_id"):
            _session_ids.add(result["call_session_id"])
        if posted and result.get("call_session_id"):
            # В фоне — следующий контакт в очереди не должен ждать, пока
            # Novofon обработает запись разговора (может занять десятки секунд).
            threading.Thread(
                target=_fetch_and_attach_recording,
                args=(base_url, token, contact_id, result["call_session_id"]),
                kwargs={"verdict": result.get("verdict") or "",
                        "stop_reason": result.get("stop_reason") or "",
                        "scenario": scenario,
                        "live_answers": dict(result.get("answers") or {}),
                        "live_status": result.get("status") or ""},
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
