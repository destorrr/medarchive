#!/usr/bin/env bash
# Local disposable I01.2 rehearsal. Never accepts an external database URL.
set -euo pipefail
cd "$(dirname "$0")/.."
for cmd in docker psql python alembic pytest; do
  command -v "$cmd" >/dev/null || { echo "Missing prerequisite: $cmd" >&2; exit 1; }
done
unset MEDARCHIVE_DATABASE_URL MEDARCHIVE_MIGRATION_DATABASE_URL MEDARCHIVE_TEST_PG_URL MEDARCHIVE_TEST_PG_DATABASE MEDARCHIVE_TEST_PG_ENABLED
# Ignore inherited libpq settings and Docker contexts pointing at remote engines.
for var in ${!PG@}; do unset "$var"; done
unset DOCKER_HOST DOCKER_CONTEXT
if [[ "$(docker context inspect --format '{{.Endpoints.docker.Host}}' "$(docker context show)")" != unix://* ]]; then
  echo "A local Unix-socket Docker engine is required" >&2; exit 1
fi
report_dir="$(mktemp -d)"
report="$report_dir/i01-results.xml"
container=""
cleanup() {
  local status=$?
  if [[ -n "$container" ]]; then
    docker stop "$container" >/dev/null || status=1
  fi
  echo "JUnit report directory: $report_dir"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
container="$(docker run --rm -d -p 127.0.0.1::5432 -e POSTGRES_DB=medarchive_i01 -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=ci-admin-only postgres:16)"
port="$(docker port "$container" 5432/tcp)"
[[ "$port" =~ ^127\.0\.0\.1:([0-9]+)$ ]] || { echo "Unexpected port mapping" >&2; exit 1; }
port="${BASH_REMATCH[1]}"
export PGCONNECT_TIMEOUT=5
ADMIN_URL="postgresql://postgres:ci-admin-only@127.0.0.1:$port/medarchive_i01"
ready=0
for ((i=0; i<60; i++)); do
  if psql "$ADMIN_URL" -X -Atc 'SELECT 1' >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 ]] || { echo "PostgreSQL startup timed out" >&2; exit 1; }
psql "$ADMIN_URL" -X -v ON_ERROR_STOP=1 -f db/postgresql/bootstrap-principals.sql
psql "$ADMIN_URL" -X -v ON_ERROR_STOP=1 <<'SQL'
ALTER ROLE medarchive_migrator PASSWORD 'ci-migrator-only';
ALTER ROLE medarchive_runtime PASSWORD 'ci-runtime-only';
GRANT CONNECT ON DATABASE medarchive_i01 TO medarchive_migrator, medarchive_runtime;
ALTER ROLE medarchive_backup PASSWORD 'ci-backup-only';
GRANT USAGE, CREATE ON SCHEMA public TO medarchive_owner;
SQL
psql "$ADMIN_URL" -X -v ON_ERROR_STOP=1 -c 'CREATE DATABASE medarchive_test_i01'
psql "$ADMIN_URL" -X -v ON_ERROR_STOP=1 -c 'GRANT CONNECT ON DATABASE medarchive_test_i01 TO medarchive_runtime, medarchive_migrator, medarchive_backup'
psql "postgresql://postgres:ci-admin-only@127.0.0.1:$port/medarchive_test_i01" -X -v ON_ERROR_STOP=1 <<'SQL'
GRANT USAGE, CREATE ON SCHEMA public TO medarchive_owner;
SET ROLE medarchive_owner;
CREATE TABLE public.i01_fixture_probe(id integer PRIMARY KEY, payload text NOT NULL);
GRANT USAGE ON SCHEMA public TO medarchive_runtime;
GRANT SELECT, INSERT, DELETE ON public.i01_fixture_probe TO medarchive_runtime;
SQL
export MEDARCHIVE_MIGRATION_DATABASE_URL=postgresql+asyncpg://medarchive_migrator:ci-migrator-only@127.0.0.1:$port/medarchive_test_i01
alembic upgrade head
alembic current | grep -q '0011_login_throttles'
psql "postgresql://postgres:ci-admin-only@127.0.0.1:$port/medarchive_test_i01" -X -v ON_ERROR_STOP=1 <<'SQL'
SET ROLE medarchive_owner;
GRANT SELECT ON public.cards, public.shipments, public.domain_events,
  public.branches, public.locations, public.users TO medarchive_runtime;
INSERT INTO public.branches(id, code, name)
  VALUES ('10000000-0000-4000-8000-000000000001', 'I01-SYNTH', 'Synthetic branch');
INSERT INTO public.locations(id, kind, branch_id, name)
  VALUES ('10000000-0000-4000-8000-000000000002', 'branch',
          '10000000-0000-4000-8000-000000000001', 'Synthetic location');
INSERT INTO public.users(id, username, password_hash, full_name, role, branch_id)
  VALUES ('10000000-0000-4000-8000-000000000003', 'i01-synthetic-disabled',
          'invalid-test-hash', 'Synthetic employee', 'branch',
          '10000000-0000-4000-8000-000000000001');
UPDATE public.users SET is_active=false WHERE username='i01-synthetic-disabled';
INSERT INTO public.cards(id, card_number, full_name, date_of_birth, origin_branch_id, current_location_id, created_by)
  VALUES ('10000000-0000-4000-8000-000000000004', 'I01-SYNTH-001', 'Synthetic patient 001',
          DATE '2000-01-01', '10000000-0000-4000-8000-000000000001',
          '10000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000003');
INSERT INTO public.locations(id, kind, name) VALUES
  ('10000000-0000-4000-8000-000000000005', 'central_office', 'Synthetic central office'),
  ('10000000-0000-4000-8000-000000000006', 'archive', 'Synthetic archive');
INSERT INTO public.users(id, username, password_hash, full_name, role, is_active) VALUES
  ('10000000-0000-4000-8000-000000000007', 'i01-synthetic-co', 'invalid-test-hash', 'Synthetic CO employee', 'central_office', false),
  ('10000000-0000-4000-8000-000000000008', 'i01-synthetic-archive', 'invalid-test-hash', 'Synthetic archive employee', 'archive', false),
  ('10000000-0000-4000-8000-000000000009', 'i01-synthetic-admin', 'invalid-test-hash', 'Synthetic admin', 'admin', false);
INSERT INTO public.couriers(id, full_name) VALUES
  ('10000000-0000-4000-8000-000000000010', 'Synthetic courier');
INSERT INTO public.boxes(id, box_number, current_location_id, created_by) VALUES
  ('10000000-0000-4000-8000-000000000011', 'I01-SYNTH-BOX',
   '10000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000003');
GRANT SELECT ON public.couriers, public.boxes TO medarchive_runtime;
INSERT INTO public.box_cycles(id, box_id, cycle_number, started_by) VALUES
  ('10000000-0000-4000-8000-000000000012', '10000000-0000-4000-8000-000000000011', 1,
   '10000000-0000-4000-8000-000000000003');
INSERT INTO public.box_card_memberships(box_cycle_id, card_id, added_by) VALUES
  ('10000000-0000-4000-8000-000000000012', '10000000-0000-4000-8000-000000000004',
   '10000000-0000-4000-8000-000000000003');
INSERT INTO public.shipments(id, from_location_id, to_location_id, courier_id, issued_by) VALUES
  ('10000000-0000-4000-8000-000000000013', '10000000-0000-4000-8000-000000000002',
   '10000000-0000-4000-8000-000000000006', '10000000-0000-4000-8000-000000000010',
   '10000000-0000-4000-8000-000000000003');
INSERT INTO public.shipment_boxes(shipment_id, box_id) VALUES
  ('10000000-0000-4000-8000-000000000013', '10000000-0000-4000-8000-000000000011');
INSERT INTO public.shipment_cards(shipment_id, card_id, box_id) VALUES
  ('10000000-0000-4000-8000-000000000013', '10000000-0000-4000-8000-000000000004',
   '10000000-0000-4000-8000-000000000011');
INSERT INTO public.cards(id, card_number, full_name, date_of_birth, origin_branch_id, current_location_id)
  VALUES ('10000000-0000-4000-8000-000000000014', 'I01-SYNTH-002', 'Synthetic patient 002',
          DATE '2000-01-02', '10000000-0000-4000-8000-000000000001',
          '10000000-0000-4000-8000-000000000006');
INSERT INTO public.archive_requests(card_id, requested_by) VALUES
  ('10000000-0000-4000-8000-000000000014', '10000000-0000-4000-8000-000000000007');
INSERT INTO public.branches(id, code, name) VALUES
  ('10000000-0000-4000-8000-000000000015', 'I01-SYNTH-B', 'Fixture branch B');
INSERT INTO public.locations(id, kind, branch_id, name) VALUES
  ('10000000-0000-4000-8000-000000000016', 'branch', '10000000-0000-4000-8000-000000000015', 'Fixture location B');
INSERT INTO public.users(id, username, password_hash, full_name, role, branch_id, is_active) VALUES
  ('10000000-0000-4000-8000-000000000017', 'i01-synthetic-active-b', 'invalid-test-hash', 'Fixture employee B', 'branch',
   '10000000-0000-4000-8000-000000000015', true);
INSERT INTO public.cards(id, card_number, full_name, date_of_birth, origin_branch_id, current_location_id, created_by)
  VALUES ('10000000-0000-4000-8000-000000000018', 'I01-SYNTH-003', 'Synthetic patient 003',
          DATE '2000-01-03', '10000000-0000-4000-8000-000000000015',
          '10000000-0000-4000-8000-000000000016', '10000000-0000-4000-8000-000000000017');
INSERT INTO public.archive_shelves(section_name, shelf_name, is_active) VALUES
  ('I01-SYNTH', 'A', true), ('I01-SYNTH', 'B', false);
GRANT SELECT ON public.archive_shelves TO medarchive_runtime;
GRANT SELECT ON public.box_cycles, public.box_card_memberships, public.shipment_boxes,
  public.shipment_cards, public.archive_requests TO medarchive_runtime;
SQL

run_tests() (
  set -e
  export MEDARCHIVE_TEST_PG_ENABLED=1
  export MEDARCHIVE_TEST_PG_DATABASE=medarchive_test_i01
  export MEDARCHIVE_TEST_PG_URL=postgresql+asyncpg://medarchive_runtime:ci-runtime-only@127.0.0.1:$port/medarchive_test_i01
  pytest -q tests/test_i01_manifest_checker.py tests/test_i01_fixture_environment.py -o xfail_strict=true
  pytest -q tests/test_i01_pg_fixture_guard.py tests/test_i01_pg_runtime_identity.py -o asyncio_mode=auto -rs --junitxml="$report" -o junit_family=xunit2 -o xfail_strict=true
  python scripts/check-i01-results.py "$report"
  psql "postgresql://postgres:ci-admin-only@127.0.0.1:$port/medarchive_test_i01" -X -v ON_ERROR_STOP=1 -Atc 'SELECT count(*) FROM public.i01_fixture_probe' | grep -qx 0
)
# Capture failure without disabling errexit inside the test subprocess.
set +e
run_tests
test_status=$?
set -e
test_url="postgresql://postgres:ci-admin-only@127.0.0.1:$port/medarchive_test_i01"
psql "$test_url" -X -v ON_ERROR_STOP=1 <<'SQL'
SET statement_timeout = '5s';
DO $check$
BEGIN
  IF current_database() <> 'medarchive_test_i01' OR session_user <> 'postgres' THEN
    RAISE EXCEPTION 'Unexpected fixture verification identity';
  END IF;
  IF EXISTS (SELECT 1 FROM public.i01_fixture_probe) THEN
    RAISE EXCEPTION 'Residual synthetic probe records';
  END IF;
  IF (SELECT count(*) FROM public.cards WHERE card_number IN ('I01-SYNTH-001','I01-SYNTH-002')) <> 2
    OR (SELECT count(*) FROM public.shipments WHERE id='10000000-0000-4000-8000-000000000013') <> 1 THEN
    RAISE EXCEPTION 'Synthetic baseline changed';
  END IF;
  IF (SELECT count(*) FROM public.branches) <> 2
    OR (SELECT count(*) FROM public.locations) <> 4
    OR (SELECT count(*) FROM public.users) <> 5
    OR (SELECT count(*) FROM public.couriers) <> 1
    OR (SELECT count(*) FROM public.boxes) <> 1
    OR (SELECT count(*) FROM public.box_cycles) <> 1
    OR (SELECT count(*) FROM public.box_card_memberships) <> 1
    OR (SELECT count(*) FROM public.shipment_boxes) <> 1
    OR (SELECT count(*) FROM public.shipment_cards) <> 1
    OR (SELECT count(*) FROM public.archive_requests) <> 1
    OR (SELECT count(*) FROM public.cards) <> 3
    OR (SELECT count(*) FROM public.shipments) <> 1
    OR (SELECT count(*) FROM public.archive_shelves) <> 2
    OR (SELECT count(*) FROM public.domain_events) <> 0 THEN
    RAISE EXCEPTION 'Unexpected fixture table counts';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND usename='medarchive_runtime') THEN
    RAISE EXCEPTION 'Residual runtime connections';
  END IF;
END $check$;
SQL
exit "$test_status"
