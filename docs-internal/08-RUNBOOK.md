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
