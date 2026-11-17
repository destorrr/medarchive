# Техническая инвентаризация SQLite — предварительный реестр

Дата проверки: 2026-11-17  
Ветка: `docs/ux-audit-project`  
Целевое ограничение проекта: PostgreSQL — единственная поддерживаемая СУБД.  
Статус: **инвентаризация, не план удаления и не разрешение на миграционные изменения**.

## 1. Подтверждённые точки зависимости

| Область | Файл/место | Наблюдение |
|---|---|---|
| Конфигурация | `src/medarchive/config.py` | `MEDARCHIVE_AUTH_BACKEND` по умолчанию равен `sqlite`; допустимы `sqlite` и `postgresql`. PostgreSQL требует `MEDARCHIVE_POSTGRES_ENABLED=true`. |
| Запуск приложения | `src/medarchive/main.py` | Всегда подключается auth-router; при `auth_backend=postgresql` подключаются PostgreSQL routers, иначе — legacy SQLite routers. |
| Startup | `src/medarchive/main.py` | SQLite база инициализируется только при `auth_backend == "sqlite"`; PostgreSQL проходит health check и bootstrap системных локаций при включённом PG. |
| Dependency wiring | `src/medarchive/dependencies.py` | Импортируются SQLite Database и legacy AuthService; `init_dependencies` создаёт SQLite Database независимо от backend, PostgreSQL Database — при включённом PG. |
| Legacy principal | `src/medarchive/dependencies.py` | `get_current_user` читает SQLite users/branches через SQL с плейсхолдерами `?`; используется старым набором роутеров. |
| PostgreSQL principal | `src/medarchive/dependencies.py` | `get_pg_current_user` использует PostgreSQL UserRepository/AsyncSession; PG role guards построены отдельно. |
| Адаптер | `src/medarchive/adapters/sqlite/` | В дереве присутствуют `database.py`, `migrations.py`, `repositories.py` и пакетный `__init__.py`. |
| Зависимости | `pyproject.toml` | `aiosqlite` остаётся обязательной зависимостью; комментарий называет его временным до удаления старых SQLite-модулей. |
| Тесты | `tests/adapters/sqlite/` | Существуют отдельные тесты SQLite database и repositories. |
| CLI/скрипты | `src/medarchive/cli.py`, `scripts/*` | Требуется отдельная проверка импорта/использования SQLite в CLI и обслуживающих скриптах. |

## 2. Важное различие

Текущая конфигурация позволяет запускать две разные поверхности приложения:

- PostgreSQL: PostgreSQL-аутентификация и PostgreSQL-роутеры.
- SQLite: legacy-аутентификация и legacy-роутеры.

Это не эквивалентно полной независимости приложения от SQLite: SQLite-классы импортируются в общем dependency-модуле, объект SQLite Database создаётся при инициализации зависимостей даже для PG-конфигурации, а legacy auth service создаётся всегда. При этом startup не вызывает `get_db().init()` в PostgreSQL-режиме.

Следовательно, перед удалением SQLite нужно проверить не только runtime-поверхность PostgreSQL, но и импортную связность, CLI, тестовую инфраструктуру, backup/restore, скрипты и документацию.

## 3. Предварительные категории работ

1. **Runtime:** убрать выбор backend и legacy router branch только после подтверждения, что PG-поверхность покрывает весь нужный функционал.
2. **Аутентификация:** проверить `auth.py` и все места использования legacy `AuthService`; сохранить корректную PG-сессию, отзыв учётных данных и cookie-политику.
3. **Dependency wiring:** исключить безусловные импорты и создание SQLite Database из PG-запуска.
4. **Адаптеры и репозитории:** установить, какие SQLite-модули используются только legacy routers/services, какие импортируются общим кодом.
5. **CLI и эксплуатация:** проверить команды, backup/restore, install/systemd и скрипты обслуживания.
6. **Тесты:** определить legacy-only тесты, общие тесты и PostgreSQL интеграционные тесты; проверить CI matrix.
7. **Упаковка:** удалить `aiosqlite` и SQLite-only зависимости только после удаления последнего реального импорта/пути.
8. **Данные:** отдельно определить, требуется ли перенос существующих эксплуатационных данных из SQLite в PostgreSQL, кто отвечает за сверку и критерии завершения.

## 4. Не проверено

- Полный поиск всех SQL-запросов, SQLite DSN/путей, `sqlite3`/`aiosqlite` imports и legacy services.
- Полная инвентаризация CLI, backup/restore, install scripts и systemd units.
- Состав GitHub Actions jobs и реальное выполнение PG-only тестов.
- Возможность полностью удалить SQLite без потери пользовательских сценариев.
- Наличие действующих production SQLite баз и необходимость миграции данных.

## 5. Ограничение

До отдельного согласования не удалять SQLite адаптер, зависимости, legacy routers, тесты или конфигурационные переключатели. Текущая задача — только установить полный граф зависимостей и сформировать безопасную последовательность отказа.


## 6. Проверка legacy backup UI и связи с PostgreSQL

Дополнительная проверка `routers/admin.py`, `routers/postgresql_admin_pages.py`, `auth.py`, `dependencies.py`, `main.py` и шаблона `admin/backup.html` уточнила картину:

- `routers/admin.py` импортирует SQLite Database, SQLite repositories, legacy AuthService и BackupService. Все маршруты `/admin/backup`, `/admin/backup/create`, `/admin/backup/restore` находятся в этом legacy admin router.
- `main.py` подключает legacy `admin_router` только в ветке SQLite. В PostgreSQL-режиме подключается `postgresql_admin_pages_router`, где маршрутов backup/restore нет.
- Следовательно, Python BackupService и HTML backup/restore UI — часть legacy SQLite поверхности, а не PostgreSQL admin UI. Их нельзя переносить как есть в PostgreSQL-only архитектуру.
- PostgreSQL backup выполняется внешним эксплуатационным механизмом: `scripts/backup.sh` запускает `pg_dump --format=custom`, проверяет архив через `pg_restore --list`, шифрует через `age`, применяет retention; `install.sh` настраивает systemd service/timer.
- Legacy SQLite restore создаёт копию текущего файла перед восстановлением и сбрасывает WAL; эти действия относятся к SQLite и не являются планом восстановления PostgreSQL.
- `auth.py` сохраняет общий login/logout router: в нём есть SQLite-типы/legacy AuthService, а при PostgreSQL backend выполняется отдельная ветка через PostgreSQLAuthService и UserRepository. Это остаточная import-time связность, даже если PG login использует PostgreSQL.
- `dependencies.py` безусловно создаёт SQLiteDatabase и AuthService; PG Database создаётся отдельно, если включён PostgreSQL.

### Вывод

SQLite dependency graph остаётся активным в legacy режиме и частично импортируется в общем PG runtime. Для отказа от SQLite необходимо отдельно решить судьбу legacy admin router, auth abstractions, dependency wiring, SQLite adapters/repositories, legacy templates и тестов. PostgreSQL backup/restore должен оставаться отдельным эксплуатационным runbook/процессом; отсутствие backup UI в PostgreSQL admin UI — зафиксированное текущее состояние, а не автоматически дефект.


## 7. Расширенная инвентаризация runtime, CLI, scripts, tests и CI (2026-11-17)

Проверены main.py, config.py, dependencies.py, routers/auth.py, все legacy routers, legacy services, SQLite adapters, cli.py, обслуживающие scripts, Alembic configuration, pytest fixtures и .github/workflows/postgresql.yml на ветке аудита.

### 7.1. Конфигурация и runtime

- Config по-прежнему выбирает auth_backend="sqlite" по умолчанию; допустимы два backend. Для PostgreSQL одновременно требуется MEDARCHIVE_POSTGRES_ENABLED=true.
- Production install script записывает MEDARCHIVE_AUTH_BACKEND=postgresql и MEDARCHIVE_POSTGRES_ENABLED=true; это подтверждает целевую конфигурацию установки, но не меняет default в коде.
- main.py подключает PostgreSQL routers при auth_backend == "postgresql", иначе подключает legacy SQLite routers. Общий auth router подключается в обоих режимах.
- dependencies.py безусловно импортирует SQLite Database и legacy AuthService; init_dependencies создаёт SQLite Database даже при PostgreSQL-конфигурации. get_current_user и get_db — legacy SQLite путь.
- PostgreSQL login использует PostgreSQL repository/service, но получает AuthService через Depends(get_auth). AuthService не является только SQLite-компонентом: его методы хеширования паролей и подписания сессионных токенов используются PostgreSQL-путём и CLI. При отказе от SQLite нужно отделить общую credential/session-функциональность от legacy principal/domain imports.
- get_pg_current_user использует PostgreSQL repository, но также проверяет токен через общий AuthService. Эту зависимость нужно сохранить или заменить согласованно.

### 7.2. Legacy routers и services

- routers/admin.py, archive.py, dashboard.py, packing.py, transfer.py импортируют SQLite Database/repositories и/или legacy role/principal dependencies. Они подключаются только в SQLite-ветке main.py.
- services/box_service.py, movement_service.py, receive_service.py, request_service.py, return_service.py, search_service.py, shipment_service.py импортируют SQLite Database и/или SQLite repositories. Это legacy сервисный контур, не используемый PostgreSQL routers по текущей структуре подключения.
- services/backup_service.py — SQLite file backup/restore, вызывается legacy admin router; PostgreSQL backup обслуживается отдельным shell/systemd контуром.
- adapters/sqlite/ содержит database, migrations и repositories. Пакет aiosqlite указан обязательной зависимостью в pyproject.toml.

### 7.3. CLI и обслуживающие scripts

- src/medarchive/cli.py создаёт первого администратора в PostgreSQL через SQLAlchemy/PostgreSQL repository; SQLite Database и SQLite SQL в CLI не обнаружены. Однако CLI импортирует общий AuthService для хеширования пароля, поэтому credential helper должен остаться доступным после удаления SQLite.
- scripts/backup.sh, install.sh, medarchive.service, medarchive-backup.service, medarchive-backup.timer, pg16-client-wrapper.sh относятся к PostgreSQL эксплуатации; backup создаётся pg_dump, проверяется pg_restore, шифруется age и ротируется.
- scripts/cleanup_stale_box_cards.py — отдельный SQLite-only скрипт: использует stdlib sqlite3, путь MEDARCHIVE_DB_PATH, SQLite backup и UPDATE по SQLite базе. Его нельзя считать частью PostgreSQL runtime или запускать против целевой PostgreSQL базы. Судьбу скрипта нужно решить отдельно (архивировать/удалить/заменить PG-инструментом).

### 7.4. Alembic, tests и CI

- migrations/env.py и alembic.ini относятся к PostgreSQL/Alembic; SQLite references в них не обнаружены при проверке.
- tests/conftest.py по умолчанию создаёт временный .db файл, выставляет MEDARCHIVE_DB_PATH и предоставляет fixture, импортирующую SQLite Database. Это legacy test foundation, не PostgreSQL fixture.
- Есть отдельные tests/adapters/sqlite/ и legacy tests для backup, routers/services и конфигурации. Их нельзя удалять механически: сначала установить, какие сценарии уже покрыты PostgreSQL equivalents и какие тесты остались только для legacy.
- .github/workflows/postgresql.yml устанавливает .[dev], поднимает PostgreSQL 16, применяет Alembic, запускает полный pytest -q -rs и отдельно проверяет encrypted backup/restore. Workflow настроен на push в master и docs/target-architecture-spec, а pull request — в master; push непосредственно в docs/ux-audit-project этим workflow не покрывается.
- pyproject.toml всё ещё устанавливает aiosqlite как обязательную runtime dependency; комментарий прямо указывает на временный legacy статус.

### 7.5. Данные и решение о миграции

docs/migration/sqlite-to-postgresql.md содержит зафиксированное решение владельца проекта от 2026-11-11: в SQLite-прототипе нет данных, которые нужно сохранять; перенос данных, staging, legacy ID mapping и импорт истории не входят в объём работ. Поэтому задача отказа от SQLite — удаление legacy кода/зависимостей и проверка PostgreSQL-only эксплуатации, а не миграция содержимого SQLite. Старые планы переноса данных не должны использоваться как актуальные требования.

### 7.6. Текущая оценка готовности к SQLite removal

**Не готово к удалению.** Подтверждены: legacy routers/services, SQLite adapter и dependency wiring, SQLite default config, aiosqlite, SQLite test fixture/tests, SQLite cleanup script и общий AuthService, который одновременно обслуживает PostgreSQL credentials. При этом PostgreSQL installation/backup/Alembic path уже существует.

Безопасная последовательность отдельного согласованного этапа: (1) определить единственный PG startup/config contract и убрать SQLite default; (2) отделить общий password/session helper от legacy SQLite principal; (3) перевести общий auth/dependencies на PG-only; (4) проверить покрытие всех нужных сценариев PostgreSQL routers/services; (5) решить судьбу cleanup script и legacy backup UI/templates; (6) удалить SQLite routers/services/adapters/tests и aiosqlite; (7) проверить clean install, миграции, smoke, full pytest и backup/restore на чистом PostgreSQL. Этот порядок — техническая рекомендация, не разрешение начать удаление.
