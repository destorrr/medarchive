#!/usr/bin/env bash
# Run PostgreSQL 16 client tools in a container for version-matched CI checks.
set -Eeuo pipefail

tool="$(basename "$0")"
case "$tool" in
    pg_dump|pg_restore) ;;
    *)
        echo "Unsupported PostgreSQL client wrapper: $tool" >&2
        exit 2
        ;;
esac

docker_args=(--rm --network host --user "$(id -u):$(id -g)")
for name in PGHOST PGPORT PGDATABASE PGUSER PGSSLMODE PGPASSWORD; do
    if [[ -n "${!name:-}" ]]; then
        docker_args+=(--env "$name")
    fi
done

# The backup script creates a private pgpass file for pg_dump and removes it
# on exit. During the later restore check, PGPASSWORD is used instead.
if [[ -n "${PGPASSFILE:-}" && -f "$PGPASSFILE" ]]; then
    docker_args+=(--volume "$PGPASSFILE:/tmp/medarchive.pgpass:ro")
    docker_args+=(--env PGPASSFILE=/tmp/medarchive.pgpass)
fi
if [[ -n "${MEDARCHIVE_BACKUP_DIR:-}" ]]; then
    docker_args+=(--volume "$MEDARCHIVE_BACKUP_DIR:$MEDARCHIVE_BACKUP_DIR")
fi

exec docker run "${docker_args[@]}" postgres:16 "$tool" "$@"
