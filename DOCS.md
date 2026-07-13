# ⛔ УСТАРЕЛО — актуальная документация в `docs-internal/`

> **Этот файл не поддерживается с 2026-07-13** и описывает старую архитектуру
> (ngrok, ручной запуск AI, старый IP вместо portalsth.ru). Оставлен как история.
>
> ✅ Актуально: [`docs-internal/README.md`](docs-internal/README.md)
> — обзор, архитектура, runbook, реестр доступов.
> Пароли из этого файла удалены — значения в `docs-internal/СЕКРЕТЫ.local.md` (вне git).

---

# Аналитический портал STH — Техническая документация (архив)

> **Production:** https://portalsth.ru/
> **Репозиторий:** https://github.com/max511028-bit/OKK
> **Пароли:** см. `docs-internal/СЕКРЕТЫ.local.md` (локально, вне git)

---

## Содержание

1. [Быстрый старт](#быстрый-старт)
2. [Архитектура](#архитектура)
3. [Структура репозитория](#структура-репозитория)
4. [Дашборды](#дашборды)
5. [Бэкенд (FastAPI)](#бэкенд-fastapi)
6. [Авторизация](#авторизация)
7. [AI-инфраструктура](#ai-инфраструктура)
8. [Деплой](#деплой)
9. [VPS — что где](#vps--что-где)
10. [nginx-конфиг](#nginx-конфиг)
11. [Скрипты и инжекторы](#скрипты-и-инжекторы)
12. [Источники данных](#источники-данных)
13. [Запуск AI после ребута компа](#запуск-ai-после-ребута-компа)
14. [Troubleshooting](#troubleshooting)
15. [История изменений](#история-изменений)
16. [Известные проблемы и roadmap](#известные-проблемы-и-roadmap)

---

## Быстрый старт

### Чтобы AI работал

1. На локальном компе должны крутиться **Ollama** + **ngrok**.
2. После ребута компа: двойной клик по `start-ai.bat` в корне репозитория.
3. Окно `cmd.exe` не закрывать — пока оно открыто, AI работает.
4. Проверка: `admin.html` → пароль админки (см. СЕКРЕТЫ.local.md) → вкладка «Статус AI» → зелёная точка и URL туннеля.

### Чтобы зайти на портал

- Главная: `http://195.208.119.67/` — открыта без пароля
- WB / Финансы / КП — пароль портала (см. СЕКРЕТЫ.local.md, одноразово на сессию)
- Админка: `admin.html` — пароль админки (см. СЕКРЕТЫ.local.md)
- Остальные дашборды (ОКК, HR, Задачник, Рекрутер) — открыты без пароля

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│ Браузер                                                          │
│ http://195.208.119.67/<dashboard>/                               │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ VPS 195.208.119.67 (Ubuntu 24.04)                               │
│ ┌────────────────────────────────────────────────────────────┐  │
│ │ nginx :80                                                  │  │
│ │  /                 → /var/www/okk/index.html               │  │
│ │  /okk/ /wb/ ...    → /var/www/okk/<dir>/                   │  │
│ │  /admin.html       → /var/www/okk/admin.html               │  │
│ │  /tasks/api/*      → 127.0.0.1:8601  (FastAPI)             │  │
│ │  /recruiter/*      → /var/www/okk/recruiter/  (статика)    │  │
│ └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│ systemd:                                                         │
│  • tasks-api.service   FastAPI + SQLite (порт 8601)              │
│                                                                  │
│ FastAPI ↔ Ollama: через прокси /ai/proxy/* → ngrok-URL → Ollama  │
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 │ ngrok HTTPS-туннель
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ Локальный комп (Windows)                                         │
│ • ngrok.exe — туннель https://...ngrok-free.dev → :11434         │
│ • ollama serve — LLM (qwen3:8b) на 127.0.0.1:11434               │
│ • start-ai.ps1 — диспетчер: проверки, запуск, мониторинг         │
└─────────────────────────────────────────────────────────────────┘
```

**Поток AI-запроса:**

1. Браузер → `POST /tasks/api/ai/proxy/chat/stream` → nginx → FastAPI
2. FastAPI читает текущий ngrok-URL из `ai_url.json`
3. FastAPI делает HTTPS-запрос к `https://...ngrok-free.dev/api/chat`
4. ngrok пробрасывает на локальную Ollama
5. Ollama стримит NDJSON-ответ обратно через всю цепочку
6. Браузер показывает текст по мере прихода

---

## Структура репозитория

```
OKK/
├── index.html                  # Главная (каталог карточек)
├── admin.html                  # Админ-панель (промпты + статус AI)
├── start-ai.bat                # Запуск AI-стека (вызывает PS-скрипт)
├── start-ai.ps1                # Корневой launcher (тонкая обертка)
│
├── okk/index.html              # ОКК — Контроль качества колл-центра
├── wb/index.html               # WB — Аутсорсинг Wildberries
├── finance/index.html          # Финансы — P&L, движение денег
├── kp/index.html               # КП — Калькулятор коммерческих предложений
├── hr-game/index.html          # HR-Консультант — чат для HR-вопросов
├── tasks/index.html            # Задачник IT — Kanban/Gantt/Roadmap
├── recruiter/index.html        # Рекрутер — отработка возражений
├── recruiter/app.py            # (deprecated) Streamlit-версия рекрутера
│
├── tasks/api/
│   ├── main.py                 # FastAPI: все эндпоинты
│   ├── recruiter_logic.py      # Логика рекрутера (Google Sheets + LLM-промпт)
│   ├── ai_prompts.json         # Сохранённые промпты из админки
│   ├── ai_url.json             # Текущий ngrok-URL (пишется start-ai.ps1)
│   ├── auth_secret.bin         # HMAC-секрет для токенов (gitignored)
│   ├── tasks.db                # SQLite задач (gitignored)
│   └── recruiter.db            # SQLite рекрутера (gitignored)
│
├── scripts/
│   ├── start-ai.ps1            # Bulletproof launcher v2 (Ollama + ngrok)
│   ├── inject_ai.py            # Инжектит AI-чат в дашборды (wb/fin/kp/hr/okk)
│   ├── inject_auth.py          # Инжектит парольный гейт (только wb/fin/kp)
│   └── setup-server.sh         # Setup VPS: nginx + systemd
│
├── DOCS.md                     # Этот файл
└── README.md                   # Краткое описание (для GitHub)
```

---

## Дашборды

| Дашборд | URL | Размер | Источник данных | AI | Пароль |
|---|---|---|---|---|---|
| Главная | `/` | 312 строк | — | нет | нет |
| ОКК | `/okk/` | 2692 строк | 5 Google Sheets (CSV) | ✅ кнопка ✨ | нет |
| WB | `/wb/` | 3097 строк | GitHub CSV | ✅ кнопка 🤖 | ✅ |
| Финансы | `/finance/` | 3359 строк | GitHub CSV | ✅ кнопка 🤖 | ✅ |
| КП | `/kp/` | 2011 строк | поля формы | ✅ кнопка 🤖 | ✅ |
| HR | `/hr-game/` | 1616 строк | — | ✅ кнопка 🤖 | нет |
| Задачник | `/tasks/` | 3559 строк | FastAPI (`/tasks/api/*`) | ❌ | нет |
| Рекрутер | `/recruiter/` | 854 строк | Google Sheets/Docs | ✅ основной интерфейс | нет |
| Админка | `/admin.html` | 442 строк | FastAPI | — | ✅ admin |

### ОКК (Контроль Качества)

**Назначение:** анализ работы колл-центра подбора складского персонала.

**Источники данных:** 5 листов Google Sheets через CSV-экспорт:
- Прослушка звонков (МПП/ОРП)
- Опросы новичков
- Сводные метрики
- Калибровки
- Чек-листы

**AI:** кнопка ✨ в шапке. Видит данные всех 5 листов целиком. Промпт настраивается в админке (ключ `okk`).

**Уникальное:** ручной AI-код (не через инжектор) — отличается от других дашбордов.

### WB (Wildberries Аутсорсинг)

**Источник:** CSV из приватного GitHub-репо `max511028-bit/WB`.

**Что показывает:** выработка сотрудников, штрафы, операции, риск-зона.

**AI:** плавающая кнопка справа внизу. Контекст: `periods[curPeriod]` (выработка/штрафы/операции по выбранному периоду).

### Финансы

**Источник:** CSV из GitHub.

**Метрики:** P&L по компаниям/филиалам, движение денежных средств, план-факт, проекты, маржа.

**AI:** плавающая кнопка. После недавнего фикса видит **реальные числа**:
- monthly aggregates
- топ-5 проектов по выручке
- убыточные проекты
- топ-3 по марже
- динамика за последние 3 месяца

### КП (Расчёт КП)

**Назначение:** калькулятор стоимости услуг для коммерческих предложений.

**AI:** объясняет расчёт, отвечает на вопросы про поля формы.

### HR-Консультант

**Назначение:** AI-чат для HR-вопросов о процессах найма/оформления/адаптации складского персонала.

**AI:** обычный чат, без аналитики, **без контекста данных** — работает как обычный консультант LLM на промпте `hr-game`.

⚠️ Прежняя ветка задумывалась как «игра» — оценка ответов рекрутёра, лидерборд — не реализовано.

### Задачник IT

**Назначение:** командный центр IT-задач.

**Возможности:** список, Kanban, Gantt, Roadmap, Weekly Review.

**Бэкенд:** SQLite через FastAPI (`/tasks/api/tasks/`, `/lists/`, `/kp/`).

**AI:** **отсутствует.** Самая большая страница без ассистента.

### Рекрутер

**Назначение:** помощь рекрутёру в отработке возражений кандидатов.

**Источники:**
- ТЗ проектов из Google Sheets (`STATS_SHEET_ID`)
- Учебник из Google Docs (`HANDBOOK_DOC_ID`)
- Roadmap проектов (`ROADMAP_SHEET_ID`)

**Логика:** `recruiter_logic.py` — извлекает релевантный кусок ТЗ под возражение через keyword-scoring, инжектит в промпт.

**Кэш:** in-memory с TTL (handbook 24h, projects 5min, TZ 4h).

**Формат ответа:** структурированный (АНАЛИЗ / ВАРИАНТ N / УТОЧНИТЬ / РЕКОМЕНДАЦИИ).

---

## Бэкенд (FastAPI)

**Файл:** `tasks/api/main.py` (~870 строк)

**Запуск:** systemd-сервис `tasks-api.service` → `uvicorn main:app --host 127.0.0.1 --port 8601`

**База:** SQLite (`tasks.db`, `recruiter.db`) рядом с `main.py`.

### Основные эндпоинты

| Метод | Путь | Назначение | Авторизация |
|---|---|---|---|
| `POST` | `/auth/check` | Проверка пароля → токен | — |
| `GET` | `/admin/prompts` | Получить все промпты | `X-Auth-Token` или `?password=` |
| `POST` | `/admin/prompts` | Сохранить промпты | то же |
| `GET` | `/ai/prompt/{dashboard}` | Промпт для одного дашборда (публично) | — |
| `GET` | `/ai/url` | Текущий ngrok-URL | — |
| `POST` | `/ai/url` | Обновить ngrok-URL (whitelist `.ngrok-*`) | — |
| `GET` | `/ai/proxy/tags` | Список моделей Ollama (через туннель) | — |
| `POST` | `/ai/proxy/chat/stream` | Стриминг чата с Ollama | — |
| `GET` | `/tasks/` | Список задач | — |
| `POST` | `/tasks/` | Создать задачу | ⚠️ нет |
| `PUT` | `/tasks/{id}` | Обновить | ⚠️ нет |
| `DELETE` | `/tasks/{id}` | Удалить | ⚠️ нет |
| `GET/POST/PUT/DELETE` | `/lists/*` | CRUD списков | ⚠️ нет |
| `POST` | `/recruiter/generate` | Сгенерировать варианты ответа | — |
| `POST` | `/recruiter/chat` | Чат-режим рекрутера | — |

⚠️ Эндпоинты задачника / списков **не защищены** — любой кто знает API-путь может изменить данные. См. roadmap.

### Конфиг через env

| Переменная | Дефолт | Назначение |
|---|---|---|
| `ADMIN_PASSWORD` | (секрет) | Пароль админки |
| `PORTAL_PASSWORD` | (секрет) | Пароль защищённых дашбордов |

Прописывать в `/etc/systemd/system/tasks-api.service`:
```ini
[Service]
Environment="ADMIN_PASSWORD=..." "PORTAL_PASSWORD=..."
```

Затем `systemctl daemon-reload && systemctl restart tasks-api`.

---

## Авторизация

### Принцип

Пароли **никогда** не попадают в клиентский код. Проверка идёт серверным эндпоинтом `POST /auth/check`. Клиент после успешной проверки получает opaque-токен (HMAC-SHA256 от серверного секрета) и хранит его в `sessionStorage`.

### Flow

```
Браузер                      FastAPI
   │                            │
   │  POST /auth/check          │
   │  {password, kind}          │
   ├───────────────────────────►│
   │                            │ HMAC-проверка
   │  {ok: true, token: "..."}  │
   │◄───────────────────────────┤
   │                            │
   ▼ sessionStorage[K] = token
   
Следующая загрузка страницы:
   │                            │
   │ if (sessionStorage[K]) →   │
   │   скрыть оверлей           │
```

### Что защищено

| Объект | Тип | Пароль | Где |
|---|---|---|---|
| `wb/`, `finance/`, `kp/` | портал | `PORTAL_PASSWORD` | оверлей через `inject_auth.py` |
| Главная, ОКК, HR, Задачник, Рекрутер | — | — | без пароля |
| `/admin.html` | админка | `ADMIN_PASSWORD` | встроенный в HTML гейт + проверка через `/auth/check` |
| `/admin/prompts` (API) | админка | токен `X-Auth-Token` или `?password=` | серверная проверка |

### Серверный секрет

`tasks/api/auth_secret.bin` — 32 случайных байта, генерируется один раз при первом старте FastAPI. **Если удалить — все выданные токены инвалидируются.** Файл должен быть в `.gitignore`.

### ⚠️ Что НЕ защищено

- API задачника (`/tasks/`, `/lists/`) — открыт всем, кто знает endpoint
- AI-эндпоинты (`/ai/proxy/*`) — открыты (это нормально, проксируем публичные модели)
- Файлы статики дашбордов в принципе открыты — оверлей пароля можно обойти через DevTools (sessionStorage можно подделать). Это защита от **случайного** просмотра, не от атаки.

---

## AI-инфраструктура

### Стек

- **Локально:** Ollama + ngrok
- **VPS:** FastAPI-прокси
- **Модель:** `qwen3:8b` (5.2 GB, помещается в 16GB RAM)

### Поток запроса

```
браузер → /tasks/api/ai/proxy/chat/stream → FastAPI →
HTTPS → https://...ngrok-free.dev/api/chat → ngrok →
локальная Ollama (127.0.0.1:11434) → стрим NDJSON →
обратно вверх по цепочке → браузер
```

### CORS

Ollama должна запускаться с `OLLAMA_ORIGINS=*`. Переменная стоит в User env постоянно, после ребута компа Ollama tray её подхватывает.

### Промпты

Хранятся в `tasks/api/ai_prompts.json`. Ключи: `okk`, `wb`, `finance`, `kp`, `hr-game`, `recruiter`. Дефолты — в `DEFAULT_PROMPTS` в `main.py`. Редактируются через `admin.html`.

### Сводка ассистентов

| Дашборд | UX | Контекст | Качество |
|---|---|---|---|
| ОКК | кнопка ✨ + чат | Полные данные 5 листов | ★★★★☆ |
| WB | плавающая 🤖 | Выработка/штрафы/операции (детально) | ★★★★☆ |
| Финансы | плавающая 🤖 | P&L, топ-проекты, маржа, тренды (после фикса) | ★★★★☆ |
| КП | плавающая 🤖 | Поля формы + результат | ★★★☆☆ |
| HR | плавающая 🤖 | **Пустой контекст** — работает как general-purpose chat | ★★☆☆☆ |
| Рекрутер | основной интерфейс | Релевантный кусок ТЗ + учебник | ★★★★★ |

---

## Деплой

### Изменения в HTML/JS

```bash
scp -i ~/.ssh/okk_key_openssh path/to/file.html root@195.208.119.67:/var/www/okk/path/to/file.html
```

### Изменения в бэкенде

```bash
scp -i ~/.ssh/okk_key_openssh tasks/api/main.py root@195.208.119.67:/var/www/okk/tasks/api/main.py
ssh -i ~/.ssh/okk_key_openssh root@195.208.119.67 "systemctl restart tasks-api"
```

### Проверка

```bash
ssh -i ~/.ssh/okk_key_openssh root@195.208.119.67 "systemctl is-active tasks-api && journalctl -u tasks-api -n 20"
```

---

## VPS — что где

| Что | Путь |
|---|---|
| Статика портала | `/var/www/okk/` |
| FastAPI код | `/var/www/okk/tasks/api/` |
| SQLite БД | `/var/www/okk/tasks/api/tasks.db`, `recruiter.db` |
| Google credentials | `/root/google_credentials.json` (chmod 400 рекомендуется) |
| systemd | `/etc/systemd/system/tasks-api.service` |
| nginx config | `/etc/nginx/sites-enabled/okk` |
| nginx logs | `/var/log/nginx/access.log`, `error.log` |
| API logs | `journalctl -u tasks-api -f` |

---

## nginx-конфиг

```nginx
server {
    listen 80;
    server_name 195.208.119.67 _;
    root /var/www/okk;
    index index.html;

    location /tasks/api/ {
        proxy_pass http://127.0.0.1:8601/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;            # критично для стриминга
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    location / {
        try_files $uri $uri/ =404;
    }
}
```

**Важно:** `proxy_buffering off` — без этого AI-стрим в браузер приходит большими блоками с задержками.

---

## Скрипты и инжекторы

### `scripts/start-ai.ps1` (v2)

Bulletproof-лаунчер AI. Делает:

1. Pre-flight: ищет `ngrok.exe`, валидирует `ngrok config check`
2. Убивает стейлые ngrok-процессы (бесплатный тариф = 1 сессия)
3. Проверяет `OLLAMA_ORIGINS=*` в User env (ставит если нет)
4. Запускает Ollama tray если не запущена
5. Проверяет CORS preflight на Ollama
6. Запускает ngrok с захватом stdout/stderr в `%LOCALAPPDATA%\sth-ai-launcher\ngrok.log`
7. Fail-fast: если ngrok падает — сразу читает лог и ищет известные ошибки (`ERR_NGROK_108` = session limit, `ERR_NGROK_105` = auth)
8. Получает HTTPS-URL из `127.0.0.1:4040/api/tunnels`
9. Публикует URL на VPS: `POST http://195.208.119.67/tasks/api/ai/url`
10. Keep-alive: каждые 15с проверяет жив ли туннель и процесс ngrok
11. На выходе: чистит URL на VPS, гасит ngrok-процесс

**Режим диагностики:** `start-ai.bat -Diagnose` или прямой вызов с флагом — все проверки без поднятия туннеля. Полезно когда что-то ломается.

**Логи:** `%LOCALAPPDATA%\sth-ai-launcher\`:
- `launch.log` — история запусков
- `ngrok.log` — stdout ngrok с таймстампами
- `ngrok.log.err` — stderr
- `ollama.log` — если запускался свой ollama

### `scripts/inject_ai.py`

Инжектит плавающую AI-кнопку + чат в HTML дашбордов.

**Использование:**
```bash
python scripts/inject_ai.py wb/index.html
```

**Что встраивает:**
- кнопка 🤖 в правом нижнем углу
- модалка чата с прокруткой
- стриминг через `/tasks/api/ai/proxy/chat/stream`
- сбор контекста из `window.chartData` / `window.dashboardData`

### `scripts/inject_auth.py` (v2)

Инжектит/удаляет парольный гейт.

**Использование:**
```bash
python scripts/inject_auth.py inject wb/index.html finance/index.html kp/index.html
python scripts/inject_auth.py remove old-protected-file.html
```

**Что встраивает:** оверлей на весь экран, форма пароля, при отправке делает POST на `/tasks/api/auth/check`. Пароль в коде НЕ хранится.

---

## Источники данных

| Дашборд | Источник | Тип |
|---|---|---|
| ОКК | 5 Google Sheets | CSV-экспорт |
| WB | GitHub `max511028-bit/WB` (приватный) | CSV |
| Финансы | GitHub | CSV |
| КП | поля формы | в браузере |
| HR | — | только статичный промпт |
| Задачник | FastAPI + SQLite | REST |
| Рекрутер | Google Sheets (ТЗ) + Google Doc (учебник) + Google Sheets (roadmap) | API |

### Google Service Account

Используется для рекрутера. Credentials лежат в `/root/google_credentials.json` на VPS.

Доступы выданы service account email на:
- `STATS_SHEET_ID` (ТЗ проектов)
- `HANDBOOK_DOC_ID` (учебник)
- `ROADMAP_SHEET_ID` (roadmap)

---

## Запуск AI после ребута компа

1. Открой Ollama tray из меню «Пуск» (или дождись автозапуска — иконка в трее).
2. Дабл-клик `start-ai.bat` в корне репо.
3. В окне `cmd.exe` пройдут проверки → должна появиться зелёная строка `AI is available for all users!` с URL туннеля.
4. **Не закрывай окно** — AI работает только пока оно открыто.
5. Проверь админку: `admin.html` → пароль админки (см. СЕКРЕТЫ.local.md) → вкладка «Статус AI» → зелёная точка.

Если что-то пошло не так — см. [Troubleshooting](#troubleshooting).

---

## Troubleshooting

### Скрипт `start-ai.bat` сразу закрылся

Запусти из PowerShell вручную: `pwsh scripts\start-ai.ps1` — увидишь ошибку.

### `Could not get tunnel URL`

1. Запусти `start-ai.bat -Diagnose` — покажет на каком шаге падает.
2. Посмотри лог: `%LOCALAPPDATA%\sth-ai-launcher\ngrok.log`.
3. Частые причины:
   - **`ERR_NGROK_108` (session limit)** → старая сессия не отвалилась. Зайди на https://dashboard.ngrok.com/agents и убей.
   - **`ERR_NGROK_105` (auth)** → токен в `ngrok.yml` протух. Перепрописать: `ngrok config add-authtoken <TOKEN>`.
   - **Port 4040 busy** → другой ngrok жив. `Get-Process ngrok | Stop-Process -Force`.

### `Статус AI: Оффлайн` в админке

1. Окно `start-ai.bat` ещё открыто? Если закрыли — перезапусти.
2. `http://localhost:11434` в браузере должно отдавать `Ollama is running`.
3. На VPS проверь URL: `curl http://195.208.119.67/tasks/api/ai/url` — должен быть не `null`.

### AI отвечает «AI оффлайн» в браузере, но в админке всё ок

Проверь DevTools → Network → запрос на `/tasks/api/ai/proxy/chat/stream` → код ответа и body. Возможно nginx не пускает или таймаут на стороне VPS.

### Рекрутер: «Не нашлось ни одного проекта»

На VPS пропал `google_credentials.json` или service account потерял доступ к таблице. Проверь:
```bash
ssh root@195.208.119.67 "ls -la /root/google_credentials.json && cat /root/google_credentials.json | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"client_email\"])'"
```

### AI отвечает с задержкой, текст приходит блоками

Проблема с `proxy_buffering` в nginx. Проверь `/etc/nginx/sites-enabled/okk` — для `/tasks/api/` должно быть `proxy_buffering off`.

### Хочу сменить пароль

1. SSH на VPS.
2. Открой `/etc/systemd/system/tasks-api.service`, в `[Service]` добавь:
   ```
   Environment="ADMIN_PASSWORD=новыйпароль"
   Environment="PORTAL_PASSWORD=другойпароль"
   ```
3. `systemctl daemon-reload && systemctl restart tasks-api`.
4. Удали `tasks/api/auth_secret.bin` если хочешь инвалидировать старые сессии.

---

## История изменений

### 2026-05-12 — Auth refactor + ngrok launcher v2

- Пароли вынесены из клиентского JS на бэкенд (`/auth/check`)
- HMAC-токены вместо `btoa(password)` сравнения
- Защита оставлена только на wb / finance / kp
- `start-ai.ps1` переписан: pre-flight checks, fail-fast, парсинг ошибок ngrok, режим `-Diagnose`
- Запуск ngrok через `ngrok http 11434` инлайн (вместо `start <tunnel>`) — не зависит от секции `tunnels:` в yml
- Финансы AI: реальный контекст (P&L, топ-проекты, маржа, тренды) вместо `JSON.stringify().slice(0,2000)`
- HR: переименован из «HR-Игра» в честный «HR-Консультант»
- Валидация ngrok-URL: whitelist `*.ngrok-free.dev/.app/.io`

### 2026-05-XX — Streaming + Admin panel

- Стриминг всех AI-ответов через NDJSON
- Админ-панель с редактируемыми системными промптами
- Рекрутер вынесен в отдельный интерфейс
- Smart TZ extraction для рекрутера (keyword scoring)

---

## Известные проблемы и roadmap

### Critical (нужно исправить)

- [ ] **API задачника без авторизации** — любой может удалить чужие задачи через POST/DELETE
- [ ] **HTTP, не HTTPS** — пароли и данные идут в открытом виде
- [ ] **Rate-limiting на `/auth/check`** — можно брутфорсить
- [ ] **Пароли в `.env`** — сейчас работают дефолты в коде

### High

- [ ] Whitelist для `/ai/url` в проде, отдельный токен для записи URL
- [ ] `main.py` 870 строк в одном файле → разбить на handlers/services
- [ ] Логирование вместо `print` / `except: pass`
- [ ] Архивация SQLite раз в день

### Medium

- [ ] AI-ассистент в Задачник IT (самая большая страница без AI)
- [ ] HR — реальная геймификация или удалить (сейчас просто чат)
- [ ] SQLite-кэш для рекрутера (TZ + ответы)
- [ ] Тесты на `recruiter_logic.py` (extract_relevant_tz, _detect_objection_type)
- [ ] CI/CD: GitHub Actions для деплоя (сейчас всё руками SCP)
- [ ] Единый компонент состояний loading/error/empty

### Low

- [ ] Минификация HTML/JS на nginx (Gzip, Brotli)
- [ ] Lazy-load Chart.js
- [ ] Метрика использования AI (по дашбордам / частые вопросы)
- [ ] Бэкапы в S3 / другой VPS
- [ ] Sentry или хотя бы лог-файл

---

## Контакты и доступы

- **VPS:** `root@195.208.119.67` (SSH-ключ `okk_key_openssh`)
- **ngrok dashboard:** https://dashboard.ngrok.com/agents
- **GitHub:** https://github.com/max511028-bit/OKK
- **Ollama tray:** трей Windows на компе пользователя

---

*Документация актуальна на 2026-05-12.*
