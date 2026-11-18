# UX-06 — инвентаризация критичных PostgreSQL DML paths и principals

Дата статического среза: 2026-11-18  
Ветка: `docs/ux-audit-project`  
Основа: read-only анализ исходников текущей ветки. Никакие БД, SQL, миграции, тесты или CI не запускались.

## 1. Вывод

Текущая PostgreSQL реализация использует общий `AsyncSession` как канал чтения и записи. Критические изменения выполняются ORM/DML из application services; маршруты обычно завершают транзакцию через явные `session.commit()/rollback()`. Утверждённый command boundary пока не реализован.

Инвентаризация выявила не только основные workflows, но и supporting write paths, которые должны учитываться до отзыва runtime DML:
- операционные workflows карт, коробок, заявок, отправлений и приёмки;
- административные изменения справочников и пользователей;
- intake/QR и внешнее хранение;
- bootstrap системных локаций и первого admin;
- login throttling;
- инфраструктурные миграции и legacy SQLite cleanup script.

## 2. Матрица критичных DML paths

| Контур | Точки входа / методы | Изменяемые данные | Требование к будущей DB-команде |
|---|---|---|---|
| Регистрация карты | `postgresql_packing.py` → `PostgreSQLCardIntakeWorkflow.register_from_qr`; `PostgreSQLCardIntakeWorkflow` | cards, domain_events | Команда регистрации карты по QR; проверить payload и branch scope |
| Создание коробки / упаковка | `postgresql_packing.py`; `PostgreSQLShipmentWorkflow.create_box/add_card_to_box/remove_card_from_box` | boxes, box_cycles, box_card_memberships, cards, domain_events | Разделить create box, add/remove membership и regroup; обеспечить D-10/D-11 |
| Списание пустой коробки | branch/central/archive packing & placement endpoints; `write_off_box` | boxes, box_cycles, placements, events | Отдельная команда списания тары с проверкой пустоты, активных shipment и placement |
| Архивное размещение | `postgresql_archive.py` placement endpoints; `PostgreSQLArchiveWorkflow.place_box/place_card/remove_*` | box_placements, card_placements, boxes/cards | Целевой контракт должен исключить самостоятельное размещение карты на полке (D-11); legacy CardPlacement пока существует |
| Заявки ЦО | `postgresql_archive.py` → `create_requests/mark_not_found` | archive_requests, cards, domain_events | Parent/item команды P-09; текущая реализация per-card |
| Извлечение по заявке | `/requests/unpack-card`; shipment workflow remove_card_from_box | memberships, cycles, cards, placements, events | Заменить поэлементный путь на атомарный parent-level start_processing (P-06) |
| Fulfillment заявки | `/requests/fulfill`; create_draft/add_standalone_card/hand_over | shipments, shipment_cards, cards, archive_requests, domain_events | Единая команда handover для всех оставшихся items одной заявки (P-09) |
| Отправления | `postgresql_transfer.py`, `postgresql_central_office.py`; shipment workflow create_draft/add_box/add_standalone_card/hand_over | shipments, shipment_boxes, shipment_cards, boxes, cards, domain_events | Команды create shipment draft, состав, handover; серверно проверять D-01/D-18 |
| Приёмка | branch/archive/central receive endpoints; `receive/_receive` | shipment state, cards, boxes, archive_requests, events | `shipment_receive_all`: полная атомарная приёмка D-02 и завершение parent P-09 |
| Внешнее хранение | `/central/custody/mark`, `/return`; `PostgreSQLCardCustodyWorkflow` | cards custody fields, domain_events | Команды mark_external/return_external; только central_office |
| Администрирование | `postgresql_admin.py`, `postgresql_admin_pages.py`; `PostgreSQLAdminService` | branches, locations, users, couriers, archive_shelves, domain_events | Allowlist admin-команд; обоснование и отдельный security/admin audit (P-07/D-15) |
| Системные локации | `ensure_system_locations` | locations | Отдельная bootstrap-команда/runner; не runtime web DML без необходимости |
| Первый admin | `medarchive-admin create-admin` → `UserRepository.create` | users | Одноразовый bootstrap путь с отдельным контролем доступа; не runtime workflow command |
| Ограничение попыток входа | `PostgreSQLLoginThrottle` | auth_login_throttles | Поддерживающий auth DML; отдельный минимальный grant или узкая DB-команда |
| Аутентификация / чтение | dependencies, auth service, repositories, receive queries | SELECT по users/locations/cards/shipments и др. | Сохранить read access, но не считать grants заменой application role/scope |
| Alembic | `migrations/env.py`, revisions 0001–0011 | DDL и миграционные DML/backfills | Только migrator credentials; никогда runtime credentials |

## 3. Подтверждённые технические наблюдения

### 3.1 Workflow services и routers

- `PostgreSQLArchiveWorkflow`, `PostgreSQLShipmentWorkflow`, `PostgreSQLCardCustodyWorkflow`, `PostgreSQLCardIntakeWorkflow` получают обычный `AsyncSession` и изменяют ORM объекты, добавляют `DomainEvent`, вызывают `flush()`.
- Проверенные routers для archive, central office, branch, packing, transfer и admin выполняют `session.commit()/rollback()` самостоятельно.
- `Database.transaction()` предоставляет context manager на `session.begin()`, но проверенные request handlers не используют его как единый обязательный use-case boundary.
- Shipment workflow уже использует блокировки отдельных строк и проверку состояний; это полезная основа, но не заменяет единую parent-request сериализацию P-09.
- Admin service записывает события `admin.*` в `DomainEvent`; в целевой архитектуре административный/безопасностный аудит должен иметь отдельный контур доступа и гарантии (P-07/D-16).

### 3.2 Административные изменения

Подтверждены операции:
- create/update/deactivate/activate branch и связанной Location;
- create/update/deactivate/activate user, включая смену роли, branch, пароля и активности;
- create/update/deactivate courier;
- create/update/deactivate archive shelf;
- блокировка конкурентных изменений пользователей через `pg_advisory_xact_lock(741928363)`;
- защита последнего активного admin реализована в application service с блокировками.

Целевой admin command boundary должен сохранять ограничения D-15 и включать аудит до/после изменения, actor, reason и запрет самодеактивации/потери последнего admin. Нынешний сервис допускает `viewer` в `VALID_ROLES`, что является legacy расхождением с D-06; удаление/миграция роли не входит в текущий этап.

### 3.3 Auth и bootstrap

- Login throttle выполняет raw SQL INSERT/UPSERT/DELETE к `auth_login_throttles`. Это не бизнес-таблица логистики, но runtime должен иметь ограниченный способ поддерживать throttle.
- `ensure_system_locations` создаёт singleton locations под advisory lock.
- CLI `create-admin` создаёт первого администратора в транзакции и использует тот же database URL из конфигурации.
- Bootstrap paths нельзя оставлять с неограниченным доступом runtime к users/locations. Их следует перевести на отдельные provisioning/maintenance процедуры либо ограниченные команды с одноразовым режимом.

### 3.4 Legacy и миграционные пути

- `scripts/cleanup_stale_box_cards.py` работает с SQLite таблицами `box_cards` и `cards`; это не PostgreSQL runtime path. Он остаётся legacy SQLite maintenance script и не должен получать PostgreSQL runtime grants.
- Alembic revisions 0001–0011 владеют DDL и историческими data migrations. В проверенных revisions нет provisioning owner/migrator/runtime principals.
- Admin UI по-прежнему содержит legacy страницы incidents; D-03 запрещает проектировать инцидент как целевой пользовательский процесс. Это отдельная задача legacy cleanup, не основание давать runtime DML на инцидентные таблицы.

## 4. Предварительная матрица PostgreSQL principals

Матрица является проектным baseline, а не фактическим grant audit. Реальные owners, grants, sequences, default privileges и функции не проверялись подключением к PostgreSQL.

| Объект / capability | owner (NOLOGIN) | migrator (LOGIN) | runtime (LOGIN) |
|---|---|---|---|
| Создание/изменение схем, таблиц, индексов, triggers | Владелец объектов | Controlled SET ROLE owner для миграций | Нет |
| DDL extensions / roles / database | Не автоматически | Нет без отдельного DBA provisioning | Нет |
| SELECT прикладных таблиц | По владению | По необходимости миграций | Только необходимое чтение |
| INSERT/UPDATE/DELETE workflow tables | Владелец объектов | В транзакциях миграции/backfill | Нет прямого DML после cutover |
| Вызов разрешённых workflow DB-команд | Владелец функций | По необходимости | EXECUTE allowlist |
| DomainEvent INSERT | Через контролируемый command boundary | Миграционные события по отдельной политике | Нет произвольного INSERT |
| Admin/security audit write | Через отдельный защищённый API | Миграционный контур по политике | Только разрешённые audit commands |
| auth_login_throttles | Владелец | При миграции | Минимально необходимый ограниченный DML/функции |
| sequences/identity | Владелец | По миграционной необходимости | Только использование через разрешённые операции |
| Backup/restore | Не предполагается | Не предполагается | Нет; отдельный infra principal |

### 4.1 Дополнительные принципы

- Не выдавать runtime членство в owner или migrator.
- Не передавать migration URL web workers, background workers и обычному CLI.
- Не полагаться на простое имя роли приложения, если runtime credentials могут выполнять `SET ROLE` в привилегированную роль.
- У SECURITY DEFINER функций фиксировать безопасный `search_path`, owner, EXECUTE grants, проверку actor/role/scope и защиту от SQL injection.
- Actor UUID остаётся application-authenticated assertion согласно DB-SEC-01, а не независимым доказательством личности.
- Read grants не реализуют D-07/D-08/D-09/D-16: application authorization/scope и/или отдельная RLS стратегия требуют отдельного проектирования.
- Учитывать sequences, views, materialized views, functions, triggers, extensions, advisory locks, health checks, backups и restore при полном grant inventory.

## 5. Незакрытые вопросы инвентаризации

До разработки конкретных GRANT/REVOKE и SQL API требуется:
1. Полный поиск всех `session.add/delete/execute`, ORM attribute mutations и raw SQL по всему PostgreSQL application package, включая вспомогательные модули, не попавшие в первую выборку.
2. Проверка фоновых задач, deployment scripts, backup/restore, health checks и эксплуатационных runbooks.
3. Разделение read-only SELECT paths и writes по таблицам/колонкам.
4. Отдельная инвентаризация административного аудита: текущие события `admin.*` находятся в DomainEvent, что не полностью соответствует требованию отдельного security/admin audit.
5. Определение bootstrap credentials и безопасного пути первого admin/system locations при отсутствии runtime DML.
6. Проверка фактических grants в целевом PostgreSQL окружении до cutover.

## 6. Следующий этап

Следующий документ должен содержать детальные контракты DB-команд для:
- `request_start_processing`, `request_edit_submitted`, `request_remove_item`, `request_mark_not_found`, `request_cancel`, `request_handover`;
- `shipment_receive_all`;
- `box_repack` и упаковки/списания;
- регистрации карты и внешнего хранения;
- административных корректировок и справочников.

Для каждой команды описать actor/role/scope, входы, предусловия, lock order, изменения таблиц, события, идемпотентность, ошибки и транзакционные границы. Затем подготовить grants matrix на уровне конкретных схем/таблиц/functions. Не реализовывать SQL/DDL или менять код без отдельного разрешения.

## 7. Границы выполненной проверки

- Проверено статически чтением файлов текущей ветки.
- Не запускались PostgreSQL, SQL, Alembic, pytest, линтеры, тестовые скрипты или CI.
- Реальные grants/owners/default privileges не проверялись.
- Исходники, тесты, миграции, workflows и deployment configuration не менялись.
