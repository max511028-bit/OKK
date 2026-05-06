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
6. [AI Ассистент — подробное описание](#ai-ассистент--подробное-описание)
7. [Дашборды (frontend)](#дашборды-frontend)
8. [Backend сервисы](#backend-сервисы)
9. [Скрипты-инжекторы](#скрипты-инжекторы)
10. [Источники данных](#источники-данных)
11. [Безопасность](#безопасность)
12. [Что делать если…](#что-делать-если)

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
│  │  ├─ /okk/, /wb/, …     → статика /var/www/okk/<dir>/   │ │
│  │  ├─ /tasks/            → /var/www/okk/tasks/index.html  │ │
│  │  ├─ /tasks/api/        → 127.0.0.1:8601 (FastAPI)       │ │
│  │  ├─ /ollama/           → 178.63.16.109:11434 (внешний)  │ │
│  │  └─ /recruiter/        → 127.0.0.1:8501 (Streamlit)     │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  systemd сервисы:                                           │
│  • tasks-api.service   (FastAPI + SQLite)                   │
│  • recruiter.service   (Streamlit)                          │
└─────────────────────────────────────────────────────────────┘
         │                              │
         │ rsync + SSH                  │ прямой fetch из браузера
         ▼                              ▼
  GitHub Actions              178.63.16.109:11434
  (push → main)               Ollama сервер IT-команды
```

**Технологии:**
- Frontend: HTML/CSS/JS, Chart.js (CDN)
- Backend: Python 3.12, FastAPI, SQLite, Streamlit
- AI: Ollama на сервере IT-команды (`178.63.16.109:11434`)
- Хостинг: VPS Ubuntu 24.04 + nginx 1.24
- CI/CD: GitHub Actions

---

## Структура репозитория

```
OKK/
├── index.html              # Главная страница (тайлы)
├── DOCS.md                 # ← вы здесь
├── .github/workflows/
│   └── deploy.yml          # Автодеплой на VPS при push в main
│
├── okk/index.html          # Дашборд контроля качества колл-центра
├── wb/index.html           # WB Аутсорсинг
├── finance/index.html      # Финансы
├── kp/index.html           # Коммерческие предложения
├── tasks/index.html        # IT-Задачник
├── tasks/api/              # FastAPI backend задачника
│   ├── main.py
│   ├── requirements.txt
│   ├── seed.json
│   └── tasks.db            # SQLite база (НЕ в git, не трогается rsync)
├── hr-game/index.html      # HR-геймификация
│
├── recruiter/              # Streamlit AI-рекрутер
│   ├── app.py
│   └── requirements.txt
│
└── scripts/
    ├── setup-server.sh     # Запускается на VPS после деплоя
    ├── inject_ai.py        # Вшивает AI-панель в дашборды
    └── inject_auth.py      # Вшивает оверлей пароля
```

---

## Деплой и CI/CD

### Триггер
Любой `git push` в ветку **`main`** → запускает `.github/workflows/deploy.yml`.

### Шаги
1. **rsync** файлов на VPS в `/var/www/okk/` (с исключениями — база данных и venv не трогаются)
2. **Запись `service_account.json`** из GitHub Secret (права 600)
3. **`bash setup-server.sh`** — настройка nginx, systemd-сервисов

### GitHub Secrets

| Имя | Что |
|---|---|
| `VPS_SSH_KEY` | Приватный SSH-ключ для root@195.208.119.67 |
| `GOOGLE_SERVICE_ACCOUNT_INFO` | JSON-ключ GCP для Streamlit-рекрутера |

### Защита данных при деплое
`rsync` **не удаляет и не перезаписывает:**
- `tasks/api/tasks.db` — база задачника (данные сохраняются между деплоями)
- `tasks/api/.venv` — Python-окружение (пересоздаётся только если нет)
- `**/__pycache__` — кэш Python

---

## VPS — что и где запущено

| Что | Путь / порт | Управление |
|---|---|---|
| Статика портала | `/var/www/okk/` | nginx |
| nginx | :80 | `systemctl status nginx` |
| Tasks API (FastAPI) | :8601 | `systemctl status tasks-api` |
| AI Recruiter (Streamlit) | :8501 | `systemctl status recruiter` |
| SQLite база | `/var/www/okk/tasks/api/tasks.db` | — |
| Python venv | `/var/www/okk/tasks/api/.venv/` | — |
| Логи | — | `journalctl -u tasks-api -n 50` |

> **Ollama не установлен на VPS** — используется внешний сервер IT-команды.

---

## nginx — маршрутизация

Конфиг: `/etc/nginx/sites-enabled/okk` (основной для IP 195.208.119.67)

Сниппеты (создаются `setup-server.sh`):
- `/etc/nginx/snippets/portal-proxies.conf` — прокси для `/recruiter/`
- `/etc/nginx/snippets/tasks-api-proxy.conf` — прокси для `/tasks/api/`

### Таблица маршрутов

| URL | Куда | Примечание |
|---|---|---|
| `/` | static `index.html` | главная |
| `/okk/`, `/wb/` и др. | static | дашборды |
| `/tasks/` | static `tasks/index.html` | задачник UI |
| `/tasks/api/*` | `127.0.0.1:8601` | FastAPI |
| `/ollama/*` | `178.63.16.109:11434` | Ollama IT-команды |
| `/recruiter/*` | `127.0.0.1:8501` | Streamlit |

---

## AI Ассистент — подробное описание

### Как это работает

Каждый дашборд имеет встроенную кнопку 🤖 в правом нижнем углу. При нажатии открывается чат-панель с выбором модели и полем для вопроса.

**Схема запроса:**

```
Браузер пользователя
       │
       │  1. GET /api/tags  (загрузка списка моделей)
       │  2. POST /api/chat  (отправка вопроса)
       ▼
178.63.16.109:11434  ←── Ollama-сервер IT-команды
```

Запросы идут **напрямую из браузера** на сервер IT-команды — наш VPS в цепочке не участвует.

> **Примечание для IT:** Альтернативный маршрут через наш VPS (`/ollama/` → `178.63.16.109:11434`) тоже настроен в nginx, но браузер обращается напрямую для снижения задержки.

### Технические параметры подключения

| Параметр | Значение |
|---|---|
| API endpoint (модели) | `http://178.63.16.109:11434/api/tags` |
| API endpoint (чат) | `http://178.63.16.109:11434/api/chat` |
| Аутентификация | не требуется |
| Таймаут | 8 секунд на загрузку моделей |
| Формат запроса | JSON, `stream: false` |

### Пример запроса (curl)

```bash
curl http://178.63.16.109:11434/api/chat -d '{
  "model": "minimax-m2.7:cloud",
  "messages": [{ "role": "user", "content": "Твой вопрос" }],
  "stream": false
}'
```

### Доступные модели (текущий список)

| Модель | Тип | Размер |
|---|---|---|
| `minimax-m2.7:cloud` | облачная | — |
| `qwen3-coder-next:cloud` | облачная | — |
| `qwen3.6:35b` | локальная | 36B |
| `qwen3:30b` | локальная | 30B |
| `qwen3-coder:30b` | локальная | 30B |
| `llama3.2:3b` | локальная | 3B |
| `smollm2:135m` | локальная | 135M |

### Требование к серверу IT (CORS)

Браузер блокирует запросы если сервер не разрешает источник `http://195.208.119.67`.

**Необходимая настройка на сервере `178.63.16.109`:**

```bash
# Добавить переменную окружения и перезапустить Ollama
OLLAMA_ORIGINS=http://195.208.119.67
systemctl restart ollama
```

Или в `/etc/systemd/system/ollama.service` в секцию `[Service]`:
```ini
Environment=OLLAMA_ORIGINS=http://195.208.119.67
```

**Без этой настройки AI-ассистент не работает** — браузер получает ошибку `403 Forbidden`.

### Где настроен в коде

| Файл | Переменная | Значение |
|---|---|---|
| `okk/index.html` | `OLLAMA_API` | `http://178.63.16.109:11434/api` |
| `scripts/inject_ai.py` | `_OLLAMA` | `http://178.63.16.109:11434/api` |
| `recruiter/app.py` | `OLLAMA_BASE` | `http://178.63.16.109:11434` |

### AI-панели по дашбордам

| Дашборд | Специализация AI | Как реализовано |
|---|---|---|
| ОКК | Анализ качества КЦ, рекрутеры, критерии | Встроен напрямую в HTML |
| WB | Выработка, штрафы, склады | Через `inject_ai.py` |
| Финансы | P&L, рентабельность, тренды | Через `inject_ai.py` + кнопки 🤖 у каждого графика |
| КП | Помощник по коммерческим предложениям | Через `inject_ai.py` |
| HR-игра | Рекрутинговый ассистент | Через `inject_ai.py` |
| AI-Рекрутер | Скрининг кандидатов из Google Sheets | Streamlit-приложение (`recruiter/app.py`) |

---

## Дашборды (frontend)

Все дашборды — **single-file HTML** со встроенными CSS и JS. Без npm, без сборки. Тёмная тема, шрифт Inter, графики Chart.js.

### Главная
**URL:** `/` | **Файл:** `index.html`
Показывает 7 тайлов с переходами на разделы. Пароль не требует.

### ОКК — Контроль качества
**URL:** `/okk/` | **Источник:** Google Sheets (5 листов)

Разделы: Сводка КЦ → Прослушка → Динамика обучения → МПП → ОРП → Опросы → AI Ассистент

Особенности:
- Рейтинг рекрутеров с радар-графиком по 21 критерию
- Медали на карточках (🥇 лучший месяц, 📈 рост 3 мес, ⭐ >90%, 🎧 50+ звонков)
- Сравнение «было/стало» по периодам

### WB — Аутсорсинг
**URL:** `/wb/`

Разделы: Дашборд → Сотрудники → ФОТ → Аномалии → Карта России

Особенности:
- Автодетектор аномалий (штраф > 3× нормы) + отчёт для Telegram
- SVG-карта ~40 городов России, цвет = коэффициент штраф/выручка

### Финансы
**URL:** `/finance/`

Разделы: KPI → Тренды → Waterfall → По дивизионам → По клиентам/городам

Особенности: кнопка `🤖 Объяснить` рядом с каждым графиком.

### КП — Коммерческое предложение
**URL:** `/kp/`

Конструктор КП с сохранением в БД. Шаблоны и история через `/tasks/api/kp`.

### Задачник IT
**URL:** `/tasks/` | **Backend:** FastAPI на `/tasks/api/`

Разделы: Dashboard → Аналитика → Weekly Review → Задачи → Kanban → Gantt → Roadmap → ТЗ

47 активных задач, сохранение в SQLite, история изменений.

### HR-игра
**URL:** `/hr-game/`
Геймификация для рекрутеров — баллы, рейтинги, ачивки.

### AI-Рекрутер
**URL:** `/recruiter/`
Streamlit-приложение. Читает кандидатов из Google Sheets, проводит скрининг через Ollama.

---

## Backend сервисы

### Tasks API (FastAPI + SQLite)

**Порт:** 8601 | **Файл:** `tasks/api/main.py` | **База:** `tasks/api/tasks.db`

**Схема БД:**

| Таблица | Назначение |
|---|---|
| `tasks` | Задачи (id, title, data JSON, timestamps) |
| `task_history` | Лог изменений (автоматически) |
| `lists` | weekly_plan/fact, roadmap, gantt, tz |
| `kp_history` | Сохранённые КП и шаблоны |

**Endpoints:**

| Метод | Путь | Действие |
|---|---|---|
| GET | `/state` | Всё сразу (tasks + lists) |
| GET/POST | `/tasks` | Список / создать |
| GET/PUT/DELETE | `/tasks/{id}` | Одна задача |
| GET | `/tasks/{id}/history` | История изменений |
| PUT | `/lists/{name}` | Обновить список |
| GET/POST/DELETE | `/kp` | КП и шаблоны |
| GET | `/health` | `{"ok": true}` |

**CORS:** разрешён только с `http://195.208.119.67`.

### AI Recruiter (Streamlit)

**Порт:** 8501 | **Файл:** `recruiter/app.py`

Использует `service_account.json` для Google Sheets и Ollama (`178.63.16.109:11434`) для генерации.

---

## Скрипты-инжекторы

### `inject_auth.py`
```bash
python scripts/inject_auth.py <файл.html>
```
Добавляет оверлей с паролем. Пароль `511028` → `btoa()` = `NTExMDI4`.

### `inject_ai.py`
```bash
python scripts/inject_ai.py <файл.html> <тип>
# Типы: wb, finance, kp, hr-game
```
Добавляет плавающую кнопку 🤖 и чат-панель с подключением к Ollama.

### `setup-server.sh`
Запускается на VPS после каждого деплоя:
1. Создаёт nginx-сниппеты (recruiter, tasks-api)
2. Вставляет `include` в конфиги nginx (умный Python-парсер, без дубликатов)
3. Проверяет внешний Ollama
4. Создаёт/обновляет systemd-сервисы (recruiter, tasks-api)
5. Перезапускает nginx

---

## Источники данных

| Дашборд | Источник | Способ |
|---|---|---|
| ОКК, WB, Финансы | Google Sheets | CSV-export (таблицы открыты по ссылке) |
| Задачник | SQLite на VPS | FastAPI `/tasks/api/state` |
| КП-история | SQLite на VPS | FastAPI `/tasks/api/kp` |
| HR-игра | JSON в HTML | Статически |

Google Sheets загружаются через:
```
https://docs.google.com/spreadsheets/d/{ID}/export?format=csv&gid={GID}
```
Таблицы должны быть **«доступны по ссылке (просмотр)»**.

---

## Безопасность

| Что | Как защищено |
|---|---|
| Дашборды | Пароль `511028` через `btoa()` (защита от случайных пользователей) |
| Tasks API | CORS только с `http://195.208.119.67` |
| service_account.json | `chmod 600` после деплоя |
| tasks.db | Не попадает в git, не удаляется rsync |
| SSH-ключ VPS | Только в GitHub Secrets |

---

## Что делать если…

### …задачник пишет «Офлайн-режим»
1. Открыть http://195.208.119.67/tasks/api/health
2. Если 404 — посмотреть лог последнего GitHub Actions → шаг «Run server setup»
3. Если `{"ok":true}` — очистить кэш браузера (Ctrl+Shift+R)

### …AI-ассистент не загружает модели
1. Открыть http://178.63.16.109:11434/api/tags — работает ли Ollama IT-команды?
2. Если работает но в браузере ошибка — IT нужно добавить `OLLAMA_ORIGINS=http://195.208.119.67`
3. Если не работает — обратиться к IT-команде

### …нужно поменять пароль
```python
import base64; print(base64.b64encode(b'НОВЫЙ_ПАРОЛЬ').decode())
```
Заменить `T='NTExMDI4'` в `scripts/inject_auth.py` → запустить inject_auth.py для всех HTML → push.

### …нужно добавить новый дашборд
1. Создать `<имя>/index.html`
2. Добавить тайл в `index.html`
3. (опц.) `python scripts/inject_auth.py <имя>/index.html`
4. (опц.) `python scripts/inject_ai.py <имя>/index.html <тип>`
5. Push → автодеплой

### …нужно посмотреть логи VPS
```bash
ssh root@195.208.119.67
journalctl -u tasks-api -n 100 --no-pager
journalctl -u recruiter -n 100 --no-pager
tail -50 /var/log/nginx/error.log
```

### …нужно бэкапнуть базу задачника
```bash
scp root@195.208.119.67:/var/www/okk/tasks/api/tasks.db ./backup-$(date +%F).db
```
