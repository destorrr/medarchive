#!/bin/bash
# МедАрхив — скрипт бэкапа БД
# Вызывается systemd timer'ом ежечасно
# Хранит 24 последних копии

set -euo pipefail

DB_PATH="${MEDARCHIVE_DB_PATH:-/var/lib/medarchive/medarchive.db}"
BACKUP_DIR="/var/lib/medarchive/backups"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%S)
BACKUP_FILE="${BACKUP_DIR}/medarchive-${TIMESTAMP}.db"

mkdir -p "$BACKUP_DIR"

# Копировать БД
if [ -f "$DB_PATH" ]; then
    cp "$DB_PATH" "$BACKUP_FILE"
    echo "[$(date)] Бэкап создан: $BACKUP_FILE"

    # Ротация: оставить 24 последних
    ls -1t "${BACKUP_DIR}"/medarchive-*.db | tail -n +25 | xargs -r rm
    echo "[$(date)] Ротация: $(ls "${BACKUP_DIR}"/medarchive-*.db | wc -l) файлов"
else
    echo "[$(date)] ОШИБКА: БД не найдена: $DB_PATH" >&2
    exit 1
fi
