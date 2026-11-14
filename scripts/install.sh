#!/bin/bash
# МедАрхив — установка systemd-сервисов PostgreSQL
set -euo pipefail

echo "=== Установка МедАрхив ==="

if [ "$(id -u)" -ne 0 ]; then
    echo "ОШИБКА: запустите от root (sudo bash scripts/install.sh)" >&2
    exit 1
fi

if ! id -u medarchive &>/dev/null; then
    useradd -r -s /usr/sbin/nologin medarchive
    echo "Создан системный пользователь medarchive"
fi

# Runtime data and backup files are private to the service account.
install -d -o medarchive -g medarchive -m 0700 /var/lib/medarchive
install -d -o medarchive -g medarchive -m 0700 /var/lib/medarchive/backups

# Secrets are kept outside the repository and are readable only by root
# and the medarchive service group.
install -d -o root -g medarchive -m 0750 /etc/medarchive
ENV_FILE=/etc/medarchive/medarchive.env
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'EOF'
# Set a unique random key. Generate with:
# python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
MEDARCHIVE_SECRET_KEY=
MEDARCHIVE_POSTGRES_ENABLED=true
MEDARCHIVE_AUTH_BACKEND=postgresql
# URL-encode reserved characters in the username/password.
MEDARCHIVE_DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:5432/medarchive
MEDARCHIVE_COOKIE_SECURE=true
EOF
    echo "Создан шаблон конфигурации: $ENV_FILE"
fi
chown root:medarchive "$ENV_FILE"
chmod 0640 "$ENV_FILE"

# Install service units.
cp scripts/medarchive.service /etc/systemd/system/
cp scripts/medarchive-backup.service /etc/systemd/system/
cp scripts/medarchive-backup.timer /etc/systemd/system/
systemctl daemon-reload

# Enable the timer only after a real database URL and secret are configured.
if grep -q '^MEDARCHIVE_SECRET_KEY=$' "$ENV_FILE" || grep -q 'USER:PASSWORD@HOST' "$ENV_FILE"; then
    echo "Таймер бэкапа пока не запущен: сначала заполните $ENV_FILE"
else
    systemctl enable --now medarchive-backup.timer
    echo "Почасовой PostgreSQL backup timer включён"
fi

echo ""
echo "=== Установка завершена ==="
echo ""
echo "Обязательные действия перед запуском:"
echo "1. Отредактируйте $ENV_FILE: задайте MEDARCHIVE_SECRET_KEY и MEDARCHIVE_DATABASE_URL."
echo "2. Установите PostgreSQL client той же основной версии, что и сервер (pg_dump и pg_restore)."
echo "3. Убедитесь, что пользователь medarchive может подключиться к PostgreSQL."
echo "4. Запустите и проверьте резервное копирование:"
echo "   sudo systemctl start medarchive-backup.service"
echo "   sudo systemctl status medarchive-backup.service"
echo "   sudo ls -l /var/lib/medarchive/backups"
echo "5. Запустите приложение: sudo systemctl enable --now medarchive"
echo ""
echo "Перед эксплуатацией проверьте восстановление дампа на отдельной тестовой БД."
echo "Резервный каталог должен находиться на зашифрованном носителе;"
echo "для аварийного восстановления храните дополнительную зашифрованную копию вне сервера."
