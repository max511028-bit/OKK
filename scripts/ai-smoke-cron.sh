#!/bin/bash
# Smoke-тесты ИИ — дёргает локальный API на VPS, результаты сохраняются в ai_test_runs.
# Endpoint /admin/ai-smoke-run без авторизации доступен только с localhost.
#
# Установка на VPS:
#   sudo cp scripts/ai-smoke-cron.sh /usr/local/bin/ai-smoke-cron.sh
#   sudo chmod +x /usr/local/bin/ai-smoke-cron.sh
#   sudo crontab -e
#     # Smoke ИИ 2 раза в день: 09:00 и 21:00 МСК
#     0 9,21 * * * /usr/local/bin/ai-smoke-cron.sh >> /var/log/sth-ai-smoke.log 2>&1
# Проверка вручную:
#   sudo /usr/local/bin/ai-smoke-cron.sh
set -eo pipefail

API_PORT="${API_PORT:-8601}"
TS=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TS] ai-smoke start"
RESP=$(curl -sS --max-time 600 -X POST "http://127.0.0.1:${API_PORT}/admin/ai-smoke-run" || echo '{"error":"curl failed"}')
echo "[$TS] response: $RESP"

# Краткая сводка для лога: ok/total
OK=$(echo "$RESP" | grep -oE '"ok":[0-9]+' | head -1 | grep -oE '[0-9]+' || echo 0)
TOTAL=$(echo "$RESP" | grep -oE '"total":[0-9]+' | head -1 | grep -oE '[0-9]+' || echo 0)
echo "[$TS] summary: $OK / $TOTAL ok"

# exit !=0 если есть упавшие — на будущее, для алертов
if [ "$OK" -lt "$TOTAL" ] || [ "$TOTAL" -eq 0 ]; then
    exit 1
fi
