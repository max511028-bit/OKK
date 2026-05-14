#!/bin/bash
# Ежедневный бэкап SQLite-баз портала.
# Установка на VPS:
#   sudo cp scripts/sth-backup.sh /usr/local/bin/sth-backup.sh
#   sudo chmod +x /usr/local/bin/sth-backup.sh
#   sudo crontab -e
#     0 3 * * * /usr/local/bin/sth-backup.sh >> /var/log/sth-backup.log 2>&1
# Проверка:
#   sudo /usr/local/bin/sth-backup.sh && ls -la /var/backups/sth-portal/
set -euo pipefail

DEST=/var/backups/sth-portal
mkdir -p "$DEST"
DATE=$(date +%Y%m%d-%H%M%S)

TASKS_DB=/var/www/okk/tasks/api/tasks.db
RECRUITER_DB=/var/www/okk/recruiter/recruiter.db

if [ -f "$TASKS_DB" ]; then
    sqlite3 "$TASKS_DB" ".backup '$DEST/tasks-$DATE.db'"
    echo "[$(date)] tasks.db backed up -> $DEST/tasks-$DATE.db"
else
    echo "[$(date)] WARN: $TASKS_DB not found"
fi

if [ -f "$RECRUITER_DB" ]; then
    sqlite3 "$RECRUITER_DB" ".backup '$DEST/recruiter-$DATE.db'"
    echo "[$(date)] recruiter.db backed up -> $DEST/recruiter-$DATE.db"
fi

# Удаляем бэкапы старше 30 дней
find "$DEST" -name '*.db' -mtime +30 -delete
echo "[$(date)] cleanup done; current size: $(du -sh "$DEST" | cut -f1)"
