#!/bin/bash
# МедАрхив — установка systemd-сервиса
set -euo pipefail

echo "=== Установка МедАрхив ==="

# Проверка прав
if [ "$(id -u)" -ne 0 ]; then
    echo "ОШИБКА: запустите от root (sudo bash install.sh)" >&2
    exit 1
fi

# Создать пользователя
if ! id -u medarchive &>/dev/null; then
    useradd -r -s /bin/false medarchive
    echo "Создан пользователь medarchive"
fi

# Директории
mkdir -p /var/lib/medarchive/backups
chown -R medarchive:medarchive /var/lib/medarchive
echo "Созданы директории /var/lib/medarchive/"

# Копировать юниты
cp scripts/medarchive.service /etc/systemd/system/
cp scripts/medarchive-backup.service /etc/systemd/system/
cp scripts/medarchive-backup.timer /etc/systemd/system/
echo "Юниты скопированы в /etc/systemd/system/"

# Перечитать конфигурацию
systemctl daemon-reload

# Включить и запустить таймер бэкапа
systemctl enable medarchive-backup.timer
systemctl start medarchive-backup.timer
echo "Таймер бэкапа включён (ежечасно)"

echo ""
echo "=== Установка завершена ==="
echo ""
echo "Дальнейшие шаги:"
echo "1. Отредактируйте MEDARCHIVE_SECRET_KEY в /etc/systemd/system/medarchive.service"
echo "   Сгенерируйте: python3 -c 'import secrets; print(secrets.token_urlsafe(32))'"
echo "2. Запустите сервис: sudo systemctl start medarchive"
echo "3. Включите автозапуск: sudo systemctl enable medarchive"
echo "4. Проверьте: curl http://localhost:8000/"
