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
# Generate an age identity offline: age-keygen -o medarchive-backup.agekey
# Put only its public recipient here; keep the private identity off this server.
MEDARCHIVE_BACKUP_AGE_RECIPIENT=
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

# Enable encrypted backups only after all required settings and age are ready.
if grep -q '^MEDARCHIVE_SECRET_KEY=$' "$ENV_FILE" \
    || ! grep -q '^MEDARCHIVE_DATABASE_URL=postgresql+asyncpg://' "$ENV_FILE" \
    || grep -q 'USER:PASSWORD@HOST' "$ENV_FILE" \
    || ! grep -Eq '^MEDARCHIVE_BACKUP_AGE_RECIPIENT=age1[0-9a-z]+$' "$ENV_FILE"; then
    echo "Таймер бэкапа пока не запущен: задайте секрет, PostgreSQL URL и публичный age recipient в $ENV_FILE"
elif ! command -v age >/dev/null 2>&1; then
    echo "Таймер бэкапа пока не запущен: установите age и повторно выполните установку"
else
    systemctl enable --now medarchive-backup.timer
    echo "Почасовой зашифрованный PostgreSQL backup timer включён"
fi

echo ""
echo "=== Установка завершена ==="
echo ""
echo "Обязательные действия перед запуском:"
echo "1. Отредактируйте $ENV_FILE: задайте MEDARCHIVE_SECRET_KEY и MEDARCHIVE_DATABASE_URL."
echo "2. Установите PostgreSQL client той же основной версии, что и сервер (pg_dump и pg_restore), а также age."
echo "3. Создайте age identity вне сервера; добавьте только публичный recipient в $ENV_FILE."
echo "4. Убедитесь, что пользователь medarchive может подключиться к PostgreSQL."
echo "5. Запустите и проверьте резервное копирование:"
echo "   sudo systemctl start medarchive-backup.service"
echo "   sudo systemctl status medarchive-backup.service"
echo "   sudo ls -l /var/lib/medarchive/backups"
echo "6. Запустите приложение: sudo systemctl enable --now medarchive"
echo ""
echo "Перед эксплуатацией проверьте восстановление дампа на отдельной тестовой БД."
echo "Держите закрытый age identity отдельно от сервера и регулярно проверяйте его доступность."
echo "Для аварийного восстановления храните дополнительную зашифрованную копию вне сервера."
