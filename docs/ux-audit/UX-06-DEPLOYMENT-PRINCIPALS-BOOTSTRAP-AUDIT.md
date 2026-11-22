# UX-06 — read-only аудит deployment principals, bootstrap и supporting DB paths

Дата статического среза: 2026-11-18  
Ветка: `docs/ux-audit-project`  
Базовый HEAD: `1679ee67329a1b40f2974bae14031c7c26bafd3e`  
Тип проверки: статическое чтение config, database adapter, CLI, Alembic, systemd units, installer, backup script, login throttle, Docker Compose и CI workflow.  
**Не выполнялись** подключение к БД, SQL/pg_catalog, запуск миграций, pytest, CI или изменение deployment.

## 1. Итог

DB-SEC-04 (owner NOLOGIN, migrator LOGIN, runtime LOGIN с разными credentials) **не реализован в текущей конфигурации и deployment files**. Текущая схема использует один `MEDARCHIVE_DATABASE_URL` для приложения, Alembic, bootstrap первого admin и PostgreSQL backup. В репозитории не обнаружен отдельный migration runner/service или provisioning этих трёх PostgreSQL principals в Alembic revisions.

Это не отменяет принятую архитектуру: это зафиксированный implementation gap и блокирующая зависимость перед production cutover на защищённые DB-команды.

## 2. Database URL и principals

### 2.1. Приложение

- `src/medarchive/config.py` содержит одно поле `database_url`, читаемое из `MEDARCHIVE_DATABASE_URL`; отдельные runtime/migrator URLs отсутствуют.
- `PostgreSQLDatabase` строит единственный engine/session factory из переданного URL.
- Production systemd unit загружает один `/etc/medarchive/medarchive.env` и запускает web app от системного пользователя `medarchive`.
- Installer генерирует шаблон env file только с `MEDARCHIVE_DATABASE_URL`; права файла — root:medarchive, mode 0640.
- Отдельные PostgreSQL roles `medarchive_owner`, `medarchive_migrator`, `medarchive_runtime` не создаются installer/config/migrations в просмотренных файлах.

### 2.2. Alembic

- `migrations/env.py` читает `MEDARCHIVE_DATABASE_URL` и использует его для migration engine.
- `alembic.ini` содержит fallback URL с development credentials `medarchive:medarchive`.
- Отдельной переменной `MEDARCHIVE_MIGRATOR_DATABASE_URL`, migration service unit или автоматизированного отдельного credential path не найдено.
- В просмотренных Alembic revisions 0001–0011 нет provisioning трёх principals, schema ownership и grants matrix. Revision 0001 содержит только комментарий о будущей append-only защите DomainEvent; это не фактическая настройка ACL.

### 2.3. Bootstrap

- CLI `medarchive-admin create-admin` строит Database из `Config().effective_database_url`, то есть из того же URL, что и runtime.
- Проверка пустой users table и advisory lock защищают от конкурентного создания первого admin, но не отделяют bootstrap privilege от runtime principal.
- Startup приложения вызывает `ensure_system_locations()` через runtime AsyncSession и создаёт singleton locations при необходимости.
- Следовательно, bootstrap системных локаций и первого admin сейчас являются обычными DML-путями с теми же credentials приложения.

### 2.4. Backup/restore

- `medarchive-backup.service` запускается от системного пользователя `medarchive` и загружает тот же EnvironmentFile.
- `scripts/backup.sh` требует `MEDARCHIVE_DATABASE_URL` и извлекает из него PG credentials для `pg_dump`.
- Бэкап шифруется age, временные файлы ограничены правами, есть lock и retention. Это полезные файловые/криптографические меры, но DB principal не отделён от runtime.
- Отдельный backup/restore principal в текущем deployment не задан. Его необходимость и точные права зависят от стратегии PostgreSQL backup; не смешивать его с migrator/runtime.

## 3. Supporting DB paths

### 3.1. Login throttle

`PostgreSQLLoginThrottle` выполняет raw SQL:
- SELECT для проверки блокировки;
- INSERT ... ON CONFLICT DO UPDATE для счётчиков;
- DELETE устаревших bucket rows;
- DELETE identity bucket после успешного входа.

Ключи — HMAC-SHA256 с MEDARCHIVE_SECRET_KEY; raw username/IP не сохраняются. Upsert и advisory locks обеспечивают concurrency semantics на уровне текущего приложения.

Для DB-SEC-02 это отдельный supporting path: нельзя оставлять широкие workflow DML grants ради throttle. До реализации выбрать защищённые SECURITY DEFINER функции либо узкие grants только на `auth_login_throttles`, включая SELECT/INSERT/UPDATE/DELETE, и проверить ограничения, очистку, lock semantics и доступность после privilege cutover.

### 3.2. Bootstrap системных локаций

`ensure_system_locations` выполняет advisory lock, SELECT ... FOR UPDATE и INSERT отсутствующих central_office/archive locations. Сейчас вызывается из startup lifespan web app. При запрете runtime прямого DML этот путь должен быть перенесён в отдельный provisioning/migration/operator step или защищённую bootstrap command. Нужно сохранить идемпотентность и не позволить web workers владеть миграционными полномочиями.

### 3.3. DomainEvent

Текущий код пишет DomainEvent из application services и admin service; read paths используют события для истории и административного журнала. В рамках этого среза не обнаружен отдельный outbox/consumer deployment contract, но полный поиск всех возможных consumers/background jobs не считается завершённым. Не делать вывод, что event delivery/retention уже обеспечены только наличием таблицы DomainEvent.

## 4. Docker Compose и CI — только development/test baseline

- `docker-compose.yml` поднимает PostgreSQL 16 с простыми локальными POSTGRES_USER/POSTGRES_PASSWORD.
- `.github/workflows/postgresql.yml` также использует тестовые credentials `medarchive:medarchive`, применяет Alembic и выполняет schema/backup/restore checks.
- Эти значения пригодны как disposable CI/dev service credentials и не подтверждают production role separation.
- CI не проверяет подключение от owner/migrator/runtime с разными grants, runtime permission denied на прямой DML, SET ROLE запреты или доступность миграций без runtime credentials.

## 5. Разрывы относительно DB-SEC-01…04

| Решение | Текущее состояние по статическому коду | Вывод |
|---|---|---|
| DB-SEC-01 application-authenticated actor | Application resolves active user; workflows передают actor UUID | Частично соответствует baseline; независимого DB identity broker не заявлено |
| DB-SEC-02 protected DB commands / no runtime direct DML after cutover | ORM/DML и raw SQL остаются в runtime services; DB commands не обнаружены как реализованный слой | Не реализовано; supporting paths должны быть включены в inventory |
| DB-SEC-03 DomainEvent | Общая таблица/ORM и typed event names в приложении | Таблица и записи есть; append-only DB ACL/trigger enforcement и полный consumer contract этим аудитом не подтверждены |
| DB-SEC-04 separate principals | Один URL в config, app, Alembic, bootstrap и backup | Не реализовано; blocker для privilege cutover |

## 6. Требования к будущему deployment design

До изменения конфигурации подготовить отдельный документ/план с точными контрактами:

1. Раздельные credentials и URL: runtime, migrator; owner — NOLOGIN и используется только через контролируемый migration ownership context.
2. Alembic запускается отдельной операторской/migration job, не web worker; web process не получает migrator secret.
3. Runtime получает только минимальные CONNECT/USAGE/SELECT и EXECUTE на опубликованные DB commands/read contracts; прямой DML на переведённые aggregates запрещён.
4. First-admin bootstrap и system locations provisioning — отдельный контролируемый путь, не runtime startup DML.
5. Login throttle — отдельный узкий permission/function contract.
6. Backup/restore principal и права определяются отдельно; backup не должен наследовать миграционные полномочия или неограниченный owner доступ.
7. Installer/systemd должны разделять секреты по процессам и пользователям; проверить file permissions, environment exposure, logging, rotation и rollback.
8. CI добавить отдельные реальные роли и негативные проверки: runtime не может SET ROLE owner/migrator, выполнять DDL/TRUNCATE/direct DML, но может выполнять разрешённые commands; migrator может выполнить upgrade; owner NOLOGIN.
9. Для каждого rollout шага определить временные legacy grants, срок/условие revoke, совместимость версий, backup/restore gate и процедуру восстановления.
10. Не вводить роли/GRANT/REVOKE и не переписывать конфигурацию до отдельного разрешения на реализацию.

## 7. Ограничения

- Проверка статическая; фактические database roles, grants, ownership, default privileges, RLS и secrets не читались.
- Не проверялись реальные systemd host values, содержимое production EnvironmentFile, system users, network ACL или права на backup storage.
- Не выполнялись PostgreSQL/SQL/pg_catalog, Alembic, pytest или CI.
- Исходники, конфигурация, migrations, workflows и deployment не изменялись.
- Документ фиксирует расхождения и проектные требования, но не является реализацией DB-SEC-01…04 и не разрешает cutover.


## 2026-11-22 — refreshed repository-wide principals/grants scan

Repeated a static scan of the current branch across the PostgreSQL initial SQL, Alembic env, every revision 0001–0011 present in the repository, Docker Compose and PostgreSQL CI workflow.

- No executable CREATE ROLE/USER, ALTER ROLE, GRANT, REVOKE, ALTER DEFAULT PRIVILEGES, SET ROLE or SECURITY DEFINER provisioning was found in the inspected Alembic revisions or deployment files.
- The only matching GRANT/REVOKE-related SQL in the initial schema/revision 0001 is a comment describing a future append-only intention; it is not an executed ACL statement.
- Alembic CI runs the upgrade to head, but that does not establish separated owner/migrator/runtime principals.
- Existing deployment audit remains valid: application, Alembic, bootstrap and backup paths are wired to the same MEDARCHIVE_DATABASE_URL in the inspected configuration; no separate migrator URL/runner or distinct backup principal is defined in the inspected files.
- Product decision DB-SEC-04 already approves the target principal split (owner NOLOGIN, migrator LOGIN, runtime LOGIN); this is an implementation gap, not an unresolved product decision.

No actual database was queried. Therefore current role membership, owners, effective privileges, PUBLIC/default grants, inherited grants, RLS, role attributes, secret distribution and deployed systemd/cron jobs remain unknown. Do not infer them from repository files. Read-only DBA preflight is required before any DDL, ACL change or event/ledger cutover.
