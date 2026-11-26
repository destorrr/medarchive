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
