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
