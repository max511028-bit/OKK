# Sales (CRM Dashboard) — интегрирован в STH-портал

Дашборд продаж: импорт лидов и сделок из Битрикс24 (CSV) + аналитика воронки.

## Архитектура

- **Фронт:** React 19 + TypeScript + Vite + Dexie (IndexedDB) + Recharts.
- **Бэк:** общий FastAPI (`tasks/api/main.py`) — таблица `dash_blobs` хранит коллекции по
  ключам `sales/imports`, `sales/leads`, `sales/deals`, `sales/snapshots`, `sales/changeLog`,
  `sales/stageMapping`, `sales/entityLinks`.
- **Синхронизация:** `src/serverSync.ts` — на старте `pullFromServer()` подтягивает все
  коллекции в Dexie, на любое изменение через Dexie-hooks срабатывает debounced
  `pushAllToServer()` (через 800 мс).
- **Источник правды:** сервер (SQLite). Dexie — горячий кэш.

## Локальная сборка

```bash
cd sales
npm install
npm run build      # → sales/dist/
python ../scripts/inject_auth.py inject dist/index.html
python ../scripts/inject_ai.py dist/index.html sales
python ../scripts/inject_mobile.py dist/index.html
```

## Деплой на VPS

```bash
tar czf /tmp/sales.tar.gz sales/dist
scp /tmp/sales.tar.gz root@portalsth.ru:/tmp/
ssh root@portalsth.ru "cd /var/www/okk && \
  tar xzf /tmp/sales.tar.gz && \
  rm -rf sales/assets sales/data sales/index.html && \
  mv sales/dist/* sales/ && rmdir sales/dist"
```

## Перезапись данных от пользователя

```bash
# При получении свежих JSON-выгрузок коллекций
curl -X PUT -H "Content-Type: application/json" -H "X-Auth-Token: $TOKEN" \
     --data @leads.json \
     https://portalsth.ru/tasks/api/sales/leads
```
