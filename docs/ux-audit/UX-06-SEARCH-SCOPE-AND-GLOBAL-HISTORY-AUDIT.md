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


## 8. Object detail visibility и dashboard global feed — статическое уточнение HEAD d132839

### 8.1 Admin: текущий доступ и недостающие object detail surfaces

- Глобальный search допускает admin, но результат карты содержит ФИО/статус/размещение и не содержит номера карты; карточки результатов в search_fragment.html не являются ссылками на объект.
- В inspected PostgreSQL routers нет универсального card detail route или box detail/history route. /boxes/{box_id}/label — отдельный printable label route; он явно разрешает admin для любой активной коробки, но не предоставляет object history.
- /shipments/{shipment_id} содержит manifest и DomainEvent timeline, но shipment_reader допускает только branch, central_office и archive. _user_location для admin пытается найти Location.kind='admin' и возвращает None; handler отвечает 403. Следовательно, admin не может открыть этот полноценный shipment detail через штатный route.
- /admin/incidents/{shipment_id} — специальный legacy exception/incident detail route, не универсальная карточка отправления; он не заменяет целевой object detail, а его процесс подлежит D-03 retirement.
- /requests показывает только активную очередь и доступен archive role; отдельного object detail route для ArchiveRequest не обнаружено.
- Единственная подтверждённая полноценная object timeline — shipment detail для sender/receiver roles. Card, box и archive request timelines в просмотренном наборе PostgreSQL routes не обнаружены.

Целевое следствие D-07: admin должен иметь отдельный global object-detail authorization path, не требующий фиктивной operational Location. Он должен давать глобальный доступ к объектной карточке и её истории, включая идентифицирующие данные, но не создавать global operational feed. Для остальных ролей остаются отдельные object-level scope predicates; D-08 ограничивает историю исходного branch после handover, P-08/D-09 регулируют manifest sender/receiver access.

### 8.2 Dashboard: подтверждённая global operational feed утечка

В postgresql_dashboard.py dashboard handler строит recent events как SELECT DomainEvent ORDER BY occurred_at DESC LIMIT 5. Фильтр по current Location применяется только для branch, central_office и archive. Для admin и viewer фильтра нет. dashboard.html отображает эти записи в секции «Последние движения».

Это прямое несоответствие D-16 и принятому уточнению D-07 × D-16: admin получает global operational feed на dashboard, хотя глобальная лента должна содержать только admin/security audit. Viewer также получает эту ленту, что дополнительно противоречит D-06.

Целевое поведение:
- admin dashboard не показывает global operational DomainEvent feed; вместо него допустимы административные/security audit summaries либо отсутствие глобального event feed;
- viewer исключается из dashboard route и всех связанных UI/read paths в рамках D-06 migration;
- operational roles сохраняют recent movements только в своей Location scope;
- admin сохраняет глобальные счётчики и поиск/карточки объектов, но object history открывает по конкретному объекту.

### 8.3 Негативные проверки для будущей реализации

1. Admin может открыть card/box/shipment/archive-request object detail и его timeline глобально, не имея Location.kind='admin'.
2. Admin dashboard не содержит operational DomainEvent feed и не показывает глобальные движения карт/коробок/отправлений.
3. Admin global audit показывает только admin/security audit records; operational shipment/card events там отсутствуют.
4. Sender/receiver сохраняют manifest и shipment timeline после завершения по P-08/D-09.
5. Branch не получает card timeline events after handover to archive (D-08).
6. Search result карточки не обещают ссылку на object detail, пока соответствующий route и visibility guard не существуют.
7. Archive request detail остаётся ограничен разрешёнными ролями; чужая роль не получает manifest/request PII.

Ограничение: это статическое чтение PostgreSQL routers/templates; HTTP responses, tests и DB policies не исполнялись.


## 9. Existing test assertions that encode legacy access (static inspection)

- tests/routers/test_postgresql_history.py creates a user with role viewer and asserts /history and /dashboard return 200 and render global history data. This is explicit legacy behavior that conflicts with D-06 and must be replaced by negative access tests after the migration plan is approved.
- tests/routers/test_postgresql_interface_role_matrix.py explicitly includes /dashboard, /search, /search/results and /history for viewer. The same test matrix does not include /shipments for admin; admin navigation is limited to dashboard/search/history/admin pages.
- tests/routers/test_postgresql_branch_search_history.py verifies current-location scope for branch/archive/central_office but does not establish branch history cutoff after handover; the fixture includes an archived own-origin card in setup, while assertions shown for the first scenario focus on location search/history.
- tests/routers/test_postgresql_dashboard.py checks authentication/session integration and that dashboard/search pages render for viewer; it does not assert that global operational movements are hidden.
- tests/routers/test_postgresql_admin_integration.py verifies administrative CRUD and audit, but the inspected test inventory does not establish admin global object detail/history access or shipment detail access.

No tests were executed. These are assertions found in source, not current runtime results.


## 10. Object detail and timeline contract — статическое уточнение HEAD 5779eea

Подготовлена отдельная спецификация `UX-06-OBJECT-DETAIL-AND-TIMELINE-CONTRACT.md`. Она определяет целевые detail/timeline поверхности для card, physical box + BoxCycle, shipment и parent ArchiveRequest; разделяет глобальный admin object access и object-scoped operational access; сохраняет P-08/D-09 для manifest sender/receiver и требует D-08 branch cutoff после handover.

Дополнительный legacy gap: `postgresql_archive.py` содержит GET/POST пути самостоятельного размещения и снятия карты с полки (`/placement/card`, `/placement/remove-card`), а также шаблонную обработку CardPlacement. Это противоречит D-11, где на полке находится только коробка. Целевое удаление этих путей должно сопровождаться инвентаризацией и миграцией legacy CardPlacement; на текущем этапе код и данные не менялись.

Тестовая матрица теперь явно включает прямой доступ по ID, admin без Location, viewer denial, branch cutoff с проверкой count/pagination, завершённый manifest для участников, запрет чужого shipment/request и отсутствие standalone card shelf placement. Тесты не запускались.


## 11. Authentication/security event coverage — статическое уточнение HEAD 50b06aa

Проверены `routers/auth.py`, `dependencies.py`, `postgresql_auth_service.py`, `postgresql_login_throttle.py`, `auth_service.py`, `postgresql_admin_pages.py` и `postgresql_admin_service.py`. Найдены admin mutation events для 13 типов; login success/failure/throttle, logout, invalid/revoked session и authorization denial не имеют найденного durable audit producer. Throttle использует keyed digests и bounded counters, но это не security audit. Credential reset отражается как `admin.user_updated` с `password_changed`, без отдельного event type. Session cookie stateless, 12h; server-side session registry/revocation path не найден.

Целевая event catalog/envelope, redaction, read permissions, atomicity, migration preservation и тесты вынесены в `UX-06-ADMIN-SECURITY-AUDIT-CONTRACT.md`. Retention, digest key rotation, denial noise policy, privileged-read scope и audit-store outage policy остаются открытыми. Только статический source inspection; тесты и runtime checks не выполнялись.
