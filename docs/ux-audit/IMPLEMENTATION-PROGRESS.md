# UX-10 — журнал реализации

Разрешение владельца проекта получено в чате: «даю разрешение». Ветка feature/ux-implementation создана от 86bfa30971815e078484694c8cd0f554e37de4d9. Ветка проектирования сохранена. Разрешение на production не предоставлено.

## Пакеты

| Пакет | Статус |
|---|---|
| I00 | In progress |
| I01–I11 | Planned |

## I00 — исходный baseline

- Проверены pyproject.toml, docker-compose.yml, alembic.ini, tests/conftest.py и .github/workflows/postgresql.yml. Python >=3.11, CI 3.12, PostgreSQL 16, legacy SQLite fixtures. Compose использует постоянный том; не запускать против неизвестной базы. Зависимости преимущественно с нижней границей.
- Коммит 16c4cd486e5dd1f4d000b6d0566a71be48e14bf1: существующий baseline workflow включён для push и PR реализации, workflow_dispatch, contents:read. Существующие проверки сохранены. Это не целевой J01–J10 и не доказательство успешного CI.
- DB1: фактические PostgreSQL revision/catalog/ACL/counts/conflicts/bootstrap parity не проверены. Изолированное окружение, версии зависимостей, результаты pytest и CI, migration head и восстановление пока не подтверждены. Не использовать реальные медицинские данные. Не выполнять destructive reset.
- Следующее: получить результат baseline CI, классифицировать failures/skips, проверить отдельное PG16 окружение и read-only DB1; затем I01 только после критериев I00. Не считать пакет Verified без доказательств.

Исходники приложения, миграции, тесты и утверждённые HTML не менялись. Изменение CI trigger и permissions — инженерное решение по UX-09.6C.

## I00.2 — исходный CI и инвентаризация

Runs 45823859419 and 45823882661 completed success. Job 139922888682 logs: PostgreSQL 16.16, Alembic 0011_login_throttles (head), pytest 488 passed, 36 warnings, 157.15s; no skips reported in summary. Chromium installation, core tables, shell syntax and existing encrypted backup/restore check succeeded. Restore check is limited to existing two-table assertion; not target DB7/DB8 acceptance. Existing SQLite tests and superuser connection remain baseline only.

Commit f1cbb166c52d0bf8c9beeec08ed4eb4b180b9b6b adds read-only catalog inventory and dependency versions to isolated CI: role flags, owners, ACL, constraints, triggers, estimated row counts, migration revision, pip check/freeze. Results pending. Estimated rows are not exact counts; conflict reconciliation, bootstrap parity, effective runtime ACL and reproducible dependency lock remain open. No external PG target inspected. I00 remains In progress; I01 not started. No application, migration, test or prototype changes.

## I00.3 — inventory and dependency baseline

Runs 45825482358 and 45825499654 success; job 139928474645 inventory succeeded. Python 3.12.16, PG16, revision 0011_login_throttles, pip check OK, 488 passed / 36 warnings (165.07s). Current CI role medarchive has superuser/createdb/createrole/login flags. All 36 pytest warnings are httpx per-request cookies deprecations (archive 3, packing 1, transfer 32); separate pytest-asyncio unset loop scope and Node punycode warnings. Do not suppress; address with fixture/auth work. Captured 44 exact package versions in constraints/ci-python312.txt from actual freeze, CI now consumes constraints and Python 3.12.16. Verification pending; no hash lock, runner/image/build tools not fully pinned. DB1 exact counts/conflicts/bootstrap parity and effective runtime ACL still open; no external database accessed. I00 remains in progress. No application or migration changes.

## I00.4 — constrained baseline verified

Runs 45849104189, 45849123641, 45849131538 success. Latest logs confirm constraints used, Python 3.12.16, pip check OK, revision 0011_login_throttles head, 488 passed / 36 warnings. Seven successful runs so far. Read actual DB1 gate 5H, bootstrap service/test, migrations/env.py and initial SQL. Initial SQL has 15 CREATE TABLE statements and no CREATE FUNCTION/TRIGGER statements; it is not equivalent to head. Application singleton bootstrap uses advisory transaction lock and row lock, existing idempotency test passed in baseline. Added read-only function/trigger definitions and exact table counts to disposable CI inventory; new run pending. No external database access, no data reset. DB1 bootstrap SQL/Alembic detailed reconciliation and retained-data conflict inventory remain open. I00 not closed; I01 not started.

## I00.5 — DB1 clean baseline reconciliation

Runs 45851708162 and 45851713974 success; job 140019485032: 488 passed, 36 warnings, 211.21s. Exact counts: 17 application tables empty, alembic_version one row. Function and trigger definitions captured in CI logs. Compared initial SQL with frozen SCHEMA_SQL in 0001: identical after trimming surrounding whitespace (13599 characters). README already prescribes Alembic. Historical SQL draft now explicitly marked non-installable in bcf7a60d03dcf83ae5e3183d4317e2bc780d55c9; SQL statements unchanged. Historical migration unchanged. Clean DB1 baseline evidence collected; external/retained data conflicts not verified, no production access. Effective target runtime ACL remains I01 evidence, not established by superuser baseline. I00 clean-environment work complete subject to verification of documentation-only change; do not mark external DB1 or release gate passed. I01 implementation may proceed in isolated environment only, with affected retained-data migrations blocked pending preflight. No application code, tests or migration changes.

## I01.1 — principal provisioning started

47cd1aaec4da72cda91103d25a56fb9debc5216b adds transactional bootstrap outside Alembic, four technical principals, fail-closed attributes/membership validation, controlled migrator SET ROLE, public schema CREATE revoke. CI executes twice and checks effective role membership and runtime DDL denial via session authorization. CI pending. No passwords, production execution, ownership transfer, runtime DML grants/revocation, backup read grants or migration URL integration yet. Existing application tests still use baseline privileged connection. I01 in progress; I00 clean baseline complete, external DB1 gate remains. No business roles or prototypes changed.
