#!/usr/bin/env bash
# PostgreSQL backup for МедАрхив. Run by the systemd timer.
set -Eeuo pipefail

: "${MEDARCHIVE_DATABASE_URL:?Set MEDARCHIVE_DATABASE_URL in /etc/medarchive/medarchive.env}"

BACKUP_DIR="${MEDARCHIVE_BACKUP_DIR:-/var/lib/medarchive/backups}"
RETENTION="${MEDARCHIVE_BACKUP_RETENTION:-24}"

if [[ ! "$RETENTION" =~ ^[1-9][0-9]*$ ]]; then
    echo "ОШИБКА: MEDARCHIVE_BACKUP_RETENTION должно быть положительным целым числом" >&2
    exit 2
fi

for command in pg_dump pg_restore python3 flock; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "ОШИБКА: не найдена обязательная команда: $command" >&2
        exit 127
    fi
done

install -d -m 0700 "$BACKUP_DIR"
exec 9>"$BACKUP_DIR/.backup.lock"
if ! flock -n 9; then
    echo "Резервное копирование уже выполняется; второй запуск пропущен"
    exit 0
fi

PGPASS_FILE="$(mktemp "$BACKUP_DIR/.pgpass.XXXXXX")"
export PGPASS_FILE
TEMP_FILE=""
cleanup() {
    rm -f -- "$PGPASS_FILE"
    if [[ -n "$TEMP_FILE" ]]; then
        rm -f -- "$TEMP_FILE"
    fi
}
trap cleanup EXIT

# Convert the application's SQLAlchemy asyncpg URL to libpq environment
# variables. Credentials are written to a mode-0600 temporary pgpass file;
# the URL/password is never passed as a command-line argument to pg_dump.
eval "$(python3 - <<'PY'
import os
import shlex
from urllib.parse import parse_qs, unquote, urlsplit

raw = os.environ["MEDARCHIVE_DATABASE_URL"]
url = urlsplit(raw)
if url.scheme != "postgresql+asyncpg":
    raise SystemExit("MEDARCHIVE_DATABASE_URL must use postgresql+asyncpg://")
if not url.hostname or not url.path.strip("/"):
    raise SystemExit("MEDARCHIVE_DATABASE_URL must include a host and database name")
if url.fragment:
    raise SystemExit("URL fragments are not supported in MEDARCHIVE_DATABASE_URL")

query = parse_qs(url.query, keep_blank_values=True)
unknown = set(query) - {"ssl", "sslmode"}
if unknown:
    raise SystemExit("Unsupported PostgreSQL URL query parameter(s): " + ", ".join(sorted(unknown)))

sslmode = (query.get("sslmode") or query.get("ssl") or [""])[0]
if sslmode and sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
    raise SystemExit("Unsupported PostgreSQL SSL mode in MEDARCHIVE_DATABASE_URL")

def pgpass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")

host = url.hostname
port = str(url.port or 5432)
database = unquote(url.path.lstrip("/"))
username = unquote(url.username or "")
password = unquote(url.password or "")
passfile = os.environ["PGPASS_FILE"]

with open(passfile, "w", encoding="utf-8") as stream:
    stream.write(":".join(pgpass_escape(item) for item in (host, port, database, username, password)) + "\\n")
os.chmod(passfile, 0o600)

values = {
    "PGHOST": host,
    "PGPORT": port,
    "PGDATABASE": database,
    "PGUSER": username,
    "PGPASSFILE": passfile,
}
if sslmode:
    values["PGSSLMODE"] = sslmode
for key, value in values.items():
    print(f"export {key}={shlex.quote(value)}")
PY
)"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="$BACKUP_DIR/medarchive-$TIMESTAMP.dump"
TEMP_FILE="$(mktemp "$BACKUP_DIR/.medarchive-backup.XXXXXX")"

pg_dump --format=custom --no-owner --no-acl --file="$TEMP_FILE"
# A successful pg_dump exit is not enough: ensure the archive can be read.
pg_restore --list "$TEMP_FILE" >/dev/null
mv -- "$TEMP_FILE" "$BACKUP_FILE"
TEMP_FILE=""
chmod 0600 "$BACKUP_FILE"

# Keep the newest N completed archives. Temporary and lock files are excluded.
python3 - "$BACKUP_DIR" "$RETENTION" <<'PY'
import sys
from pathlib import Path

directory = Path(sys.argv[1])
retention = int(sys.argv[2])
backups = sorted(
    directory.glob("medarchive-*.dump"),
    key=lambda item: item.stat().st_mtime,
    reverse=True,
)
for stale in backups[retention:]:
    stale.unlink()
print(f"Ротация: оставлено {min(len(backups), retention)} копий")
PY

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] PostgreSQL-бэкап создан: $BACKUP_FILE"
