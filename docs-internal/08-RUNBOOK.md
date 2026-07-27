# 08 · Runbook — операционная шпаргалка

> Обновлено: 2026-07-13. Все сбои ниже — реально случавшиеся; при новом типовом
> сбое добавь секцию по тому же шаблону (симптом → диагноз → лечение).

## 1. Быстрый health-check всей системы (1 минута)

```bash
# Портал жив?
curl https://portalsth.ru/tasks/api/health           # → {"ok":true}
# AI-цепочка (Ollama на ПК → туннель → VPS)?
curl https://portalsth.ru/tasks/api/ai/health        # → {"online":true,"models":3}
# Агент обзвона жив? (heartbeat не старше ~1 мин)
curl https://portalsth.ru/tasks/api/voicecall/agent-status
```
На портале то же самое видно глазами: индикатор «🟢 агент онлайн» на странице обзвона.

## 2. Карта процессов и как их перезапускать

### На VPS (ssh -i ~/.ssh/ai-tunnel.key root@195.208.119.67)

| Что | Проверить | Перезапустить |
|---|---|---|
| Бэкенд | `systemctl status tasks-api` | `systemctl restart tasks-api` |
| nginx | `systemctl status nginx` | `systemctl reload nginx` |
| Туннель приходит? | `ss -lnt \| grep -E '21434\|25001'` | со стороны ПК (ниже) |

⚠️ SSH-сессии на VPS с удержанием/фоном обрываются — только короткие one-liner'ы.

### На ПК (PowerShell)

| Что | Задача планировщика | Перезапуск |
|---|---|---|
| Агент обзвона | `STH-Dispatch-Agent` | `Stop-ScheduledTask -TaskName STH-Dispatch-Agent; Start-ScheduledTask -TaskName STH-Dispatch-Agent` |
| Ollama | `STH-Ollama-Serve-User` | `Start-ScheduledTask ...` (или вотчдог сам поднимет) |
| SSH-туннель | `STH-AI-Tunnel-User` | аналогично |
| Вотчдог (чинит всё AI) | `STH-Portal-Watchdog` | `Start-ScheduledTask -TaskName STH-Portal-Watchdog` — прогоняет полный цикл лечения |

Все задачи: user-context, триггер «при входе», через VBS-шим `C:\ProgramData\sth\run-hidden.vbs`
(не менять на прямой powershell — вернутся вспышки окон).

**После правки `voicecall/*.py` агент НЕ подхватывает код сам** — обязателен перезапуск
задачи `STH-Dispatch-Agent`. После правки `tasks/api/main.py` — push в main (CI сам
задеплоит и рестартанёт tasks-api, ~3-4 мин).

## 3. Где логи

| Лог | Путь |
|---|---|
| Агент обзвона (всё: звонки, распознавание) | `C:\ProgramData\sth\dispatch-agent.log` |
| AI-вотчдог + туннель | `C:\ProgramData\sth\ai-watchdog.log`, `ai-tunnel.log`, `ai-tunnel.ssh-stderr.log` |
| Бэкенд VPS | `journalctl -u tasks-api --since '1 hour ago'` |
| Дампы «не распознано» (WAV окон слушания) | `voicecall/diag/unrec_*.wav` (на ПК) |
| Недоставленные результаты звонков | `voicecall/dispatch_agent_failed_results.jsonl` |

⚠️ Кириллица в логах ломает Windows-консоль (cp1251) — читать через
`python -c "import io; print(io.open(r'путь', encoding='utf-8', errors='replace').read()[-3000:])"`
или выгружать в файл.

## 4. Типовые сбои и лечение (реальные случаи)

### 4.1 После ребута ПК не работает AI (`/ai/health: unreachable`)
- **Причина (2026-07-13):** задачи автозапуска были выключены; SYSTEM-туннель не читает ключ.
- **Лечение:** `Start-ScheduledTask STH-Portal-Watchdog` — поднимет Ollama+Silero+туннель.
  Если не помогло: убить все `ssh.exe`, запустить `STH-AI-Tunnel-User`, проверить
  `Get-Process ollama`. Туннель обязан быть **в контексте пользователя** (SYSTEM не
  читает `ai-tunnel.key` — это известный «зомби», см. 10-ТЕХДОЛГ).

### 4.2 Звонок идёт, кандидат говорит — а бот «не слышит» (громкость н/д или тишина)
- **Диагноз-развилка (выстрадано 2026-07-10):** смотри в логе агента счётчик
  `входящих RTP-пакетов получено: N`.
  - Пакеты идут, а звука нет → **баг буфера/guard в `phone_call.py`** (НЕ NAT!).
    Был случай: memory-guard дропал все пакеты при разрыве базы RTP-timestamp — лечится
    пере-базированием (уже исправлено, `_patch_rtp_memory_guard`).
  - Пакетов нет/единицы → сеть/Novofon.
- Дампы окон слушания — `voicecall/diag/`; прогони их big-моделью
  (`stt.recognize_wav_file`) чтобы понять: мусор / эхо бота / чистая речь.

### 4.3 «Novofon не перезвонил на нашу линию за 30 сек» / SIP 500
- Транзиентный сбой Novofon. Контакт **сам вернётся в очередь** (авто-повтор,
  attempts<2). Если массово — проверить баланс/статус линии в ЛК Novofon.

### 4.4 Кампания «зависла» (dispatch_state=running, звонков нет)
- Проверить heartbeat агента (health-check §1). Агент умер → перезапустить задачу.
- Проверить лог агента на MemoryError/краш — обёртка сама перезапускает python
  через 10с (`scripts/run-dispatch-agent.ps1`).

### 4.5 Роботы-секретари (Яндекс) попадают в «годен»
- Двухслойная защита: фразы (`dialog.is_voicemail_phrase`) в реальном времени +
  LLM-классификация записи после звонка (`_llm_is_robot_secretary`) → переклассификация
  в «автоответчик». Новые формулировки роботов → добавлять фразы в `_VOICEMAIL_PATTERN`
  (тесты `test_voicecall_dialog.py`!) — но НЕ фразы ринг-бэка («оставайтесь на линии») —
  это дозвон, не автоответчик (`_RINGBACK_PATTERN`).

### 4.6 Портал недоступен целиком
- `systemctl restart tasks-api nginx` на VPS. Если VPS недоступен — хостер.

## 5. Что НЕЛЬЗЯ делать

1. **Не коммитить** `scripts/install-ai-watchdog.ps1`, `scripts/start-ai-tunnel.ps1`
   (локальные чужие правки) и любые секреты.
2. Тесты — **только** в `tasks/api/tests/`.
3. Не менять действия задач планировщика на прямой powershell (вспышки окон).
4. Не запускать тяжёлые модели/агент на VPS (960 МБ RAM).
5. Правки сценариев обзвона — только руками владельца через конструктор.

## Инцидент 22.07.2026: VPS завис, после ребута не поднялся nginx

Симптомы: сайт/SSH таймаутят, пинг проходит → VPS завис (960 МБ RAM, вероятен
OOM). После ребута из панели хостинга портал НЕ вернулся: `nginx` = failed.

Причина: `/etc/nginx/conf.d/gzip-api.conf` (добавлен скриптом 20.07) дублировал
директиву `gzip` из nginx.conf. Работающему nginx это не мешало (жил со старым
конфигом), но старт после ребута падал: `nginx -t` → «"gzip" directive is
duplicate». Файл удалён — сжатие делает сам бэкенд (`_vc_maybe_gzip`).

Лечение по шагам: панель хостинга → Reboot → `ssh root@195.208.119.67` →
`systemctl is-active nginx tasks-api` → если failed: `nginx -t` (покажет
битый конфиг) → починить/удалить → `systemctl start nginx`. Туннель с ПК:
перезапустить задачу `STH-AI-Tunnel-User`. Агент переподключается сам.

Урок: ЛЮБОЕ изменение конфигов nginx на VPS — только через `nginx -t` ДО
reload, и обязательная проверка `systemctl is-enabled` + тестовый старт.
Открытый техдолг по итогам: swap-файл на VPS + мониторинг OOM (P0).

## Укрепление VPS после инцидента 22.07.2026

Сделано на VPS (сохраняется после перезагрузки):

| Мера | Где | Зачем |
|---|---|---|
| **Swap 2 ГБ** | `/swapfile` + запись в `/etc/fstab`, `vm.swappiness=20` | 960 МБ RAM без swap = зависание при любом пике |
| **Авто-рестарт сервисов** | `/etc/systemd/system/{nginx,tasks-api}.service.d/restart.conf` | у nginx было `Restart=no` — он и остался лежать после ребута |
| **Защита бэкенда от OOM** | `tasks-api.service.d/oom.conf` → `OOMScoreAdjust=-500` | при нехватке памяти ядро убьёт что угодно, но не портал |
| **Вотчдог зависания** | `portal-watchdog.timer` (раз в 3 мин) + `/usr/local/bin/portal-health-watchdog.sh` | `Restart=always` ловит смерть процесса, но НЕ ступор: 2 провала health подряд → рестарт |
| **Лимит журнала** | `/etc/systemd/journald.conf.d/size-limit.conf` (50 МБ) | журнал разросся до 193 МБ |
| **Ротация логов по размеру** | `/etc/logrotate.d/00-size-caps` (50 МБ, 3 копии) | auth.log вырос до 1.9 ГБ и забил диск |
| **Отключены ненужные демоны** | ModemManager, udisks2, multipathd (`disable` + `mask`) | ~50 МБ RAM на безголовом VPS без модемов и multipath |
| **ClientAlive для sshd** | `/etc/ssh/sshd_config.d/99-tunnel-keepalive.conf` | мёртвые сессии туннеля держали порты 21434/25001 |

### Корневая причина заполнения диска (важно!)

`auth.log` рос на **~700 МБ/сутки** из-за войны дублей SSH-туннеля: копии
`start-ai-tunnel.ps1` стартовали из нескольких мест (задача при входе,
STH-Portal-Watchdog, ручной перезапуск), дрались за порты, а `precleanup`
внутри скрипта убивал на VPS сессию «победителя» — бесконечный цикл.
Лечение: **мьютекс single-instance** в `scripts/start-ai-tunnel.ps1`
(файл НЕ коммитится — правило №1 в CLAUDE.md, живёт только на ПК).
После фикса: 494 КБ/мин → 2 КБ/мин.

Проверка здоровья VPS одной командой:
```
ssh root@195.208.119.67 "free -m; df -h /; systemctl is-active nginx tasks-api portal-watchdog.timer"
```

## «Ошибка звонка» у всех контактов подряд — проверь SIP-линию

Симптом: контакты получают `error`, в логе агента «❌ Novofon не перезвонил
на нашу линию за 30 сек».

Диагностика (с 27.07 агент делает это сам и пишет причину в лог/карточку):
причина берётся из отчёта Novofon — `finish_reason`. Значение **`sip_offline`**
означает, что наша SIP-линия 0125878 числилась у Novofon офлайн, поэтому он
не перезвонил нам для сведения разговора. Кандидату при этом НЕ звонили.

Лечение: агент сам перерегистрируется и делает до ТРЁХ кругов, обнаруживая
отказ за ~3 секунды (Novofon фиксирует sip_offline мгновенно: start_time ==
finish_time, 0с — замер 27.07).

### Корневая причина найдена 27.07 (замер)

`pyVoIP` рапортует `REGISTERED` через **~120 мс** (пришёл 200 OK от SIP-сервера),
а коммутатор Novofon переключает `physical_state` на «Зарегистрирован» только
через **~2 секунды**. Всё это окно заявка `start_employee_call` отлетает с
`sip_offline`. Раньше мы верили локальному статусу и просили перезвонить сразу.

Лечение (в коде): перед заявкой на звонок ждём подтверждения ИМЕННО ОТ
NOVOFON — `call_api.get_sip_line_state()` → `physical_state`.

Проверить состояние линии глазами Novofon в любой момент:
```
python -c "import sys;sys.path.insert(0,'voicecall');import call_api;from _sip_config import load_env,require;e=load_env();print(call_api.get_sip_line_state(require(e,'NOVOFON_API_SECRET'),'0125878'))"
``` Если не помогло —
проверить регистрацию вручную:
```
python -c "import sys;sys.path.insert(0,'voicecall');from _sip_config import load_env,require;import phone_call as pc;from pyVoIP.VoIP import VoIPPhone;e=load_env();p=VoIPPhone(server=require(e,'SIP_SERVER'),port=int(e.get('SIP_PORT','5060')),username=require(e,'SIP_USER'),password=require(e,'SIP_PASS'),myIP=pc.get_local_ip(),callCallback=lambda c:None);p.start();print(p.get_status())"
```
Ожидаем `PhoneStatus.REGISTERED`. Если нет — смотреть сеть на ПК и статус
линии в личном кабинете Novofon.
