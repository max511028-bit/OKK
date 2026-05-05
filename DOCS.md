# Аналитический портал — Полная документация

> Production: **http://195.208.119.67/**
> Репозиторий: **https://github.com/max511028-bit/OKK**
> Пароль входа: **`511028`** (на всех дашбордах кроме главной)

---

## Оглавление

1. [Архитектура](#архитектура)
2. [Структура репозитория](#структура-репозитория)
3. [Деплой и CI/CD](#деплой-и-cicd)
4. [VPS — что и где запущено](#vps--что-и-где-запущено)
5. [nginx — маршрутизация](#nginx--маршрутизация)
6. [Дашборды (frontend)](#дашборды-frontend)
   - [Главная](#главная)
   - [ОКК — Контроль качества](#окк--контроль-качества)
   - [WB — Аутсорсинг](#wb--аутсорсинг)
   - [Финансы](#финансы)
   - [КП — Коммерческое предложение](#кп--коммерческое-предложение)
   - [Задачник IT](#задачник-it)
   - [HR-игра](#hr-игра)
   - [AI-Рекрутер](#ai-рекрутер)
7. [Backend сервисы](#backend-сервисы)
   - [Tasks API (FastAPI + SQLite)](#tasks-api-fastapi--sqlite)
   - [AI Recruiter (Streamlit)](#ai-recruiter-streamlit)
   - [Ollama](#ollama)
8. [Скрипты-инжекторы](#скрипты-инжекторы)
9. [Источники данных](#источники-данных)
10. [Аутентификация](#аутентификация)
11. [Что делать если…](#что-делать-если)

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│  Браузер пользователя                                       │
│  http://195.208.119.67/<dashboard>/                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  VPS 195.208.119.67 (Ubuntu 24.04)                          │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ nginx :80                                               │ │
│  │  ├─ /                  → /var/www/okk/index.html        │ │
│  │  ├─ /okk/, /wb/, …     → статика из /var/www/okk/<dir>/ │ │
│  │  ├─ /tasks/            → /var/www/okk/tasks/index.html  │ │
│  │  ├─ /tasks/api/        → 127.0.0.1:8601 (FastAPI)       │ │
│  │  ├─ /ollama/           → 127.0.0.1:11434 (Ollama)       │ │
│  │  └─ /recruiter/        → 127.0.0.1:8501 (Streamlit)     │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  systemd сервисы:                                           │
│  • tasks-api.service   (FastAPI на venv)                    │
│  • recruiter.service   (Streamlit)                          │
│  • ollama.service      (LLM сервер)                         │
└─────────────────────────────────────────────────────────────┘
                           ▲
                           │ rsync + SSH
                           │
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions (push → main)                               │
│  • Шаг 1: rsync файлов                                      │
│  • Шаг 2: запись service_account.json                       │
│  • Шаг 3: bash setup-server.sh                              │
└─────────────────────────────────────────────────────────────┘
                           ▲
                           │
                           │
┌─────────────────────────────────────────────────────────────┐
│  Git push → main                                            │
└─────────────────────────────────────────────────────────────┘

Источники данных:
• Google Sheets (CSV-export через gid) → читают ОКК, ВБ, Финансы
• Локальная SQLite БД → Задачник, история КП
• Локальные JSON в HTML → КП, HR-игра
```

**Технологии:**
- Frontend: ванильный HTML/CSS/JS, Chart.js (CDN)
- Backend: Python 3.12, FastAPI, Streamlit, SQLite
- AI: Ollama (llama3.2:3b локально + cloud-модели)
- Хостинг: один VPS под Ubuntu 24.04 + nginx 1.24
- CI/CD: GitHub Actions

---

## Структура репозитория

```
OKK/
├── index.html              # Главная страница портала (тайлы)
├── DOCS.md                 # ← вы здесь
├── .github/
│   └── workflows/
│       ├── deploy.yml      # Автодеплой на VPS при push в main
│       └── sync-finance.yml
│
├── okk/index.html          # Дашборд контроля качества колл-центра
├── wb/index.html           # WB Аутсорсинг (выработка/штрафы/карта)
├── finance/index.html      # Финансы (P&L, рентабельность)
├── kp/index.html           # Конструктор коммерческих предложений
├── tasks/index.html        # IT-Задачник (Kanban + Gantt + Roadmap)
├── tasks/api/              # FastAPI backend для задачника
│   ├── main.py
│   ├── requirements.txt
│   └── seed.json
├── hr-game/index.html      # Геймификация для рекрутеров
│
├── recruiter/              # Streamlit AI-рекрутер
│   ├── app.py
│   ├── requirements.txt
│   └── index.html          # Stub с редиректом на /recruiter/
│
└── scripts/
    ├── setup-server.sh     # Запускается на VPS после деплоя
    ├── inject_ai.py        # Вшивает плавающую AI-панель в дашборды
    └── inject_auth.py      # Вшивает оверлей пароля
```

---

## Деплой и CI/CD

### Триггер
Любой `git push` в ветку **`main`** запускает `.github/workflows/deploy.yml`.

### Шаги workflow

1. **Checkout** репозитория
2. **rsync** всех файлов на VPS в `/var/www/okk/`:
   ```
   rsync -avz --delete --exclude='.git' --exclude='.github'
   ```
   Используется SSH-ключ из секрета `VPS_SSH_KEY`.
3. **Заливка `service_account.json`** для AI-Рекрутера из секрета `GOOGLE_SERVICE_ACCOUNT_INFO`
4. **Запуск `bash /var/www/okk/scripts/setup-server.sh`** — настраивает nginx, Ollama, systemd-сервисы

### GitHub Secrets (нужны в репозитории)

| Имя | Что |
|---|---|
| `VPS_SSH_KEY` | Приватный SSH-ключ для root@195.208.119.67 |
| `GOOGLE_SERVICE_ACCOUNT_INFO` | JSON-ключ Google Cloud сервисного аккаунта (для Streamlit-рекрутера) |

### Время деплоя
~1 мин 30 сек: rsync → setup-server.sh → пересоздание venv (если нужно) → reload nginx

---

## VPS — что и где запущено

| Что | Путь / порт | Запуск |
|---|---|---|
| Статика портала | `/var/www/okk/` | nginx |
| nginx | :80 (HTTP) | `systemctl status nginx` |
| Ollama (LLM) | :11434 | `systemctl status ollama` |
| AI Recruiter (Streamlit) | :8501 | `systemctl status recruiter` |
| Tasks API (FastAPI) | :8601 | `systemctl status tasks-api` |
| SQLite база задачника | `/var/www/okk/tasks/api/tasks.db` | — |
| venv задачника | `/var/www/okk/tasks/api/.venv/` | создаётся в setup-server.sh |
| Логи systemd | `journalctl -u <service> -n 50` | — |

---

## nginx — маршрутизация

Конфиг в `/etc/nginx/sites-enabled/okk` (основной для домена) и `/etc/nginx/sites-enabled/default` (для прочих Host).

Сниппеты создаёт `setup-server.sh`:

- **`/etc/nginx/snippets/portal-proxies.conf`** — прокси для `/ollama/` и `/recruiter/`
- **`/etc/nginx/snippets/tasks-api-proxy.conf`** — прокси только для `/tasks/api/`

### Локации

| URL | Назначение | Прокси на |
|---|---|---|
| `/` | главная | static |
| `/okk/`, `/wb/`, `/finance/`, `/kp/`, `/hr-game/`, `/tasks/` | дашборды | static |
| `/tasks/api/*` | API задачника | `127.0.0.1:8601` |
| `/ollama/api/*` | Ollama API (для AI-чата) | `127.0.0.1:11434` |
| `/recruiter/*` | Streamlit UI | `127.0.0.1:8501` |
| `/recruiter/_stcore/` | Streamlit websocket | `127.0.0.1:8501` (с `Upgrade`) |

---

## Дашборды (frontend)

Все дашборды — **single-file HTML** со встроенными CSS и JS. Без сборки, без npm. Тёмная тема, шрифт Inter, графики через Chart.js (CDN).

### Главная

**Файл:** `index.html` (~190 строк)
**Что делает:** показывает 7 тайлов, ссылающихся на разделы. Без логики и данных. Не требует пароля.

---

### ОКК — Контроль качества

**Файл:** `okk/index.html` (~2660 строк)
**URL:** `/okk/`
**Источник данных:** Google Sheets (5 листов CSV)

**Разделы:**
1. **Сводка КЦ** — 4 KPI-карточки (% прослушки, баллы МПП/ОРП, CSAT) + линейный график динамики
2. **Прослушка** — рейтинг рекрутеров, карточки, фильтр по месяцу/дивизиону, **модалка с радар-графиком 21 критерия**
3. **Динамика обучения** — отслеживание роста показателей до/после обучения
4. **МПП** (методы первичного подбора) — оценки по компетенциям
5. **ОРП** (отбор резюме по проверкам) — оценки проверок
6. **Опросы** — CSAT/NPS по складам, должностям, городам

**Особенности:**
- Все процентные значения парсятся через `parsePercent()` (запятые → точки, `0.79 → 79%`)
- Кириллица в CSV — обрабатывается напрямую без перекодировки
- Геймификация: на карточке рекрутера до 4 медалей (`🥇` лучший месяц, `📈` рост 3 мес, `⭐` >90%, `🎧` 50+ звонков)
- Сравнение «было/стало» — бейдж с зелёной/красной/нейтральной стрелкой над модалкой

**ИИ-помощник:** встроен напрямую в HTML (не через `inject_ai.py`), функция `loadOllamaModels()`, селектор моделей с retry-on-click, таймаут 8с.

---

### WB — Аутсорсинг

**Файл:** `wb/index.html` (~3030 строк)
**URL:** `/wb/`

**Разделы:**
- 📊 Дашборд: KPI выработки, штрафов, операций
- 👥 Сотрудники: рейтинг, фильтры, риск-зона
- 💰 ФОТ: разбивка по складам и периодам
- 📡 **Аномалии**: автоматический детектор (штраф > 3× нормы) + кнопка «Скопировать отчёт» для Telegram-рассылки
- 🗺 **Карта России**: SVG с ~40 городами, цвет круга = коэф. штраф/выручка
- ⚙️ Прочее: типы операций, типы штрафов

**ИИ-помощник:** через `inject_ai.py wb` (плавающая кнопка 🤖, чат с моделью).

---

### Финансы

**Файл:** `finance/index.html` (~3240 строк)
**URL:** `/finance/`

**Разделы:**
- KPI: выручка, маржа, прибыль, средний чек
- Тренды: линейные графики по месяцам
- Waterfall: разбивка маржи
- ZP: доля ФОТ
- По дивизионам: радары и stacked bars
- По клиентам / городам / проектам: drill-down таблицы
- **AI-Объяснение каждого графика**: рядом с canvas — кнопка `🤖 Объяснить` (атачится через `setInterval` каждые 3с — переживает перерисовку)

**ИИ-помощник:** через `inject_ai.py finance`.

---

### КП — Коммерческое предложение

**Файл:** `kp/index.html` (~1940 строк)
**URL:** `/kp/`

**Что делает:** конструктор PDF-стайл коммерческого предложения для аутсорсинга персонала.

- Параметры: город, кол-во человек, ФОТ, накладные, маржа
- Автопересчёт всех таблиц
- **Сохранение состояния** в БД (`/tasks/api/kp` — да, сидит в той же FastAPI)
- **Шаблоны** (`is_template=true`): можно сохранить готовое КП и переиспользовать
- **История**: все сохранённые КП с датой
- Печать через `window.print()` стилем PDF

**ИИ-помощник:** `inject_ai.py kp`.

---

### Задачник IT

**Файл:** `tasks/index.html` (~3560 строк)
**URL:** `/tasks/`
**Backend:** FastAPI на `/tasks/api/`

**Что делает:** перенесённый Command Center для IT-команды на ВКР/СТХ.

**Разделы:**
- 📊 Dashboard — KPI команды и здоровья проекта
- 📈 Аналитика
- 📅 Weekly Review — план/факт/просрочено/решения недели
- ✅ **Задачи** — главный реестр (47 активных)
- 📋 Kanban — карточки по статусам
- 📊 Gantt — таймлайн с фазами
- 🗺 Roadmap — стратегические инициативы
- 📑 ТЗ — реестр технических заданий

**Создание/редактирование задач:**
- Кнопка `+ Новая задача` (плавающая FAB снизу справа)
- Модалка с 14 полями (id, title, desc, system, type, priority, risk, assignee, deadline, status, tz, metric, effect, blockers, deps)
- Сохраняется в SQLite через `POST /tasks/api/tasks`
- Изменения логируются в таблицу `task_history`

**Toast-уведомления:**
- 🟢 «Подключено к серверу»
- 🟡 «Офлайн-режим (изменения не сохраняются)» — если API недоступен после 3 ретраев
- 🔴 «Не сохранено: HTTP 500» и т.п.

---

### HR-игра

**Файл:** `hr-game/index.html`
**URL:** `/hr-game/`

Геймифицированная мотивация для рекрутеров — баллы, рейтинги, ачивки.

**ИИ-помощник:** `inject_ai.py hr-game`.

---

### AI-Рекрутер

**URL:** `/recruiter/`
**Это Streamlit-приложение**, отдельное от HTML-дашбордов.

**Файл:** `recruiter/app.py` (~790 строк)
**Что делает:**
- Читает кандидатов из Google Sheets через `gspread`
- Через Ollama-модель (локально на VPS) проводит первичный скрининг
- Отвечает на вопросы рекрутера, генерирует тексты вакансий, скоринг резюме
- Использует `service_account.json` для GCP

---

## Backend сервисы

### Tasks API (FastAPI + SQLite)

**Файл:** `tasks/api/main.py` (307 строк)
**Запуск:** `tasks-api.service` через `/var/www/okk/tasks/api/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8601`
**База:** `/var/www/okk/tasks/api/tasks.db`
**Префикс маршрутов в коде:** `root_path="/tasks/api"` → реальные пути нацелены на nginx-проксирование

**Схема БД:**

| Таблица | Поля |
|---|---|
| `tasks` | id, title, data (JSON всех полей задачи), created_at, updated_at |
| `task_history` | id, task_id, event (текст изменения), ts |
| `lists` | name (PK), data (JSON), updated_at — для weekly_*, roadmap, tz, gantt |
| `kp_history` | id, title, city, people, payload (JSON), is_template, created_at |

**Endpoints:**

| Метод | Путь | Что делает |
|---|---|---|
| `GET` | `/state` | вся снимка: tasks + все lists |
| `GET` | `/tasks` | список задач |
| `GET` | `/tasks/{id}` | одна задача |
| `POST` | `/tasks` | создать |
| `PUT` | `/tasks/{id}` | обновить (логируется diff в history) |
| `DELETE` | `/tasks/{id}` | удалить |
| `GET` | `/tasks/{id}/history` | хронология изменений |
| `PUT` | `/lists/{name}` | обновить weekly_plan / roadmap / gantt и др. |
| `GET` | `/kp` | список сохранённых КП (`?template=true` для шаблонов) |
| `POST` | `/kp` | сохранить КП |
| `DELETE` | `/kp/{id}` | удалить КП |
| `GET` | `/health` | `{"ok": true}` |

**Seed:** при первом запуске (если в `tasks` ноль строк) импортирует `seed.json` с 15 задачами, 8 roadmap, 14 gantt, 6 tz.

**CORS:** `allow_origins=["*"]` (нужно сузить).

---

### AI Recruiter (Streamlit)

**Файл:** `recruiter/app.py`
**Запуск:** `recruiter.service`
```
python3 -m streamlit run app.py --server.port 8501
                                --server.address 127.0.0.1
                                --server.baseUrlPath /recruiter
```

**Зависимости (`requirements.txt`):** streamlit, gspread, oauth2client, google-api-python-client, requests, openpyxl и др.

**Что использует:**
- `service_account.json` (или env-переменная `GOOGLE_SERVICE_ACCOUNT_INFO`) — доступ к Google Sheets
- `127.0.0.1:11434/api/generate` — локальная Ollama для генерации ответов

---

### Ollama

**Запуск:** `ollama.service` (стандартный systemd-юнит от установщика)
**API:** `127.0.0.1:11434`
**Модели:** скачиваются в `~/.ollama/models/`. По умолчанию ставится `llama3.2:3b`.

**Доступ через nginx:** `/ollama/api/tags`, `/ollama/api/chat` (используется AI-панелями всех дашбордов).

---

## Скрипты-инжекторы

### `scripts/inject_auth.py`

```bash
python scripts/inject_auth.py okk/index.html wb/index.html …
```

Вшивает после `<body>` JS-оверлей с полем пароля. Пароль `511028` хранится как `btoa()` = `NTExMDI4`. После ввода — `sessionStorage.setItem('p_auth', ...)`. **Не криптография**, просто блокировка от случайных пользователей.

### `scripts/inject_ai.py`

```bash
python scripts/inject_ai.py wb/index.html wb
python scripts/inject_ai.py finance/index.html finance
python scripts/inject_ai.py kp/index.html kp
python scripts/inject_ai.py hr-game/index.html hr-game
```

Вшивает в конец `<body>` плавающую AI-кнопку 🤖 + панель чата. Шаблон один, но `CONFIGS[type]` содержит:
- `title`, `greeting`, `suggestions` — UI-тексты
- `system_prompt` — роль модели
- `build_ctx` — JS-функция, собирающая контекст с дашборда (передаётся в Ollama вместе с вопросом)

Для **OKK** AI-чат написан **отдельно прямо в `okk/index.html`** (не через инжектор) — там много специфики.

### `scripts/setup-server.sh`

Запускается **на VPS** после деплоя. Что делает:

1. Записывает nginx-сниппеты (`portal-proxies.conf` + `tasks-api-proxy.conf`)
2. Через **Python-парсер** вставляет нужные `include` в `server { }` блоки **всех** site-конфигов nginx, **избегая дубликатов** (если в файле уже есть `/ollama/`, не вставляется portal-proxies; tasks-api-proxy всегда безопасен)
3. Ставит/запускает Ollama
4. Создаёт `recruiter.service` и стартует
5. Создаёт venv в `/var/www/okk/tasks/api/.venv/`, ставит fastapi/uvicorn, создаёт `tasks-api.service`
6. `nginx -t && reload`
7. Печатает диагностику: статус сервисов, health-check через `Host: 195.208.119.67`

---

## Источники данных

### Google Sheets (для ОКК, ВБ, Финансов)

В каждом HTML захардкожены ID листов и `gid` страниц. Загрузка идёт через CSV-export:
```
https://docs.google.com/spreadsheets/d/{ID}/export?format=csv&gid={GID}
```

**Никакого OAuth** — таблицы должны быть **«доступны по ссылке»** (View). Это работает потому что портал размещён на GitHub Pages-аналоге (статика), и CORS на CSV-export разрешён Google.

### SQLite (для задачника + истории КП)

`/var/www/okk/tasks/api/tasks.db` — управляется FastAPI. Бэкап — копирование файла.

### Локальные JSON в HTML (для КП, HR-игры)

Конфиги статически вшиты в `<script>` блоки. Для смены — редактировать HTML и push.

---

## Аутентификация

| Что | Как |
|---|---|
| Пароль на дашборды | `inject_auth.py` → `btoa('511028')` сравнивается с введённым. Слабая защита — base64 видно в DevTools. Подойдёт против случайных людей с правильной ссылкой. |
| API задачника | **нет аутентификации** (CORS открыт). Защищён только тем, что URL не публикуется и пароль на UI. |
| Streamlit-рекрутер | без аутентификации (защищён через основной btoa-оверлей в `recruiter/index.html`-обёртке) |

---

## Что делать если…

### …задачник пишет «Офлайн-режим»

1. Проверить https://195.208.119.67/tasks/api/health — должно вернуть `{"ok":true}`
2. Если 404 — посмотреть GitHub Actions последнего run, шаг **Run server setup** → строки `[tasks-api] Health check`, `=== nginx tasks/api proxy config ===`
3. Если health внутренний 200, а внешний 404 — проблема в nginx-include (см. историю фиксов в `setup-server.sh`)

### …ИИ-помощник стоит на «Загрузка…»

1. Проверить http://195.208.119.67/ollama/api/tags
2. Если пусто — `systemctl status ollama` на VPS
3. Если работает но дашборд не видит — DevTools → Network → запрос `tags` → смотреть статус. Возможен 502 если ollama упал

### …нужно добавить новый дашборд

1. Создать `<имя>/index.html`
2. (опционально) `python scripts/inject_auth.py <имя>/index.html` — пароль
3. (опционально) Добавить в `scripts/inject_ai.py` блок в `CONFIGS` и запустить
4. Добавить тайл в главный `index.html`
5. Push → автодеплой

### …нужно поменять пароль входа

1. В Python: `>>> import base64; base64.b64encode(b'НОВЫЙ').decode()`
2. Замените `T='NTExMDI4'` в `scripts/inject_auth.py`
3. Запустите `python scripts/inject_auth.py okk/index.html wb/index.html finance/index.html kp/index.html tasks/index.html hr-game/index.html`
4. Push

### …нужно посмотреть логи на VPS

```bash
ssh root@195.208.119.67
journalctl -u tasks-api -n 100
journalctl -u recruiter -n 100
journalctl -u ollama -n 100
tail -50 /var/log/nginx/error.log
tail -50 /var/log/nginx/access.log
```

### …нужно бэкапнуть задачник

```bash
ssh root@195.208.119.67 'cat /var/www/okk/tasks/api/tasks.db' > backup-$(date +%F).db
```

### …нужно скачать новую модель Ollama

```bash
ssh root@195.208.119.67 'ollama pull qwen3:8b'
```

---

## Контакты и ответственные

(заполнить)

---

_Последнее обновление: автоматически генерируется при правках._
