# UX-06 — сопоставление DB-команд с текущей PostgreSQL-моделью

Дата статического среза: 2026-11-18  
Ветка: `docs/ux-audit-project`  
Основа: read-only анализ текущей ветки приложения. Код, DDL, миграции и тесты не запускались и не изменялись.

## 1. Назначение и границы

Документ связывает утверждённую целевую архитектуру DB security (DB-SEC-01…04) с фактической PostgreSQL реализацией на момент аудита. Это не спецификация для немедленного DDL: названия будущих команд концептуальные, а выводы основаны на статическом чтении ORM, workflow services, routers, dependencies и Alembic revisions.

Проверены:
- `src/medarchive/adapters/postgresql/models.py`;
- `src/medarchive/services/postgresql_archive_workflow.py`;
- `src/medarchive/services/postgresql_shipment_workflow.py`;
- `src/medarchive/services/postgresql_card_custody.py`;
- `src/medarchive/services/postgresql_admin_service.py`;
- `src/medarchive/routers/postgresql_archive.py`;
- `src/medarchive/routers/postgresql_shipments.py`;
- `src/medarchive/adapters/postgresql/database.py`;
- `src/medarchive/dependencies.py`;
- PostgreSQL migrations 0001, 0002 и 0010.

## 2. Фактическая модель данных

| Целевое понятие | Текущая таблица/ORM | Статическое наблюдение |
|---|---|---|
| Карта | `cards` / `Card` | Самостоятельный объект; текущая локация или shipment, статус и external custody хранятся непосредственно в Card |
| Физическая тара | `boxes` / `Box` | Имеет человекочитаемый box_number, current_location/current_shipment |
| Цикл коробки | `box_cycles` / `BoxCycle` | Уже существует отдельный цикл с номером и датами |
| Членство карты | `box_card_memberships` / `BoxCardMembership` | Есть частичный уникальный индекс одной активной membership на карту |
| Размещение коробки | `box_placements` / `BoxPlacement` | Есть отдельное размещение коробки на полке |
| Размещение карты | `card_placements` / `CardPlacement` | Существует в ORM и миграциях; противоречит утверждённой целевой модели D-11, где самостоятельное размещение карты на полке исключено |
| Отправление | `shipments` / `Shipment` | Состав разнесён на shipment_boxes и shipment_cards |
| Заявка ЦО | `archive_requests` / `ArchiveRequest` | Сейчас одна запись содержит один card_id; нет родителя с дочерними позициями |
| Операционное событие | `domain_events` / `DomainEvent` | Общая таблица; event_type и JSONB payload, aggregate reference и actor_user_id |
| Административный аудит | отдельный audit контур | Требует отдельной инвентаризации consumers и фактических grants; не объединять с DomainEvent |

### 2.1 Несовпадения с утверждённой целевой моделью

1. **Заявки ЦО:** текущая `ArchiveRequest` — per-card request. Целевая модель P-09 — одна родительская заявка с несколькими `ArchiveRequestItem`. В текущем ORM отдельной модели позиции не обнаружено.
2. **Статусы:** текущая проверка статусов `new / in_progress / completed / not_found / cancelled`; целевой жизненный цикл использует `draft / submitted / processing / completed / cancelled` и отдельные статусы позиций. Прямое переименование без миграционного плана недопустимо.
3. **Извлечение:** текущая логика имеет отдельный `unpack-card` endpoint и удаление карты из коробки как самостоятельный шаг; целевой P-06 требует атомарного извлечения всех позиций в момент начала обработки родительской заявки.
4. **Размещение:** `CardPlacement` остаётся действующей моделью и используется в запросах/проверках; целевое D-11 исключает самостоятельное размещение карт на полке.
5. **Отправка заявки:** текущий endpoint fulfillment принимает список card_ids и формирует отправление с отдельными картами; нет доказанного серверного барьера, связывающего полный состав единой родительской заявки с единственным shipment и единым handover по P-09.

Эти пункты являются разрывами между текущей реализацией и утверждённой целевой моделью, а не основанием автоматически удалять legacy-таблицы или менять схему.

## 3. Текущие workflow и границы транзакций

### 3.1 Archive workflow

В `PostgreSQLArchiveWorkflow` обнаружены:
- `create_requests(card_ids, actor_id)` — проверяет карты и создаёт несколько отдельных `ArchiveRequest`, по одной на карту;
- `mark_not_found(request_id, actor_id, note)` — меняет результат одной per-card заявки;
- `place_box`, `place_card`, `remove_card_from_shelf`, `remove_box_from_shelf` — операции размещения.

Workflow использует `flush()`, но не владеет commit. В `postgresql_archive.py` handlers явно выполняют `session.commit()` и `session.rollback()`. В частности:
- POST `/requests/not-found` вызывает mark_not_found и затем commit;
- POST `/requests/unpack-card` вызывает shipment workflow remove_card_from_box и затем commit;
- POST `/requests/fulfill` создаёт shipment draft, добавляет standalone cards, вызывает hand_over и затем commit;
- POST создания заявок ЦО вызывает create_requests и затем commit.

Следствие: транзакционный владелец сейчас фактически router handler, а не единый application use case/Unit of Work, утверждённый в архитектуре.

### 3.2 Shipment workflow

В `PostgreSQLShipmentWorkflow` обнаружены методы:
- `create_draft`, `add_box`, `add_standalone_card`;
- `hand_over`;
- `receive` / `_receive`;
- `remove_card_from_box`, операции создания/списания/упаковки коробок;
- `register_exception` / `resolve_exception`.

Workflow выполняет блокировки строк и проверки состава/локаций, а также пишет DomainEvent через общий helper. Он использует flush; внешние handlers выполняют commit/rollback. Текущая модель shipment позволяет манифест с коробками и отдельными картами, но групповой request aggregate и его атомарный handover по P-09 ещё не представлены.

### 3.3 DB session

`PostgreSQLDatabase.transaction()` существует и использует `session.begin()`, однако проверенные archive routers работают через dependency session и ручные commit/rollback. Наличие transaction context manager само по себе не означает, что все use cases уже используют единый Unit of Work.

## 4. Предварительное сопоставление целевых DB-команд

| Целевая команда | Текущие точки реализации | Основные таблицы | Разрыв / что уточнить |
|---|---|---|---|
| `request_create_draft` / `request_submit` | `PostgreSQLArchiveWorkflow.create_requests`; POST central archive requests | cards, archive_requests, domain_events | Сейчас создание сразу создаёт отдельные заявки per-card; draft/submitted parent aggregate отсутствует |
| `request_edit_draft` | Не обнаружена | archive_requests | Нет родительского состава и операции редактирования; целевая команда разрешена только автору и только в `draft`, после `submitted` состав неизменяем |
| `request_start_processing` | Отдельного use case не обнаружено; текущий unpack-card и fulfilment разнесены | archive_requests, cards, box_cycles, box_card_memberships, box_placements, card_placements | Требуется атомарная операция P-06: lock parent, проверить весь состав, извлечь все карты, закрыть memberships и при необходимости placements, сменить parent status |
| `request_remove_item` | Прямого эквивалента целевой позиции нет; текущие действия per-card/box | archive_requests, cards, memberships, cycles, placements, domain_events | Нужен результат позиции, обязательный комментарий и атомарный возврат исключённой карты |
| `request_mark_not_found` | `PostgreSQLArchiveWorkflow.mark_not_found`; POST /requests/not-found | archive_requests, cards, domain_events | Сейчас финализируется одиночная заявка; нужно позиционное событие внутри родителя |
| `request_cancel` | Текущая отмена отдельной заявки не эквивалентна P-09 | archive_requests, cards, memberships, cycles, placements, domain_events | Нужна parent-level отмена с различными полномочиями до/после processing и атомарным возвратом всех extracted карт |
| `request_create_fulfillment_shipment` / `request_confirm_fulfillment_handover` | Текущие `hand_over`/fulfilment endpoint — только частичная основа | shipments, shipment_cards, archive_requests, domain_events | Целевая модель: не более одного fulfillment shipment на parent; manifest содержит все и только extracted items после обработки всех позиций; единый handover переводит все manifest items в issued_pending_receipt и custody назначенному курьеру |
| `request_confirm_item_receipt` / `request_confirm_bulk_receipt` / `shipment_receive_box` | Текущая `receive/_receive` — только частичная основа | shipments, shipment_cards, shipment_boxes, cards, archive_requests, domain_events | P-09: item-level receipt и атомарный bulk только реально полученных карт; обычные коробочные shipments: box-level receipt по одной коробке; parent/shipment completion вычисляется по terminal outcomes |
| `box_repack` | Операции add/remove card to/from box в shipment workflow | boxes, box_cycles, box_card_memberships, box_placements, cards, domain_events | Требуется отдельный контракт нового BoxCycle и сохранение placement при опустошении в сценарии regroup (D-11) |
| `admin_correct` | `PostgreSQLAdminService` и admin routers | несколько операционных таблиц, DomainEvent / admin audit | Нужен allowlist корректировок, обоснование, запрет переписывания истории и отдельный security audit |

Названия команд — проектные идентификаторы, не существующие SQL-функции.

## 5. Текущая DomainEvent реализация

ORM содержит поля:
- `event_type`;
- `aggregate_type`, `aggregate_id`;
- `actor_user_id`;
- `occurred_at`;
- `payload` JSONB;
- `correction_of_event_id`;
- `request_id`.

Текущие workflow helpers создают DomainEvent непосредственно из application code и выполняют flush. Миграция 0002 добавляет append-only trigger на UPDATE/DELETE. Это подтверждает защиту от изменения/удаления существующих событий на уровне trigger, но **не доказывает**, что runtime не может произвольно вставить событие или изменить критичные таблицы. Отдельного типизированного schema registry/версии payload в проверенной ORM-модели не видно.

Целевое решение DB-SEC-03 сохраняет эту общую сущность, но требует до реализации:
- каталога event_type и схем payload с версией;
- явного actor_type/command_id/correlation_id и правил для system/migration events;
- минимизации PII;
- согласования aggregate ownership и consumers;
- разграничения DomainEvent и admin/security audit.

## 6. Текущая DB security основа и ограничения

Статически подтверждено:
- миграция 0001 создаёт PostgreSQL таблицы и append-only защиту DomainEvent описывает как часть схемы;
- миграция 0002 создаёт ряд workflow guards и append-only trigger;
- `dependencies.py` создаёт PostgreSQL session и извлекает пользователя из application authentication; role dependencies проверяют роли приложения;
- `Database` принимает один database_url и создаёт async engine/session factory;
- в проверенных миграциях 0001/0002 не обнаружено provisioning трёх principals, явного разделения runtime/migrator credentials или SECURITY DEFINER command API;
- проверенная runtime архитектура пока использует ORM session для чтения и DML в workflow services.

Ограничение достоверности: этот документ не является проверкой grants реального PostgreSQL окружения. Подключение к БД и выполнение SQL не проводились. Нельзя утверждать, что фактические production grants совпадают с локальными миграционными предположениями.

## 7. Очерёдность следующего проектирования

1. Завершить inventory всех критичных DML paths: routers, services, repositories, background tasks, CLI/scripts, admin corrections.
2. Составить детальный command contract для P-06/P-09/D-02: actor/role, inputs, preconditions, lock order, mutations, events, idempotency, error codes.
3. Отдельно определить read API и application scope; DB grants не заменяют row/role authorization.
4. Составить grants matrix: schema/table/sequence/function × owner/migrator/runtime.
5. Определить actor context, который передаётся DB-командам, и его проверку через users/roles; не трактовать его как независимую криптографическую идентичность.
6. Спроектировать typed/versioned DomainEvent contract и compatibility для текущих consumers.
7. Составить additive-first rollout и preflight для реального PostgreSQL окружения.
8. Только после завершения проектирования и отдельного разрешения планировать код, DDL, миграции и тесты.

## 8. Явно вне объёма этого статического этапа

- Не запускались PostgreSQL, SQL, Alembic, pytest или GitHub Actions.
- Не проверялись реальные grants/owners/default privileges.
- Не изменялись исходники, тесты, миграции, workflows или deployment configuration.
- Не создавались SQL-функции, роли, grants или миграции.
- Не удалялись legacy `CardPlacement`, per-card `ArchiveRequest` или старые статусы.

## 2026-11-22 — corrected command mapping for approved P-09 workflow

The mapping below follows the approved single-shipment, atomic handover and item-level receipt model for archive → CO. Courier custody at handover supersedes the earlier archive custody rule.

| Target command | Required target semantics | Transaction boundary |
|---|---|---|
| request_create_fulfillment_shipment | Create the sole fulfillment shipment for all extracted items after every position is processed; courier required; unique parent link | Shipment + full manifest + item links + courier snapshot + DomainEvent — one commit |
| request_confirm_fulfillment_handover | Current shipment author confirms physical handover of the entire manifest; all manifest items become issued_pending_receipt; custody transfers to the assigned courier | Shipment + all manifest items + custody + DomainEvent — one commit |
| request_issue_item | Archive records physical issue of one card position; unissued positions remain pending and can be issued later in the same parent request | One item + custody/DomainEvent update |
| request_mark_not_found | One item receives not_found with mandatory closed-list reason and optional comment; no write-off or fictitious location update | One item + DomainEvent |
| request_cancel_remaining | Before any issue, cancel the request; after partial issue, cancel only the remaining unissued items; issued items remain unchanged | Parent + selected remaining items, one commit |
| request_confirm_item_receipt | CO confirms actual receipt of one physically arrived card; only then custody transfers courier → CO | One item/card + DomainEvent |
| request_confirm_bulk_receipt | CO confirms a selected set of cards that have actually arrived; per-card actor/time is persisted | Entire selected set, all-or-nothing |
| box_place_bulk | Place several received boxes on one active shelf | All selected boxes + target shelf, all-or-nothing |
| box_move_bulk | Move boxes from one or more shelves to one target shelf | All selected boxes + source/target placements, all-or-nothing |

The target contract requires one full immutable manifest and one indivisible handover. Receipt is item-level, not shipment-wide: handover transfers custody to the assigned courier, and each actual receipt transfers only the received card to CO. No additional dispatch or cancellation after handover.

Ordinary box shipments remain separate from P-09 card fulfilment. Their receipt does not inspect card counts, seals or packaging; box placement remains a separate operation.
