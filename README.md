# STH Аналитический портал

Единая точка входа в аналитические инструменты компании: дашборды, AI-ассистенты, рекрутер, задачник.

**Live:** http://195.208.119.67/

## Что есть

| Дашборд | Что делает | AI |
|---|---|---|
| ОКК | Контроль качества колл-центра | ✅ |
| WB | Аутсорсинг Wildberries | ✅ |
| Финансы | P&L, движение денег, проекты | ✅ |
| КП | Калькулятор коммерческих предложений | ✅ |
| HR | AI-консультант по найму | ✅ |
| Задачник | Kanban / Gantt / Roadmap | ❌ |
| Рекрутер | Отработка возражений кандидатов | ✅ (основная функция) |

## Стек

- **Frontend:** vanilla HTML + Chart.js (single-page per dashboard)
- **Backend:** FastAPI + SQLite на VPS Ubuntu
- **AI:** Ollama (`qwen3:8b`) на локальном компе + ngrok-туннель → FastAPI-прокси
- **Auth:** HMAC-токены, серверная проверка (пароли в env)

## Запуск AI

```bash
# На локальном компе
двойной клик по start-ai.bat
# окно cmd.exe должно остаться открытым
```

## Документация

См. [DOCS.md](./DOCS.md) — полная техдока с архитектурой, troubleshooting, roadmap.

## Деплой

```bash
scp -i ~/.ssh/okk_key_openssh path/file root@195.208.119.67:/var/www/okk/path/file
ssh -i ~/.ssh/okk_key_openssh root@195.208.119.67 "systemctl restart tasks-api"  # если меняли бэкенд
```
