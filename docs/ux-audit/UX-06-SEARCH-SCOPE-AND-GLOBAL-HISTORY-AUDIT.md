# UX-06 — search scope и границы глобальной истории

Дата: 2026-11-18  
Ветка: docs/ux-audit-project  
База: HEAD f0d4e60fe122e033ded3d2a7891a2be900a58251  
Тип: статический аудит исходников; не HTTP security test.

## 1. Целевое правило admin (решение владельца продукта)

Admin сохраняет глобальный поиск, глобальный просмотр карточек объектов и полную историю конкретного объекта. Глобальная лента ограничивается административным и security-аудитом. Отдельный глобальный operational feed не проектируется. История движения доступна в карточках объектов.

## 2. Текущая реализация search

В src/medarchive/routers/postgresql_dashboard.py helper _search_context:
- branch/archive/central_office получают активную Location соответствующего типа;
- карты доступны по current_location_id либо по current_shipment_id отправления in_transit, где текущая Location является отправителем или получателем;
- коробки используют аналогичное ограничение;
- при отсутствии Location для операционной роли возвращается пустой результат;
- admin не получает location_id, поэтому выполняет глобальный поиск;
- viewer также не получает location_id, а search routes явно допускают viewer, поэтому viewer фактически получает глобальный search scope;
- поиск карт сопоставляет card_number и full_name, но DTO выдачи содержит full_name, статус и сведения о размещении, не включая card_number; limit — 50 карт и 50 коробок.

Текущий admin search scope соответствует продуктовому решению. Viewer access противоречит D-06. Серверные guards должны закрыть viewer на GET /search, GET /search/results и POST /search.

## 3. History surfaces

- /history сейчас формирует выборку DomainEvent; branch использует origin_branch_id и может видеть события после передачи карты (D-08 gap).
- viewer и admin в этом handler не получают event filter; viewer global history нарушает D-06, admin global operational history нарушает уточнённую границу D-07 × D-16.
- Dashboard recent movements ограничен операционной Location для branch/archive/central_office; admin не должен получать глобальную операционную ленту через этот или другой dashboard feed.
- /admin/audit фильтрует DomainEvent по admin.% и является временным смешанным источником; целевой global audit должен читать отдельный admin/security audit store.
- Timeline конкретного shipment aggregate уже существует в shipment detail route. Требуется закончить inventory остальных object detail timelines и проверить их visibility/payload.

## 4. Целевые ограничения и негативные проверки

1. Admin может глобально искать карты/коробки и открывать карточки объектов; object history доступна в контексте конкретного объекта.
2. Admin не получает global operational event feed; global audit содержит только административные/security события.
3. Branch не видит события карты после handover в архив; право видеть manifest собственного shipment сохраняется по P-08.
4. Viewer запрещён на всех search/history endpoints независимо от скрытия пунктов меню.
5. Branch/archive/central_office search не возвращает карты/коробки вне текущей Location и собственных отправлений in_transit.
6. Не участвующая в shipment роль не получает его manifest или timeline.
7. Поиск и object detail не раскрывают дополнительные PII поля за пределами D-07/D-09.

## 5. Остаточная работа

- проверить object detail routes для card, box, shipment и archive request: visibility, PII, timeline source;
- завершить полный inventory DomainEvent consumers (routers, services, templates, jobs, exports, notifications, tests и внешние интеграции);
- перечислить все producer'ы admin.* и все административные/security действия, которые сейчас не пишут audit event;
- определить схему, permissions, retention и migration strategy отдельного admin/security audit;
- выполнить HTTP negative tests после отдельного разрешения на реализацию/тестирование.

## 6. Ограничения

Статическое чтение исходников на указанном HEAD. HTTP requests, pytest, PostgreSQL, SQL и CI не выполнялись. Production deployment и данные не проверялись. Исходники, тесты, миграции, workflows и deployment не менялись.


## 7. DomainEvent producer/consumer inventory — статическая проверка HEAD f0d4e60

### 7.1 Текущая модель и DDL

ORM `src/medarchive/adapters/postgresql/models.py` и initial migration `0001_initial_postgresql_schema.py` определяют один общий `domain_events` со столбцами: id, event_type, aggregate_type, aggregate_id, actor_user_id, occurred_at, JSONB payload, correction_of_event_id, request_id. Полей event_version, command_id и correlation_id в текущей модели нет. Migration `0002_workflow_guards.py` создаёт append-only trigger на UPDATE/DELETE. Он не запрещает INSERT; effective grants и обход trigger не проверялись.

### 7.2 Producer inventory

| Producer | Подтверждённые типы / область | Классификация |
|---|---|---|
| `postgresql_card_intake.py` | `card.registered_from_medical_system_qr` | DomainEvent |
| `postgresql_card_custody.py` | `card.external_custody_started`, `card.external_custody_returned` | DomainEvent |
| `postgresql_archive_workflow.py` | `archive_request.created`, `archive_request.not_found`, `card.placed_in_archive`, `card.removed_from_archive_shelf`, `box.placed_in_archive`, `box.removed_from_archive_shelf` | DomainEvent |
| `postgresql_shipment_workflow.py` | `box.created`, `box.written_off`, `box.cycle_started`, `box.cycle_closed`, `card.added_to_box`, `card.removed_from_box`, `shipment.created`, `shipment.handed_over`, `shipment.exception_registered`, `shipment.received`, `shipment.exception_resolved`, `box.shipment_handed_over`, `card.shipment_handed_over`, `box.shipment_received`, `card.shipment_received` | DomainEvent; exception types are legacy process surfaces subject to D-03 retirement |
| `postgresql_admin_service.py` | 13 `admin.*` types listed below | Target admin/security audit, not DomainEvent |

The admin service currently writes: `admin.branch_created`, `admin.branch_updated`, `admin.branch_deactivated`, `admin.branch_activated`, `admin.courier_created`, `admin.courier_deactivated`, `admin.courier_activated`, `admin.shelf_created`, `admin.shelf_updated`, `admin.shelf_deactivated`, `admin.shelf_activated`, `admin.user_created`, `admin.user_updated`. All are appended through `_audit_admin_change` to DomainEvent and associated with the actor where persisted.

This is a code-path inventory of identified PostgreSQL producers, not a claim that all possible producers (including external integrations, scripts, manual SQL or deployed versions) have been exhaustively excluded.

### 7.3 Consumer/read-path inventory

| Consumer | Read behavior | Target treatment |
|---|---|---|
| `postgresql_dashboard.py` dashboard recent movements | Reads DomainEvent for role/location-scoped card and shipment movement summaries | Keep only for authorized operational roles and current location scope; no global admin operational feed |
| `postgresql_dashboard.py` `/history` | Reads DomainEvent across card, box, shipment and archive_request aggregates | Replace with object-scoped history navigation; enforce D-06/D-08 and remove global operational feed for admin |
| `postgresql_shipments.py` shipment detail | Reads events for the concrete shipment aggregate and renders timeline | Preserve after shipment visibility check; this is an object timeline |
| `postgresql_admin_pages.py` `/admin/audit` | Reads `admin.%` from DomainEvent; filters and displays full JSON payload | Move to separate admin/security audit source; replace raw payload dump with allowlisted/redacted structured fields |
| `postgresql_admin_pages.py` shipment exception resolution | Reads `shipment.exception_resolved` for a concrete shipment | Keep as shipment-specific legacy timeline detail only while D-03 retirement plan permits; do not classify as global admin audit |

Templates confirmed for these surfaces: `postgresql_history.html`, `postgresql_shipment_detail.html`, `admin/postgresql_audit.html`. The audit template renders `item.event.payload | tojson` in full. No other direct DomainEvent query was found in the inspected PostgreSQL router/service set; this does not close external consumers, background jobs or exports inventory.

### 7.4 Test coverage evidence

- `tests/routers/test_postgresql_admin_integration.py` checks all 13 current admin event types, actor attribution, password-hash exclusion from payload, audit filtering and rendering.
- The same test file seeds operational DomainEvent and confirms the admin audit filter does not display a non-admin event.
- `tests/adapters/test_postgresql_schema_integration.py` checks the DomainEvent indexes and append-only trigger presence.
- `tests/adapters/test_postgresql_shipment_workflow.py` asserts multiple card/shipment event types and checks no extra events for rejected operations.
- `tests/routers/test_postgresql_branch_search_history.py` seeds DomainEvent records for branch scope/history scenarios.

These are source-level test assertions inspected statically. They were not executed in this audit.

### 7.5 Admin/security audit gaps

1. The current 13 event types cover branch/courier/shelf/user administration; this is not evidence of complete security audit coverage (authentication failures, throttling, session/security events, authorization denials, privileged reads and credential lifecycle were not established as audit producers).
2. Payloads include user names/usernames, courier full name and phone, and before/after administrative fields. The test excludes password hashes, but the UI renders all payload keys. Define per-event allowlists, sensitive-field policy, retention and privileged payload access before migration.
3. A command changing a business aggregate and requiring an admin/security record must write both authoritative records atomically and correlate them (future command_id/correlation_id); do not duplicate the business fact into the global audit feed.
4. Migration must preserve old `admin.*` rows, actor, timestamp, aggregate reference and payload provenance. Do not delete or rewrite append-only DomainEvent history; define a copy/verification/cutover strategy and whether old rows remain as legacy source.

### 7.6 Still open

- inventory jobs, scripts, exports, notifications and external consumers beyond the inspected source tree;
- full object-detail timeline inventory for card, box and archive request;
- enumerate security-sensitive actions and decide which events must be recorded;
- define audit schema, event catalog/versioning, payload allowlists, read permissions, retention, migration and atomic dual-record contract;
- perform HTTP/DB negative tests only in a separately authorized implementation/verification stage.
