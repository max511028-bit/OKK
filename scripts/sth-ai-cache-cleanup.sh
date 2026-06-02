#!/bin/bash
# Еженедельная чистка кэша ответов ИИ (ai_cache > 7 дней → удалить, потом VACUUM).
# TTL «свежести» в коде 24ч (см. _AI_CACHE_TTL_HOURS), но физически записи висят дольше —
# чтобы можно было считать топ-вопросы. Через неделю удаляем окончательно.
#
# Установка на VPS:
#   sudo cp scripts/sth-ai-cache-cleanup.sh /usr/local/bin/sth-ai-cache-cleanup.sh
#   sudo chmod +x /usr/local/bin/sth-ai-cache-cleanup.sh
#   sudo crontab -e
#     0 4 * * 0 /usr/local/bin/sth-ai-cache-cleanup.sh >> /var/log/sth-ai-cache-cleanup.log 2>&1
set -euo pipefail

DB=/var/www/okk/tasks/api/tasks.db
TS=$(date '+%Y-%m-%d %H:%M:%S')

if [ ! -f "$DB" ]; then
    echo "[$TS] ERROR: DB not found at $DB"
    exit 1
fi

BEFORE=$(sqlite3 "$DB" "SELECT COUNT(*) FROM ai_cache WHERE created_at < datetime('now','-7 day');" 2>/dev/null || echo 0)
sqlite3 "$DB" "DELETE FROM ai_cache WHERE created_at < datetime('now','-7 day');"
sqlite3 "$DB" "VACUUM;"

echo "[$TS] OK removed=$BEFORE older_than=7days"
