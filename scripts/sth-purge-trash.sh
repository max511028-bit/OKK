#!/bin/bash
# Ежедневная чистка корзины задач (soft-delete > 7 дней → физическое удаление).
# Используется ИИ-ассистентом Задачника: ИИ удаляет задачи в корзину (deleted_at),
# а этот cron окончательно вычищает старьё. Без него корзина растёт бесконечно.
#
# Установка на VPS:
#   sudo cp scripts/sth-purge-trash.sh /usr/local/bin/sth-purge-trash.sh
#   sudo chmod +x /usr/local/bin/sth-purge-trash.sh
#   sudo crontab -e
#     0 5 * * * /usr/local/bin/sth-purge-trash.sh >> /var/log/sth-purge-trash.log 2>&1
#
# Эндпоинт /admin/tasks/purge-trash доступен localhost'у без токена (см. main.py:418),
# поэтому curl с 127.0.0.1 проходит свободно.
#
# Проверка вручную:
#   sudo /usr/local/bin/sth-purge-trash.sh
#   tail /var/log/sth-purge-trash.log
set -euo pipefail

DAYS="${1:-7}"
API="http://127.0.0.1:8601/admin/tasks/purge-trash?days=${DAYS}"
TS=$(date '+%Y-%m-%d %H:%M:%S')

RESP=$(curl -fsS -X POST "$API" -H 'Content-Type: application/json' 2>&1) || {
    echo "[$TS] ERROR: curl failed: $RESP"
    exit 1
}

# Ответ вида: {"ok":true,"purged":N}
echo "[$TS] OK days=$DAYS resp=$RESP"
