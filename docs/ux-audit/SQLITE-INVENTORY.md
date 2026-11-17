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


## 8. Повторная точечная сверка ключевых файлов и общего auth runtime (2026-11-17)

Повторно получены актуальные версии `config.py`, `dependencies.py`, `services/auth_service.py`, `routers/auth.py`, `cli.py`, `pyproject.toml`, `tests/conftest.py`, `docs/migration/sqlite-to-postgresql.md` и `scripts/cleanup_stale_box_cards.py`.

### Уточнённые подтверждения

- `config.py`: `MEDARCHIVE_DB_PATH` остаётся в конфигурации; `MEDARCHIVE_AUTH_BACKEND` по умолчанию — `sqlite`; допустимы оба backend; для PostgreSQL требуется `MEDARCHIVE_POSTGRES_ENABLED=true`.
- `dependencies.py`: SQLite Database импортируется безусловно; `init_dependencies` создаёт SQLite Database и общий AuthService при любом backend. PostgreSQL Database создаётся отдельно при включённом PG. Legacy `get_db`, `get_current_user` и legacy role guard остаются в том же модуле, что и PG dependencies.
- `routers/auth.py`: общий auth router безусловно импортирует SQLite Database и общий AuthService. Внутри POST login есть PostgreSQL-ветка с PostgreSQLAuthService/UserRepository, но SQLite imports сохраняются на уровне модуля. Поэтому auth router — подтверждённая import-time SQLite связь PostgreSQL приложения.
- `services/auth_service.py`: общий AuthService используется не только SQLite. CLI PostgreSQL bootstrap создаёт его для password hashing; PostgreSQL dependencies/login передают его в PostgreSQLAuthService для credential/session функций. Удалять класс целиком нельзя без выделения общей реализации.
- `cli.py`: фактический bootstrap администратора работает через PostgreSQL Database/UserRepository; SQLite не используется как backend CLI. Общий AuthService остаётся зависимостью только для хеширования пароля.
- `pyproject.toml`: `aiosqlite>=0.20.0` остаётся обязательной runtime dependency; комментарий помечает её временной до удаления старых SQLite-модулей.
- `tests/conftest.py`: базовая pytest fixture задаёт `MEDARCHIVE_DB_PATH` и импортирует SQLite Database. Следовательно, legacy тестовый фундамент остаётся SQLite-first, несмотря на отдельный PostgreSQL CI workflow.
- `cleanup_stale_box_cards.py`: независимый скрипт использует stdlib `sqlite3`, SQLite backup API и UPDATE; он не является PostgreSQL maintenance tool.
- `docs/migration/sqlite-to-postgresql.md`: отдельное решение владельца проекта указывает, что данные SQLite-прототипа не требуется переносить; удаление legacy-кода остаётся отдельным изменением после проверки зависимостей.

### Что эта повторная проверка НЕ доказывает

- Она не является доказательством отсутствия SQLite-упоминаний/вызовов в каждом из 256 файлов дерева.
- Не выполнялся AST/import graph анализ, поиск по содержимому всех файлов, сборка пакета, запуск pytest или запуск PG-only приложения.
- Не проверялась реальная среда развёртывания и не подтверждалось, какой backend сейчас используется в production.

### Текущий статус

Ключевые общие точки зависимости подтверждены повторно; SQLite removal по-прежнему не готов к безопасному выполнению. Наиболее важный архитектурный узел — общий `dependencies.py`/auth router/AuthService, где legacy SQLite и PostgreSQL credential flow пока пересекаются. До отдельного согласования ничего не удалять и не менять.

## 9. Сверка runtime, production deployment, CI и SQLite-тестов (2026-11-17)

Проверены актуальные blobs следующих файлов: .github/workflows/postgresql.yml, scripts/install.sh, scripts/medarchive.service, scripts/backup.sh, pyproject.toml, src/medarchive/main.py, src/medarchive/dependencies.py, src/medarchive/routers/auth.py, legacy routers, SQLite adapter, backup service, pytest fixtures и профильные тесты.

### 9.1 Runtime и точка входа

- Config.auth_backend по умолчанию равен sqlite; MEDARCHIVE_POSTGRES_ENABLED по умолчанию false. Для PG backend нужны обе настройки: MEDARCHIVE_AUTH_BACKEND=postgresql и MEDARCHIVE_POSTGRES_ENABLED=true.
- main.py импортирует init_dependencies из общего dependencies.py при загрузке приложения. Общий модуль безусловно импортирует SQLite Database.
- init_dependencies() безусловно создаёт объект SQLiteDatabase(config) и общий AuthService, даже когда выбран PostgreSQL. При PG включённом дополнительно создаётся PostgreSQL Database.
- В PostgreSQL lifespan SQLite init() не вызывается; PostgreSQL routers подключаются отдельно. То есть подтверждённая связь PostgreSQL runtime с SQLite — import-time/initialization object dependency и обязательная установка aiosqlite, а не открытие SQLite соединения в штатном PG startup.
- Общий auth router импортирует SQLite Database на уровне модуля, хотя его PG ветка логина использует PostgreSQLAuthService/UserRepository. Импорт SQLite Database в этом router фактически не нужен для PG ветки.
- Legacy routers admin.py, archive.py, dashboard.py, packing.py, transfer.py напрямую импортируют SQLite database/repositories; они подключаются только при auth_backend=sqlite.
- Legacy admin router содержит SQLite backup UI и вызывает BackupService; PostgreSQL admin HTML router отдельный и такого UI не имеет.

### 9.2 Production install и backup

- scripts/install.sh создаёт /etc/medarchive/medarchive.env с MEDARCHIVE_POSTGRES_ENABLED=true и MEDARCHIVE_AUTH_BACKEND=postgresql; scripts/medarchive.service также задаёт PostgreSQL backend.
- Production backup shell/systemd использует PostgreSQL tools pg_dump/pg_restore и зашифрованные архивы age. scripts/backup.sh намеренно отказывается продолжать при наличии старых plaintext medarchive-*.dump.
- Следовательно, эксплуатационная установка уже нацелена на PostgreSQL; SQLite default в Config относится к общему/default и legacy режиму, а не к шаблону production deployment.

### 9.3 CI и фактическое покрытие

- Workflow .github/workflows/postgresql.yml поднимает PostgreSQL 16, устанавливает пакет .[dev], применяет alembic upgrade head, проверяет текущую revision и наличие ключевых таблиц, валидирует shell scripts, запускает весь pytest -q -rs, затем выполняет зашифрованный backup/restore в отдельную БД.
- Workflow trigger: push только в master и docs/target-architecture-spec; pull_request только в master. Push в рабочую ветку docs/ux-audit-project сам по себе этим workflow не запускается. Это конфигурация workflow, не доказательство статуса конкретных CI runs.
- В тестах явно остаются SQLite-specific suites: tests/adapters/sqlite/test_database.py, test_repositories.py, legacy router tests, test_admin_backup.py, test_backup_service.py, test_cleanup_script.py. Base fixtures в tests/conftest.py создают SQLite database по умолчанию.
- PostgreSQL-specific tests создают PostgreSQL Config явно/используют PostgreSQL integration fixtures. Workflow CI совмещает оба набора тестов; его команда запускает полный pytest suite, а не только PostgreSQL tests.
- tests/test_auth_config.py явно фиксирует SQLite как default backend. Это тестируемое поведение текущей конфигурации, не только забытая документационная ссылка.

### 9.4 Классификация оставшихся зависимостей

| Область | Текущее состояние | Статус для будущего PostgreSQL-only |
|---|---|---|
| SQLite adapter и repositories | Legacy runtime; прямые imports из legacy routers | Удалить после отключения legacy routers и их тестов |
| Общий dependencies.py | Безусловный SQLite import и объект Database | Развязать по backend; оставить только PG path в целевой версии |
| Общий auth router | SQLite Database import не нужен PG path | Удалить import и разделить/упростить backend wiring |
| AuthService | Общий password/session helper используется PostgreSQL | Сохранить полезную криптографическую часть, вынести из SQLite/legacy coupling |
| Config | SQLite default и MEDARCHIVE_DB_PATH | Пересмотреть default и удалить SQLite-only config |
| aiosqlite | Обязательная runtime dependency | Удалить после удаления всех runtime imports |
| SQLite backup service/UI | Только legacy admin router | Удалить вместе с legacy router/template/service/tests |
| cleanup_stale_box_cards.py | Standalone stdlib sqlite3 maintenance utility | Не включать в PG deployment; архивировать/удалить отдельным решением |
| SQLite pytest suites/fixtures | Часть полного pytest suite | Заменить/удалить в отдельном этапе; не ломать текущий CI до этого |
| PostgreSQL backup/restore | Отдельный shell/systemd/CI путь | Целевая эксплуатационная реализация уже есть |

### 9.5 Оставшиеся ограничения

- Это ручная сверка выбранных ключевых файлов и всех очевидных runtime/test/deployment entry points, а не AST-based полный import graph.
- Не выполнялись локальный запуск приложения, pytest, pip install, runtime import tracing или CI run.
- Не подтверждалась фактическая конфигурация работающего production экземпляра.
- Не анализировался каждый старый план/документ и каждый текстовый asset на упоминания SQLite; исторические документы могут сохранять контекст прототипа.
- Никакие файлы приложения, тесты, зависимости или workflow не менялись.

**Вывод:** production deployment и CI уже используют PostgreSQL, но кодовая база и тестовый пакет остаются двухрежимными. Для безопасного PostgreSQL-only перехода нужен отдельный согласованный этап: разорвать общий import/dependency coupling, вынести AuthService credential helpers, удалить legacy routers/adapter/backup и SQLite tests/fixtures, затем убрать aiosqlite и SQLite config. До согласования ничего не удалять.
