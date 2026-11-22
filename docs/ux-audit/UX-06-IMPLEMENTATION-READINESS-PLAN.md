# UX-06 — сводный implementation/readiness plan

Дата: 2026-11-18  
Статус: план подготовки к реализации; не является разрешением на реализацию  
Основа: UX-06 storage model, technical data model, invariants/test matrix, DB security/transaction architecture, DB command mapping, DML/principals inventory, command contracts, grants matrix; решения D-01…D-18, P-01…P-09 и DB-SEC-01…04.

## 1. Резюме

Целевая модель МедАрхива требует не локального изменения одной таблицы или экрана, а согласованного перехода нескольких связанных контуров:

1. физическое хранение: Card → BoxCardMembership → BoxCycle → BoxPlacement;
2. групповые заявки ЦО: ArchiveRequest parent + ArchiveRequestItem;
3. одно fulfillment-отправление archive → central_office на заявку, полный manifest и единый handover; item/bulk receipt;
4. защищённые PostgreSQL DB commands и единая транзакционная граница;
5. разделение owner/migrator/runtime и постепенный отзыв runtime DML;
6. append-only DomainEvent с typed/versioned контрактом и отдельный admin/security audit;
7. согласованные UI, API, dashboard predicates и ролевые scopes;
8. миграция исторических данных без потери истории и без автоматического угадывания физического размещения.

**Рекомендуемый порядок:** сначала закрыть read-only preflight и технические решения, затем additive schema и principals, после этого backfill с отчётами, затем вертикально переводить агрегаты на DB commands и только после подтверждения полного покрытия отзывать прямой runtime DML. Legacy cleanup выполняется последним.

Нельзя начинать с массового REVOKE, удаления CardPlacement, переписывания старых миграций или одномоментного переключения всех workflows.

## 2. Что считается целевым результатом

### 2.1 Модель хранения
- Card остаётся самостоятельным главным объектом учёта.
- Обычное архивное хранение: карта находится в одном активном BoxCardMembership текущего BoxCycle.
- На полке размещается только коробка через BoxPlacement; самостоятельный CardPlacement не является целевой моделью.
- Каждое новое заполнение физической тары создаёт новый BoxCycle; история циклов и memberships сохраняется.
- Извлечение по началу обработки заявки и перегруппировка — разные серверные операции с разным поведением пустой коробки.

### 2.2 Групповая заявка ЦО
- Один parent request содержит множество позиций-карт.
- Только автор ЦО редактирует состав в состоянии `draft`. Отдельная команда `submit` фиксирует неизменяемый состав; после `submitted` ни ЦО, ни архив не могут добавлять, удалять, заменять или перепривязывать позиции. Архив меняет только результаты обработки существующих позиций; если требуется иной состав, архив отменяет заявку с обязательной причиной, а ЦО создаёт новую.
- Начало processing атомарно извлекает все подлежащие обработке карты.
- Архив фиксирует по каждой позиции результат: отправка, removed_by_archive или not_found.
- Архив может выдавать карты частями; ЦО подтверждает фактическое получение по каждой карте либо атомарным набором. Ответственность остаётся за архивом до receipt ЦО.
- P-09: все позиции заявки обрабатываются до создания единственного fulfillment-отправления; полный manifest содержит все позиции `extracted`, handover выполняется одной атомарной операцией. Receipt item-level; bulk receipt выбранных фактически поступивших карт атомарен; parent завершается после terminal всех items.
- Handover полного manifest и отмена parent до handover сериализуются; после handover отмена запрещена, `not_found` и `removed_by_archive` сохраняют окончательные результаты.

### 2.3 DB security
- medarchive_owner — NOLOGIN, владелец объектов.
- medarchive_migrator — отдельный LOGIN для контролируемых миграций.
- medarchive_runtime — отдельный LOGIN для приложения.
- После cutover runtime не имеет прямого DML к критичным workflow aggregates; разрешены только необходимые read paths и EXECUTE на allowlist команд.
- Actor baseline остаётся application-authenticated (DB-SEC-01), без заявления о независимой криптографической идентификации.
- DomainEvent остаётся общим append-only журналом с typed/versioned событиями; admin/security audit отделён.

## 3. Реестр потоков работ и зависимости

| Поток | Содержание | Зависит от | Результат/критерий |
|---|---|---|---|
| A. Data preflight | Инвентаризация фактических связей карт, коробок, заявок и shipment | Read-only доступ к копии production-like БД | Отчёты конфликтов, без автоисправления |
| B. Target schema | Parent/items, lifecycle, indexes, FKs, audit/idempotency structures | Утверждённая модель и A | Additive DDL с проверяемым backfill |
| C. Storage transition | BoxCycle/membership/placement invariants, CardPlacement retirement | A, B, D | Нет новых самостоятельных CardPlacement; все архивные карты классифицированы |
| D. DB security foundation | principals, ownership, schemas, default privileges, function security | Полный DML/read inventory, deployment inventory | Runtime/migrator credentials раздельны; effective ACL подтверждены |
| E. Command boundary | SQL command API, idempotency, actor/scope, Unit of Work | B, D, G | Критичные операции доступны через стабильный command contract |
| F. Request/shipment workflows | parent request, items, full processing before one shipment, full-manifest handover, item/bulk receipt, cancellation before handover | B, C, E, G | P-06/P-09/D-02 invariants выполняются атомарно |
| G. Audit/events | Typed DomainEvent, admin/security audit, consumers | B, D, E | Все записи согласованы с изменениями; нет произвольного runtime insert |
| H. API/UI | endpoints, screens, dashboards, permissions, filters | B, C, F, G | UI отражает новый lifecycle и не вызывает legacy writes |
| I. Cutover/hardening | per-aggregate revoke, negative tests, rollback rehearsal | A–H | Runtime DML отозван без скрытых обходных путей |
| J. Legacy retirement | legacy endpoints, CardPlacement read-only, viewer/incident remnants | I и отдельные планы миграции | Старые пути отключены, история сохранена |

Потоки не следует реализовывать строго по буквам как независимые проекты: D и G должны быть подготовлены до критичных команд; B/C/F/E проектируются согласованно; H может идти параллельно после стабилизации API contracts, но не раньше их.

## 4. Последовательность реализации по фазам

### Phase 0 — решение о старте и фиксация baseline

**Работы**
1. Отдельно согласовать начало реализации UX-06 и определить среду/снимок БД для preflight.
2. Зафиксировать commit/tag baseline, версию схемы, deployment topology и список работающих версий приложения.
3. Получить backup и подтвердить проверяемую процедуру restore на изолированной среде.
4. Установить владельцев решений для unresolved product/technical items из раздела 10.

**Gate 0**
- Есть письменное разрешение на реализацию.
- Определена тестовая копия данных и безопасный способ доступа.
- Известны deployment/worker topology, окружения и процесс отката.
- Production данные не меняются в рамках проектного аудита.

### Phase 1 — полный read-only preflight

**Данные и целостность**
- Счётчики Card, Box, BoxCycle, BoxCardMembership, BoxPlacement, CardPlacement.
- Карты с активным CardPlacement, активным membership, обоими одновременно или ни одним.
- Архивные карты без membership и без активной заявки.
- Карты с несколькими активными memberships/requests.
- Коробки с активным placement: пустые, непустые, не в archive location.
- Несогласованности Card.current_location/current_shipment, manifest, custody и коробочных связей.
- Активные per-card ArchiveRequest по статусам, наличие shipment, handover, receipt и фактическое местонахождение карты.
- Дубликаты активных заявок на одну карту и конфликтующие связи.
- Циклы без ожидаемой тары, коробки с несколькими открытыми циклами, orphan memberships/placements.

**Код и эксплуатация**
- Полный поиск ORM mutations, session.add/delete/execute, bulk DML и raw SQL по всему PostgreSQL package.
- Все HTTP endpoints, фоновые workers, scheduled tasks, scripts, CLI, bootstrap, cleanup, auth throttle, health checks.
- Deployment secrets/env vars, alembic runner, backup/restore, monitoring и operator runbooks.
- Все consumers DomainEvent, допустимые event_type, payload shapes и совместимость.
- Полный перечень SELECT paths с PII и их application-level scope.

**Результаты**
- Машиночитаемый и человекочитаемый preflight report.
- Список конфликтных строк/агрегатов с категорией, количеством и безопасным планом ручного разрешения.
- Матрица «таблица × read/write path × principal × owner».
- Никакие данные не исправляются автоматически.

**Gate 1**
- Каждый активный объект учёта классифицирован.
- Для каждого DML path известен исполнитель и требуемое право.
- Неоднозначные данные не скрыты за автоматическим backfill.
- Подтверждены backup/restore и способы воспроизведения preflight.

### Phase 2 — техническое закрытие контрактов и schema design

**Работы**
1. Зафиксировать точные поля parent request и request items, связи с shipment, историю удаления состава и сохранение исходных UUID/дат/авторов.
2. Утвердить единый источник связи parent ↔ fulfillment shipment; исключить двойное владение связью.
3. Утвердить command ledger: уникальность command_id, hash нормализованного входа, сохранённый типизированный результат, поведение при повторе с другим payload.
4. Утвердить typed DB result contract и каталог event_type/version/payload.
5. Утвердить механизм отдельного admin/security audit и его read permissions.
6. Утвердить точный механизм actor role/scope enforcement; не использовать подделываемый runtime GUC как proof.
7. Утвердить schema namespace, функции SECURITY DEFINER, фиксированный search_path, owner и адресные EXECUTE grants.
8. Утвердить deferred constraints/triggers и места, где целостность обеспечивается командой, а не CHECK.
9. Утвердить lock order для request → cards → boxes/cycles/memberships/placements → shipment/manifest и проверить отсутствие циклических блокировок.

**Gate 2**
- Для каждого инварианта указан конкретный механизм защиты.
- Для каждой команды определены actor/role/scope, предусловия, lock set/order, mutations, events, idempotency и errors.
- Нет открытого противоречия между storage model, command contracts и grants matrix.
- DDL review не требует угадывать поведение legacy данных.

### Phase 3 — DB security foundation и additive schema

**Рекомендуемый порядок**
1. Создать owner/migrator/runtime principals и отдельные secrets/URLs в непроизводственной среде.
2. Установить ownership/default privileges и отозвать ненужные PUBLIC privileges.
3. Добавить новые структуры аддитивно: request items, command ledger, admin/security audit, event version metadata и необходимые связи/индексы.
4. Создать read/command functions с узким allowlist и безопасным SECURITY DEFINER.
5. Ввести временные grants только для ещё не переведённых legacy paths; зафиксировать владельца и условие удаления каждого grant.
6. Добавить DB checks/partial unique indexes/deferred constraints после анализа совместимости со старым состоянием.
7. Выполнить migration smoke tests и проверить фактических владельцев/ACL в pg_catalog.

**Запреты**
- Не переписывать исторические Alembic revisions.
- Не отзывать runtime DML на все таблицы заранее.
- Не выдавать runtime owner/migrator membership или широкие schema/table grants.
- Не удалять legacy таблицы/столбцы до завершения backfill и периода совместимости.

**Gate 3**
- Runtime не может выполнять DDL/SET ROLE/изменять ACL.
- Migrator отдельно аутентифицируется и действует только через контролируемый runner.
- Новые структуры и функции созданы, права проверены прямыми SQL-тестами.
- Старое приложение продолжает работать на временной совместимой матрице прав.

### Phase 4 — backfill и reconciliation

**ArchiveRequest**
- Каждую legacy per-card заявку преобразовать в parent + одну item.
- Сохранить исходную идентичность заявки там, где это предусмотрено планом, и создать новый item ID.
- Сопоставить legacy statuses не механически, а с учётом shipment, handover, receipt и фактического состояния карты.
- Сохранить автора, timestamps, fulfilled_by, comments/reasons и исторические связи.
- Историю изменения состава не реконструировать вымышленными событиями: явно пометить недоступную историческую детализацию.

**P-09 physical transit, custody and item receipt**
- The current `Card.current_location_id/current_shipment_id` pair describes location/transit, while current `custody_status` describes external custody and does not identify the responsible organization. Target schema must add a distinct `cards.custody_location_id` FK; do not overload `custody_status`.
- For archive → CO handover, set the card's current shipment pointer to the specific batch but keep `custody_location_id = archive`. Only item receipt moves `custody_location_id` to central_office and clears the transit pointer.
- Add `shipment_cards.request_item_id`, `received_by`, `received_at` for P-09 item-level linkage and receipt evidence. A batch transitions to `partially_received` after the first, but before the last, item receipt; shipment-level received fields are populated only after every manifest card is received.
- Extend `shipments_status_check`, `ck_shipment_handover_time`, `ck_shipment_received_fields`, `ck_shipment_exception_fields`, handover/receipt CHECK constraints and deferred shipment guards together. Replace the legacy `medarchive_guard_shipment_update`, shipment consistency triggers and `medarchive_guard_archive_request` from revision `0002_workflow_guards` at the controlled P-09 cutover; they currently enforce shipment-wide transit/receipt and one `fulfillment_shipment_id`. Preserve ordinary box shipment semantics; do not globally impose P-09 partial receipt states on other routes.

**P-09 transport batch backfill**
- Additive schema only: current `archive_requests` is a per-card table, so create a shadow parent table and `archive_request_items`; introduce one-to-one parent↔shipment link (nullable UNIQUE `fulfillment_shipment_id` or authoritative UNIQUE `shipments.request_id`), `shipment_cards.request_item_id`, item receipt/custody metadata only where absent. Do not add `archive_request_items.fulfillment_batch_id`.
- Build deterministic mapping from every legacy per-card request UUID to exactly one parent + one item. Store this mapping in a unique migration map; preserve source IDs, actor/time, comments/reasons and DomainEvent history.
- Create a fulfillment batch only where the legacy shipment ↔ request/card manifest relation is unambiguous. Do not merge shipment rows based on matching dates, courier names or similar card lists.
- Do not infer item-level receipt from a legacy shipment-wide received status unless source evidence proves receipt of that specific card. Ambiguous shipment/item/custody rows go to a quarantine report and block cutover; no synthetic receipt/handover events.
- Reconcile parent/item/batch/manifest counts, unique active card reservations, item-to-batch assignment, shipment route, card current location/custody, timestamps and actor references. Produce checksums and UUID-level trace samples.
- Backfill must be idempotent: rerunning with the same migration batch/source UUIDs cannot create duplicate parent, item, batch, manifest row or event. Preserve legacy records read-only through the compatibility window; drop/rename is a separate approved retirement step.

**Storage**
- Не конвертировать CardPlacement в BoxPlacement автоматически.
- Карты с CardPlacement без membership вынести в ручную сверку; размещение в коробку выполняет уполномоченный пользователь с новым событием.
- Исторические memberships/cycles/placements не переписывать.
- Backfill не должен закрывать placement только потому, что текущая коробка пуста: сначала определить, почему она пуста (extraction/regroup/legacy anomaly).

**Проверка**
- Сверить counts и контрольные суммы до/после.
- Сформировать отчёты по unresolved rows, не пропускать их через cutover.
- Повторный запуск backfill безопасен и не создаёт дубликатов.

**Gate 4**
- 100% legacy rows либо преобразованы и сверены, либо находятся в явно утверждённой карантинной категории с владельцем решения.
- Нет автоматических догадок о местонахождении карт.
- Контрольные суммы и выборочные трассировки истории совпадают.
- Backfill протестирован на копии production-like данных и повторяем.

### Phase 5 — перевод workflow по вертикальным срезам

Переводить агрегаты и сценарии по одному, сохраняя совместимость старого и нового приложения на каждом шаге.

**5A. Хранение и коробки**
- card manual/QR registration;
- create box, add/remove card;
- BoxCycle lifecycle;
- placement;
- regroup с сохранением placement пустой коробки;
- write-off пустой коробки;
- запрет новых CardPlacement.

**5B. Заявки**
- create draft, edit draft (только автор), submit; submitted composition immutable;
- start processing + extraction одной транзакцией;
- remove item + возврат в коробку;
- mark not_found;
- cancel до/после processing с разными actor и атомарным возвратом;
- завершение без shipment, если все позиции имеют допустимые терминальные исходы.

**5C. Отправления**
- one-to-one fulfillment shipment на parent;
- каждая партия содержит только выбранные ready-to-issue items;
- отдельный handover для каждой партии;
- item-level receipt и атомарный bulk receipt только реально поступивших карт;
- завершение parent только после terminal исхода всех позиций.

**5D. Остальные маршруты и функции**
- маршруты D-18 и состав отправления D-01;
- external custody ЦО;
- admin справочники и корректировки;
- admin/security audit;
- dashboards/list predicates UX-05;
- QR payload карты без изменения существующего формата.

Для каждого среза одновременно переключаются command path, API, UI, audit events, tests и read models. Нельзя переводить только UI, оставляя старый endpoint с обходным DML.

**Gate 5 для каждого среза**
- Все новые команды проходят positive/negative tests.
- Старый write path выключен или защищён так, что не может обходить новый путь.
- События и история проверены.
- Счётчики dashboard совпадают с фильтрованными списками.
- Поддержан безопасный rollback на предыдущую версию приложения без потери уже записанных данных.

### Phase 6 — per-aggregate privilege cutover

После успешного перевода конкретного агрегата:
1. Проверить отсутствие вызовов старого DML path по логам/метрикам и поиском кода.
2. Отозвать прямые runtime INSERT/UPDATE/DELETE на его критичных таблицах.
3. Проверить permission denied для каждого прямого DML statement от runtime.
4. Проверить, что все разрешённые команды работают от runtime.
5. Проверить SELECT scope и PII exposure.
6. Обновить grants inventory и rollback runbook.

Не отзывать права на основании одного только факта, что UI использует новую команду. Нужны подтверждения всех API, jobs, scripts и maintenance paths.

**Gate 6**
- Для каждого критичного объекта есть evidence отсутствия legacy writes.
- Negative SQL tests доказывают запрет прямого DML.
- Нет привилегированных обходных ролей, доступных web workers.
- Общий cutover завершён только когда проверены все агрегаты и все runtime processes.

### Phase 7 — hardening, наблюдаемость и legacy retirement

- Полный аудит pg_catalog: role memberships, ownership, schema ACL, table/sequence/function ACL, default privileges, PUBLIC, search_path.
- Проверка SECURITY DEFINER на unsafe search_path, dynamic SQL, object shadowing и прямой обход actor/scope.
- Проверка append-only DomainEvent и admin/security audit от runtime.
- Конкурентные тесты, повтор команд, serialization failures, deadlocks, retries и idempotency.
- Проверка backup/restore и migration rehearsal на production-like объёме.
- Удаление старых endpoints/templates и legacy write triggers только после подтверждения отсутствия обращений.
- Существующие CardPlacement сохранить read-only как исторические данные без физического удаления и автоматического TTL/purge; они не являются текущим местоположением карты. Активные legacy placements сверять вручную, не преобразовывать автоматически.
- viewer migration, legacy incident UI/data cleanup и SQLite retirement выполнять отдельными планами, не смешивать с cutover UX-06.

**Gate 7**
- Все обязательные проверки раздела 9 пройдены.
- Подтверждён restore и rollback.
- Нет критичных unresolved data anomalies.
- Есть операционные инструкции и владельцы мониторинга.
- Отдельно утверждён срок хранения legacy read-only данных.

## 5. Обязательная матрица транзакционных сценариев

| Сценарий | Единая транзакция должна включать | Недопустимое частичное состояние |
|---|---|---|
| Start processing | lock parent, проверить весь состав, закрыть memberships, закрыть placement только у опустевших из-за extraction коробок, item extracted, parent processing, events | parent processing при частично извлечённых картах |
| Remove item | возврат карты в коробку, новое/активное membership, item removed_by_archive, comment/event | item удалён, карта осталась без хранения |
| Cancel до processing | parent cancelled, pending items cancelled, reason/event | часть состава осталась активной |
| Cancel после processing | возврат всех extracted карт, terminal item statuses, parent cancelled, events | часть карт возвращена, часть оставлена извлечённой |
| Handover единственного shipment | parent/shipment/all manifest items locks, full manifest, all items issued_pending_receipt, archive custody unchanged, events | повторный handover, неполный manifest, ошибочный parent reference | parent/batch/item locks, selected batch manifest, items issued_pending_receipt, archive custody unchanged, events | повторная выдача, item в двух batches, ошибочный parent reference |
| P-09 item/bulk receipt | validate only selected actually-arrived items; update each item receipt/custody/location, batch projection, parent terminality and per-item events atomically | any non-arrived item accepted; partial mutation on bulk failure; premature batch/parent completion |
| Regroup | lock всех коробок/карт, close old memberships/cycles, create new cycles/memberships, preserve placements, events | автоматическое снятие placement пустой коробки по общему trigger |
| Admin correction | allowlisted correction, reason, before/after audit, actor/time | произвольный UPDATE без отдельной записи аудита |

## 6. Обязательные тестовые наборы и evidence

### DB invariants
- unique active membership per card;
- unique open cycle per physical box;
- unique active placement per box;
- archive card membership/extracted invariant;
- no membership + extracted overlap;
- no duplicate active request items across parents;
- parent → one fulfillment shipment; its immutable manifest contains all and only `extracted` items of that parent;
- each transport batch contains only cards physically issued in that batch; parent item states remain independently tracked;
- parent cannot complete before all terminal results and receipt;
- after partial issue only the unissued remainder may be cancelled; issued items remain active until actual CO receipt;
- terminal results and audit metadata immutable;
- no direct location mutation outside workflow.

### Authorization and grants
- branch/central_office/archive/admin role and scope matrix;
- отправитель/получатель видят собственный manifest после завершения по P-08;
- чужие отправления и последующая история исходного branch недоступны по D-08;
- runtime direct DML denied на каждой критичной таблице после cutover;
- runtime EXECUTE denied для внутренних функций;
- PUBLIC не имеет лишнего EXECUTE/CREATE;
- runtime cannot SET ROLE owner/migrator;
- мигратор credentials отсутствуют в web worker environment;
- admin correction ограничена allowlist и обязательным reason.

### Concurrency/idempotency
- edit draft vs submit; submit vs start processing; duplicate submit/start-processing;
- cancel vs handover;
- duplicate start processing;
- duplicate handover;
- duplicate item/bulk receipt; duplicate shipment handover;
- two requests competing for same card;
- regroup vs shipment/request extraction;
- concurrent membership and placement mutation;
- serialization/deadlock retry with same command_id;
- same command_id with different input hash returns conflict.

### Evidence package per release
- migration SQL/revision and schema diff;
- preflight/backfill report;
- ACL/ownership report;
- pytest/unit/integration results;
- direct SQL negative-test results under actual runtime principal;
- concurrency test logs;
- event compatibility report;
- API contract tests;
- UI smoke/accessibility results;
- backup/restore rehearsal and rollback evidence.

Тест считается пройденным только при реальном запуске и сохранённом результате; статический review не заменяет runtime evidence.

## 7. Rollback и восстановление

Rollback должен быть спроектирован отдельно для каждой фазы.

- **До backfill:** откат additive migration возможен только если не созданы зависимости и нет данных; предпочтительно forward-fix, а не разрушительный down migration.
- **После backfill:** не удалять новые таблицы/колонки с преобразованными данными; откат версии приложения должен сохранять совместимый read/write contract. При необходимости использовать forward-compatible dual schema.
- **После перевода workflow:** переключать application version/feature flag только на заранее проверенный совместимый путь; нельзя возвращать прямой runtime DML без временного контролируемого grant и согласованного окна.
- **После handover:** бизнес-операции не «откатываются» административным SQL; исправление — отдельный auditируемый процесс.
- **После revoke grants:** восстановление ACL только через миграционный/operator principal по runbook, не через runtime.
- **При повреждении/потере данных:** восстановление из backup в изолированную среду, проверка consistency, затем контролируемый cutover; не выполнять blind restore поверх текущей БД.

Для каждого release определить: rollback trigger, owner решения, допустимую потерю доступности, восстановление credentials/ACL, совместимость схемы, сохранность DomainEvent и идемпотентности.

## 8. Порядок миграций Alembic — правила

Точные номера revisions определяются только после DDL review; сейчас не назначаются.

1. Новые таблицы/колонки/indexes — additive.
2. Новые функции и triggers создаются отдельно от backfill.
3. Backfill — идемпотентный и resumable; при большом объёме может быть отдельной управляемой процедурой, а не длинной блокирующей migration transaction.
4. Constraints сначала валидируются на данных, затем включаются/валидируются по безопасной стратегии.
5. Старые CHECK/FK/unique constraints не удалять до подтверждения нового пути и backfill.
6. Переход функций/endpoint contracts отделить от физического удаления legacy objects.
7. DROP/cleanup — отдельная последняя миграция после наблюдаемого периода.
8. Старые revisions не редактировать; SQLite cleanup не смешивать с PostgreSQL target transition.

## 9. Критерии допуска к production

Production cutover не допускается, пока не выполнены все условия:

- [ ] Gate 0–6 пройдены и задокументированы.
- [ ] Preflight/backfill report не содержит необъяснённых конфликтов.
- [ ] Restore rehearsal выполнен на сопоставимом объёме.
- [ ] Миграции проверены на production-like копии.
- [ ] Все critical DB invariants защищены механизмами БД и/или command boundary.
- [ ] Все составные операции атомарны и проверены при конкурентном исполнении.
- [ ] Негативные тесты прямого runtime DML действительно дают permission denied.
- [ ] Actor role/scope проверяются сервером и DB command; UI не является границей безопасности.
- [ ] DomainEvent append-only и typed/versioned; admin/security audit отдельный.
- [ ] Все consumers совместимы с новым event contract.
- [ ] API/UI/dashboard используют новые состояния и единые server predicates.
- [ ] Все маршруты/составы соответствуют D-01/D-18.
- [ ] QR payload карты сохранён без несовместимых изменений.
- [ ] Runtime, migrator, owner credentials и infrastructure principals разделены.
- [ ] Backup/restore, monitoring, bootstrap, maintenance и jobs учтены.
- [ ] Rollback/forward-fix runbooks проверены.
- [ ] Есть план наблюдения после cutover и назначены ответственные.

## 10. Открытые решения перед реализацией

Эти вопросы не отменяют ранее утверждённые D/P/DB-SEC решения, но влияют на DDL и rollout. Они должны быть закрыты до соответствующего этапа.

### Утверждённые продуктовые/операционные базовые правила (не открытые вопросы)
1. **P-04/D-13 — списание медицинской карты (утверждено):** ЦО инициирует заявку; один уполномоченный сотрудник архива исполняет её. Обязателен закрытый код `lost` / `damaged` / `other`; комментарий и внешний документ необязательны. До начала обработки заявку отменяет ЦО с обязательной причиной; после начала — только архив с обязательной причиной; после фактического списания отмена невозможна. Ошибочное списание исправляется отдельной P-07-командой с сохранением исходного события.
2. **P-07 — административные корректировки (утверждено):** обычный allowlist master-data полей карты пуст, включая номер, ФИО, дату рождения и телефон. Разрешены только отдельные типизированные корректировки custody/location, BoxCycle/membership, BoxPlacement и компенсация ошибочного списания; исходные события, shipment evidence и результаты P-09 не переписываются. Точные input/result schemas и DB command contracts — техническая детализация.

### Миграционные и операционные планы — не новые продуктовые решения
3. **D-06 — viewer:** продуктово и миграционно закрыто 2026-11-21. Роль исключена из целевой модели; приложение не введено в эксплуатацию, данных для миграции нет. Удаление legacy viewer paths и тестовых ожиданий — задача реализации.
4. **D-03 — legacy incidents:** продуктово закрыто 2026-11-21; старый exception process и связанные пути исключены без миграции рабочих записей (их нет). Удаление legacy code paths — задача будущей реализации.

### Технические решения и доказательства — требуют завершения до соответствующих DDL/cutover gates
5. **DB command idempotency:** четыре положения утверждены владельцем продукта 2026-11-18 как обязательный технический стандарт: client-generated UUID v4 с повторным использованием на retry; server-side schema-specific canonicalization + SHA-256 с версией алгоритма; replay успешных результатов и детерминированных бизнес-отказов после авторизации без фиксации auth failures/transient errors; бессрочное сохранение ключей необратимых операций с tombstone при очистке payload. Для обычных команд утверждён срок хранения записи идемпотентности 90 календарных дней после завершения; по истечении запись может быть очищена без удаления business records, DomainEvent и audit records. До реализации проверить фактические retry horizons клиентов/jobs, bounded cleanup и backup/restore interaction. Срок утверждён; реализация/DDL не разрешены.
6. **DB result contract (утверждено 2026-11-20):** для всех публичных DB-команд обязателен единый versioned JSONB envelope: `result_code`, `result_version`, `result_payload`, `command_id`, `correlation_id`, `completed_at`. Для каждой команды — закрытый allowlist кодов и строгая схема/validator payload; произвольный JSON, ORM-объекты и SQL-диагностика запрещены. Ledger сохраняет точный committed envelope, replay возвращает исходный результат после повторной проверки доступа. HTTP status/body mapping остаётся ответственностью API adapter и должен быть специфицирован до реализации. Решение о формате закрыто; API mapping — техническая детализация, реализация/DDL не разрешены.
7. **DB schemas (решение утверждено 2026-11-20):** целевая закрытая схема `medarchive` для таблиц и внутренних функций; `medarchive_api` создаётся только при необходимости отдельной grantable API boundary; `public` не содержит объектов приложения МедАрхива. Продуктово-архитектурный выбор закрыт. До реализации остаётся compatibility review SQL, ORM metadata, Alembic, extensions, backup/restore и operational scripts; точные ownership, ACL и migration schema должны быть подтверждены на техническом этапе. Реализация/DDL не разрешены.
8. **RLS/actor context (решение утверждено 2026-11-20):** RLS не является обязательной первой линией защиты. Защищённые DB-команды получают явный UUID авторизованного сотрудника и сами проверяют активность, роль и scope по авторитетным записям БД; caller-supplied role/location не доверяются, mutable GUC не является доказательством личности. Прямой runtime DML в обход команд запрещён. Read-side scope пока проверяется приложением. Архитектурный выбор закрыт; до реализации остаются threat-model/ACL review и негативные тесты фактических прав на целевой среде. Возможное будущее RLS — только после доказуемой привязки session identity к доверенному механизму. Реализация/DDL не разрешены.
9. **Login throttle (решение утверждено 2026-11-20):** использовать узкий набор фиксированных SECURITY DEFINER-функций: атомарная проверка/увеличение счётчиков, сброс счётчика успешного входа и ограниченная очистка только истёкших незаблокированных записей. Runtime получает только EXECUTE на эти функции, без прямого SELECT/INSERT/UPDATE/DELETE таблиц throttle. Функции используют фиксированный безопасный search_path, ограниченные входные параметры, keyed digests и конкурентно-безопасные обновления; вызывающий не выбирает произвольные ключи, timestamps или retention windows. Архитектурный выбор закрыт; до реализации остаются lock contention, атомарности throttle buckets и семантики cleanup. Важное разграничение: keyed bucket keys login throttle — внутреннее состояние ограничения частоты, не audit/event identifiers; они не копируются в security events и не используются для связывания событий. Состояние throttle отдельно от durable security audit. Реализация/DDL не разрешены.
10. **Bootstrap (решение утверждено 2026-11-20):** первый администратор и обязательные системные локации создаются отдельной одноразовой operator/infra-операцией вне web startup/runtime. Операция проверяет ожидаемое неинициализированное состояние БД, берёт блокировку от параллельного запуска, атомарно создаёт системные локации и первого администратора и записывает audit-запись без секретов. Используется краткоживущий явно выданный bootstrap credential, отдельный от runtime и migrator; после успеха credential/path отключается или отзывается. Постоянный bootstrap-пароль и HTTP endpoint запрещены; восстановление утраченного первого администратора оформляется отдельной break-glass процедурой. Архитектурный выбор закрыт; до реализации остаются выбор deployment principal, точный перечень системных локаций и runbook восстановления. Реализация/DDL не разрешены.
11. **Infrastructure principals (логическое разделение утверждено 2026-11-20):** использовать отдельные учётные записи/идентичности для runtime приложения (только разрешённые повседневные операции, без DDL и обходного DML), migrator (только миграции), владельца объектов БД (`NOLOGIN`), резервного копирования/восстановления (отдельно от runtime и migrator), мониторинга (только технические показатели, без PII и изменения рабочих данных) и первоначальной настройки (краткоживущая, отзываемая после bootstrap). Maintenance identity создавать только при наличии конкретной задачи и ограниченного набора команд. Архитектурный выбор закрыт; до реализации определить реальные grants/credentials и проверить их на выбранной deployment topology. Права backup/restore/monitoring/maintenance не конкретизировать без сведений о соответствующих инструментах и процедуре восстановления. Реализация/DDL не разрешены.
12. **DomainEvent consumers (scope уточнён 2026-11-20):** владелец продукта подтвердил, что внешние потребители событий на текущем этапе не предполагаются; внешняя доставка, подписки, outbox/consumer offsets, webhook-контракты и downstream compatibility сейчас не проектируются. Текущий scope — внутренние readers МедАрхива. До изменения event schema/payload/retention завершить inventory внутренних readers и проверить deployment jobs/readers и effective DB grants, чтобы исключить уже существующие внешние/операционные зависимости. Внешние потребители в продуктовый scope не входят; проверка фактического deployment остаётся техническим precondition. Реализация не разрешена.
13. **Read permissions (подход утверждён 2026-11-20):** право чтения проверяется на сервере по активному actor, роли, организационному scope, custody/состоянию объекта, участию в процессе и отдельному праву на чувствительные поля; SELECT privilege не равен пользовательскому доступу. Одна и та же scope/field policy обязательна для UI, прямых URL/API, поиска/autocomplete, фильтров, сортировки, пагинации, total counts, dashboard aggregates, notifications и будущих отдельно разрешённых exports. Авторизация — до сериализации PII и агрегации; чужие/несуществующие ID не раскрывают наличие объекта. DOB/phone — только самостоятельная карточка карты с отдельным правом; адрес пациента не передаётся в систему. До реализации завершить `role × object × field set × operation × scope × read surface` inventory и negative-test matrix, затем проверить effective PostgreSQL ACL/views/functions/inherited roles/PUBLIC/default privileges на целевой среде. Принцип принят; полный inventory и доказательство фактических grants остаются открытыми. Реализация/DDL не разрешены.
14. **Rollout compatibility:** определить поддерживаемые версии приложения и dual-path период. Не закрыто.
15. **Legacy CardPlacement retention (решение утверждено 2026-11-21):** существующие записи сохраняются как исторические read-only данные без физического удаления и без автоматического TTL/purge. Они не считаются текущим местоположением карты и не участвуют в операционных выборках active placement. Новые CardPlacement не создаются; активные legacy placements подлежат ручной сверке/корректному переводу в целевую модель с новым событием, без автоматического угадывания коробки. Если возникнет обязательное правовое требование к удалению/ограничению хранения, оно требует отдельной правовой оценки и отдельного решения; ad hoc purge запрещён. Продуктовое решение закрыто; реализация/DDL не разрешены.

### Технические вопросы, закрытые read-only аудитом
- **D-10/P-03 — физическая тара и BoxCycle:** текущая модель уже разделяет постоянную физическую тару Box и последовательные BoxCycle; ORM и интеграционные тесты подтверждают связь BoxCycle.box_id и повторное использование тары с новым циклом. Осталось сохранить эту семантику в target schema и миграции; отдельного продуктового решения сейчас не требуется.
- **D-17 — QR карты:** фактический parser contract извлечён из кода и интеграционных тестов; формат не менять. Перед реализацией сохранить совместимость и включить регрессионные проверки.
- **DomainEvent append-only UPDATE/DELETE:** миграция 0002 создаёт trigger и trigger function, запрещающие UPDATE/DELETE. Это не доказывает отсутствие INSERT abuse или обхода через привилегированные principals; effective grants и trigger bypass проверяются на preflight/cutover.
- **DomainEvent ownership и command ledger:** разделение authoritative records и рекомендуемая ledger-модель описаны в UX-06-EVENT-OWNERSHIP-AND-COMMAND-IDEMPOTENCY-AUDIT.md; это проектная рекомендация, не реализованная схема и не согласование всех параметров.

Не следует задавать эти вопросы повторно, если соответствующее решение уже дано в проектной переписке; этот список — реестр для закрытия только оставшихся пунктов.

## 11. Что не входит в этот план

- Не изменять код, ORM, API, UI, tests, migrations, workflows или deployment до отдельного разрешения на реализацию.
- Не удалять SQLite и старые ArchiveRequest до отдельных решений. `viewer` и legacy incidents исключены из целевой модели; CardPlacement history сохраняется без физического удаления, legacy write path выводится из эксплуатации отдельно.
- Не менять формат QR карты.
- Не создавать новую сущность incident.
- Не внедрять дополнительные маршруты сверх D-18.
- Не выполнять production SQL, GRANT/REVOKE, backfill или data correction в рамках проектного этапа.
- Не считать текущую реализацию соответствующей целевой модели до фактического cutover и тестирования.

## 12. Текущее состояние и следующий шаг

UX-06 теперь имеет согласованный набор проектных документов: storage model, technical data model, DB invariants/test matrix, security/transaction architecture, command mapping, DML/principals inventory, protected DB command contracts, grants matrix и этот consolidated readiness plan.

Это означает, что основные технические зависимости и последовательность будущей реализации описаны. Это **не означает**, что все открытые решения закрыты или что реализация разрешена.

Следующий шаг — завершить технические контракты и инвентаризации из раздела 10 (включая retry-horizon inventory, API error mapping для уже утверждённого typed result envelope, deployment principals, внешних consumers и retention), выполнить read-only preflight по данным и deployment на согласованной непроизводственной копии, затем пройти итоговую проверку согласованности и Gate 0–2. P-04/D-13 и P-07 уже утверждены и повторного продуктового согласования не требуют. UX-06 может быть представлен к рассмотрению после этих действий; реализация и production cutover требуют отдельных разрешений и gates.

## 11. Уточнение D-07 × D-16 (2026-11-18)

Владелец продукта подтвердил: admin сохраняет глобальный поиск и просмотр карточек объектов, включая идентифицирующие сведения, и полную историю каждого конкретного объекта. Глобальная лента ограничивается административным и security-аудитом; глобальный operational feed не проектируется. Следствия для readiness:
- убрать admin из целевого глобального /history операционных DomainEvent;
- сохранить object-scoped history для admin в карточках объектов;
- сохранить глобальный поиск admin;
- реализовать отдельный admin/security audit store/read path вместо текущего фильтра admin.% в общей DomainEvent;
- обеспечить атомарную корреляцию admin/security audit и DomainEvent, если одна административная команда меняет бизнес-агрегат.

Это продуктовое решение закрыто; остаются технические задачи разделения audit ownership, schema, permissions, consumers и миграции исторических admin.* событий.

## 13. DomainEvent consumer/producer inventory — уточнение 2026-11-18

Read-only inspection at HEAD f0d4e60 identified PostgreSQL event producers in card intake, card custody, archive workflow, shipment workflow and PostgreSQL admin service. The admin service currently emits 13 `admin.*` event types for branch, courier, shelf and user create/update/activation mutations. These belong in the target admin/security audit, not the target business DomainEvent stream.

Identified consumers: dashboard recent movements, global `/history`, shipment aggregate detail timeline, `/admin/audit`, and a shipment-specific exception-resolution detail read. The current admin audit template renders the full JSON payload. Tests statically inspected cover the 13 admin event types, actor attribution, password-hash exclusion, audit filtering/rendering, append-only trigger presence, shipment event assertions and branch history fixtures. Tests were not run.

Additional blockers: prove security-event coverage beyond administrative CRUD; define payload allowlists/redaction and PII retention; inventory external/background/export consumers; preserve old admin.* evidence through a non-destructive migration; and define atomic dual-write/correlation when an administrative command also changes a business aggregate. No schema or source changes authorized.


## 14. Command idempotency — уточнение технического baseline (2026-11-18)

В UX-06-DB-COMMAND-CONTRACTS.md добавлено проектное предложение по UUID command_id lifecycle, server-side schema-specific canonicalization/hash, разделению command_id/correlation_id/request_id, конкурентной регистрации через unique key, replay с повторной проверкой actor/read permission, классификации negative outcomes, result envelope и retention/tombstone. Эти пункты не считаются согласованными до ответа владельца продукта. Реализация и DDL не разрешены.


## 15. Admin object detail и dashboard feed — дополнительные UX-06 gaps (2026-11-18)

Статически подтверждено:
- admin global search существует, но search result cards не ведут на универсальные card/box detail routes; такие routes в inspected PostgreSQL router set не обнаружены;
- shipment detail содержит manifest/timeline, но его role guard исключает admin и требует operational Location; admin-specific incident detail — только legacy exception page, не object detail;
- archive request queue доступна archive role и отдельного request detail route не обнаружено;
- dashboard recent movements выполняет нефильтрованный DomainEvent query для admin и viewer и показывает глобальную ленту на dashboard.

Целевые требования:
1. добавить object-scoped detail/history read surfaces для card, box, shipment и archive request; admin получает global object access без synthetic Location;
2. убрать operational DomainEvent feed с admin dashboard; global feed только admin/security audit;
3. закрыть viewer dashboard/search/history routes и мигрировать legacy accounts по D-06;
4. сохранить location/participation scope для операционных ролей, D-08 для branch history и P-08/D-09 для shipment manifest.

Это статические findings, не HTTP подтверждение. Код и тесты не менялись.


## 16. Test-suite legacy access expectations (2026-11-18)

Static inspection found tests that explicitly encode viewer access to /dashboard, /search, /search/results and /history, including rendering global history. These tests conflict with accepted D-06 and must be revised as part of a separately approved implementation/migration; do not treat passing legacy tests as proof of target authorization.

Current tests establish operational location scope for branch/archive/central_office, but do not establish the branch post-handover history cutoff required by D-08, nor admin global object detail/timeline access required by D-07. Dashboard tests do not assert the absence of global DomainEvent movements for admin. HTTP/pytest were not run.


## 17. Object detail and timeline authorization contract (2026-11-18)

Добавлен `UX-06-OBJECT-DETAIL-AND-TIMELINE-CONTRACT.md`. Документ переводит D-07/D-08/D-09/D-11/D-16 и P-08/P-09 в матрицу доступа к карточкам карты, коробки, отправления и групповой заявки ЦО. Зафиксированы admin global object access без synthetic Location, operational object/participation scope, branch history cutoff после handover, полная история собственного shipment для sender/receiver, правила состава timeline, PII и негативные/позитивные тесты.

Документ также фиксирует обнаруженный legacy конфликт: PostgreSQL archive router по-прежнему предоставляет самостоятельное shelf placement карты (`/placement/card`), несовместимое с D-11. Нужен отдельный migration/retirement plan; legacy данные и код не удалять на этом этапе.

Это проектная спецификация, не разрешение на реализацию. Выводы основаны на статическом чтении исходников на HEAD `5779eea`; HTTP, PostgreSQL, pytest и CI не запускались.


## 18. Administrative/security audit coverage — static inspection 2026-11-18

Добавлен `UX-06-ADMIN-SECURITY-AUDIT-CONTRACT.md`. В просмотренных PostgreSQL auth/dependency/admin paths найдены 13 административных mutation event types, но не найдены durable audit events для успешного/неуспешного входа, throttling, logout, invalid/revoked session и authorization denial. Login throttle хранит HMAC-keyed counters, но не заменяет audit trail. Password reset включён в generic `admin.user_updated` с boolean-флагом. Предложены event catalog, allowlisted envelope/payload, atomicity, read permissions, migration preservation и тесты.

Открытыми оставлены retention, digest key management, policy для noisy 401/403 и privileged reads, audit-store outage behavior и точная схема. Это статический аудит и проектное предложение; реализация не разрешена, тесты/HTTP/DB/CI не запускались.


## 19. P-04/P-07 workflow gap audit (2026-11-18)

Добавлен `UX-06-P04-P07-WORKFLOW-GAP-AUDIT.md`. Статически подтверждено: PostgreSQL реализует списание пустой коробки, но отдельный card write-off workflow/command/route не обнаружен; статусы Card `inactive`/`under_review` не определяют согласованный процесс списания. Admin CRUD существует, но отдельные corrective commands для card/box/shipment/request/custody/placement/history не обнаружены; поле `DomainEvent.correction_of_event_id` само по себе не реализует процесс коррекции.

В документе зафиксированы обязательные границы P-04 (инициирует ЦО, исполняет архив) и P-07 (allowlist, обязательная причина, append-only correction, атомарность), а также точные продуктовые решения, блокирующие соответствующие state machine и UI. Оба решения остаются открытыми.


## 20. Technical contract consolidation (2026-11-18)

Добавлен `UX-06-TECHNICAL-CONTRACT-DECISIONS-PROPOSAL.md` с рекомендациями по versioned JSONB result envelope, приватной application schema `medarchive`, ограниченной роли RLS при DB-SEC-01, SECURITY DEFINER throttle API, one-shot operator bootstrap, разделению infrastructure principals и event consumer compatibility.

Ключевые рекомендации: не использовать mutable GUC как доказательство actor identity; на первом этапе проверять actor/role/object scope внутри защищённых DB-команд; не выдавать runtime table DML для throttle, а открыть узкие функции; bootstrap вынести из web startup; backup/monitoring/maintenance grants не проектировать до выбора реальной deployment topology.

Consumer inventory остаётся неполным в части внешних интеграций и production jobs; не менять event contract до завершения branch-aware repo search и подтверждения внешних consumers. Все рекомендации — проектные, не реализация.


## 14. Command idempotency — стандарт утверждён (2026-11-18)

Владелец продукта подтвердил все четыре положения раздела 11 документа `UX-06-DB-COMMAND-CONTRACTS.md` как обязательный технический стандарт МедАрхива. Согласованы UUID v4 lifecycle и reuse ключа на retry, server-side schema-specific canonicalization с SHA-256 и версией алгоритма, replay успешных и детерминированных бизнес-результатов после авторизации, а также запрет удаления ключей необратимых операций с сохранением tombstone при очистке результата.

Решение по политике идемпотентности закрыто. Открытыми остаются только технические детали: inventory retry horizons и календарные сроки хранения ledger для остальных команд, а также окончательная классификация необратимых команд. Они должны быть определены до настройки purge/retention, но не отменяют утверждённый стандарт.

Изменены только проектные Markdown-документы. Это не разрешение на реализацию: SQL/DDL, ORM, API, UI, тесты, миграции и deployment не менялись.


## 18. DomainEvent consumer inventory — уточнение (2026-11-18)

Добавлен `UX-06-DOMAIN-EVENT-CONSUMER-INVENTORY.md`. В просмотренных PostgreSQL router paths перечислены consumers dashboard recent movements/history, shipment detail timeline, admin audit и точечное чтение shipment exception resolution. Поправка: legacy `/history` вызывает `MovementService.get_recent_movements` и читает SQLite `card_movements`, а не PostgreSQL `DomainEvent`; прежние упоминания этого legacy route как DomainEvent consumer неточны. Backup использует полный `pg_dump` и не является прикладным event consumer. В просмотренном source tree не обнаружены app-level queue worker, outbox dispatcher или webhook publisher; внешние/серверные jobs этим не исключаются.

Readiness остаётся открытым до подтверждения внешних consumers/deployment jobs и effective DB grants. Не менять event types/payload/retention до такого подтверждения. Только Markdown документация; runtime проверки не выполнялись.


## 21. P-04 — mandatory structured write-off reason approved (2026-11-18)

Владелец продукта подтвердил обязательный структурированный код основания для заявки ЦО на списание медицинской карты; свободный комментарий необязателен и сохраняется, если предоставлен. Решение закрывает вопрос обязательности кода/комментария, но каталог кодов ещё требуется определить. Открыты необходимость внешнего акта/вложения, второго подтверждающего сотрудника архива и правила отмены заявки. Полномочия P-04 не меняются: ЦО инициирует, архив исполняет. Только Markdown; реализации и тестов нет.


### P-07 field-level card correction decision (2026-11-18)

The product owner rejected the proposed `Card.phone` clerical correction. The ordinary P-07 card-field allowlist is empty: no card master-data field, including `phone`, is editable through P-07. Any future contact/identity-data correction requires a separately designed and approved process. Previously approved P-07 domain corrections (custody/location, current BoxCycle/membership, BoxPlacement and compensating write-off correction) remain separate typed commands, not generic card metadata patches.


## 22. Актуальный статус после согласования P-04/P-07 и технических стандартов (2026-11-18)

Этот раздел имеет приоритет над ранними формулировками разделов 10, 12, 18–20 и 21, где отдельные решения ещё значились открытыми. Ранние записи сохранены как исторический журнал.

### Продуктовые решения, закрытые владельцем

- P-04/D-13: инициатор — ЦО; исполнитель — один уполномоченный сотрудник архива; обязательный закрытый reason code `lost` / `damaged` / `other`; комментарий и внешний документ необязательны; отмена до/после начала обработки ограничена ролями и обязательной причиной; после списания отмена невозможна, ошибочное списание — отдельная P-07 компенсация.
- P-07: обычный allowlist изменений master-data полей Card пуст, включая `phone`; domain corrections для custody/location, BoxCycle/membership, BoxPlacement и erroneous write-off выполняются только отдельными typed commands. Shipment evidence, ArchiveRequest outcomes и исходные event attribution/payload не переписываются.
- DB-SEC-01…04, общая append-only DomainEvent модель и четыре положения command idempotency baseline утверждены. Не запрашивать повторное продуктовое согласование этих пунктов.

### Текущие технические/операционные gates

1. **External consumer and grants inventory:** получить подтверждение владельца эксплуатации по cron/systemd jobs, ETL/BI, ручным SQL consumers, read replicas и интеграциям; сверить effective DB roles/grants на реальном deployment до изменения event schema/payload/retention.
2. **Deployment principal topology:** описать фактические app/migrator/backup/monitoring/maintenance identities, URLs и secret distribution. Базовые principals `medarchive_owner` NOLOGIN, `medarchive_migrator` LOGIN и `medarchive_runtime` LOGIN уже утверждены; дополнительные роли зависят от подтверждённой топологии.
3. **Typed command/event catalog:** завершить per-command input/result schemas, versioning, state guards, event types, negative outcomes и mapping API errors. P-04/P-07 typed command contracts должны отражать утверждённые правила, не generic override.
4. **Mandatory event/audit write availability (утверждено 2026-11-21):** любая изменяющая состояние команда, контракт которой требует `DomainEvent` и/или `AdminSecurityAudit`, фиксирует бизнес-изменение, все обязательные записи событий и результат команды атомарно. Если обязательную запись сохранить нельзя, вся транзакция откатывается; изменение без истории не допускается. Правило распространяется на значимые бизнес-операции и privileged mutations. Успешный login не выдаёт сессию до commit обязательного audit record. Ordinary reads автоматически не блокируются; routine 401/403 не записываются в основной бизнес-журнал и не создают по одной обязательной durable audit-записи. Утверждена отдельная restricted/best-effort security telemetry только для выбранных high-risk denial или threshold crossings; повторы группируются/агрегируются, частота и объём ограничиваются, отказ/недоступность telemetry не блокирует запрос и не меняет denial outcome. Политика обработки отказов доступа как продуктовое решение закрыта; до реализации остаётся технически определить reason codes, aggregation keys/windows/thresholds, maximum rate, retention и поведение при недоступности хранилища. Также остаются retention/legal hold, HMAC key custody/rotation для целостности audit-записей, privileged-read scope for business-data exports и tamper evidence. Сбор и корреляция событий по IP/identity digests исключены решением владельца продукта (2026-11-21).
5. **Command ledger retention:** срок для обычных команд утверждён — 90 календарных дней после завершения; tombstone необратимых команд сохраняется бессрочно. До реализации подтвердить, что retry horizons клиентов/jobs укладываются в 90 дней, и специфицировать bounded cleanup и backup/restore interaction.
6. **Migration/reconciliation preflight:** инвентаризировать только те legacy SQLite/CardPlacement/ArchiveRequest и исторические admin events, для которых миграция или retention остаются применимыми. Viewer-account и incident reconciliation не требуются; рабочих данных нет.
7. **Evidence/test matrix:** для каждой команды/роли зафиксировать позитивные, негативные, concurrent retry, rollback, append-only, grants и migration-preservation сценарии. Тесты пока не запускались.

### Текущая граница

Продолжается только read-only аудит и Markdown-проектирование. UX-06 не закрыт; UX-07 не начинается; реализация приложения, SQL/DDL, ORM, миграции, API/UI, тесты, workflows и deployment не разрешены и не менялись. PostgreSQL/CI/pytest не запускались.


## 11. AdminSecurityAudit catalog design gate (2026-11-19)

A detailed proposal is available in `UX-06-ADMIN-SECURITY-EVENT-CATALOG.md`. It defines candidate mandatory transactional events, bounded security telemetry, privileged-read event candidates, common envelope, payload allowlists/prohibitions, read permissions and required acceptance evidence.

The catalog remains a proposal, not a final approved schema. PR-READ-01 is resolved: mass card lookup/search is excluded from AdminSecurityAudit, including large-result and cross-scope searches; successful business-data exports/downloads and report/manifest operations that export patient-identifying data remain in scope; exporting AdminSecurityAudit itself is prohibited. Retention/legal hold, digest/IP and HMAC lifecycle, export/access logging and tamper-evidence policy remain unresolved. No implementation, DDL or tests are authorized by this proposal.


## 12. Audit retention and integrity proposal (2026-11-19)

Technical proposal: `UX-06-AUDIT-RETENTION-INTEGRITY-PROPOSAL.md`. It separates retention classes without inventing periods; defines legal-hold and purge/backup reconciliation requirements; recommends no IP/identity digest by default; proposes per-event HMAC plus externally signed checkpoint manifests; and defines integrity and business-data export audit acceptance criteria. It does not approve durations, select infrastructure or authorize implementation.


## 13. Audit access and export policy (2026-11-19)

Proposal: `UX-06-AUDIT-ACCESS-AND-EXPORT-POLICY-PROPOSAL.md`. It specifies admin-only read, admin-only read access with no audit-feed export; ordinary feed/detail reads are not logged. Acceptance criteria cover server-side read-scope enforcement and non-admin denial. Audit-feed export is not a product feature and must not be implemented. Neither feed opening nor individual event-detail views are logged. Separate business-data export evidence remains governed by the event catalog.


## 37. UX-06 — экспорт AdminSecurityAudit разрешён всем admin с причиной (2026-11-19)

Владелец продукта утвердил вариант A: любой пользователь с существующей ролью `admin` может экспортировать саму ленту AdminSecurityAudit при обязательном непустом указании причины. Отдельная permission и подтверждение вторым admin не требуются. Экспорт должен быть ограничен по диапазону/объёму/полям, защищён, атрибутирован и иметь обязательную запись результата. Другие роли доступа к экспорту не получают. Просмотр деталей отдельных событий не журналируется. Осталось согласовать режим feed-open telemetry. Изменена только Markdown-документация; код, БД, миграции, тесты, workflows и deployment не менялись.


## 40. UX-06 — открытие глобальной ленты не журналируется (2026-11-19)

Владелец продукта ответил: нет, сам факт открытия общей ленты AdminSecurityAudit не регистрировать. Вместе с ранее утверждённым отказом регистрировать просмотр деталей отдельных событий это означает, что обычное чтение audit feed не создаёт access-log events. Журналирование остаётся для экспорта самой ленты (любой admin, обязательная причина, фиксация результата) и для успешных бизнес-экспортов/скачиваний персональных данных согласно каталогу. Отдельный AuditAccessLog, если будет реализован, содержит только факты запросов/завершения/скачивания экспорта, не feed-open и не detail-view. Изменена только Markdown-документация.


## Owner decision recorded — patient identifiers in audit-feed export (2026-11-19)

The owner explicitly permits patient-identifying data (including full names and medical-card numbers) in the exported AdminSecurityAudit feed. Treat this as a narrow export-schema exception, not permission to add these identifiers to ordinary audit event payloads or to export arbitrary JSON. Maintain admin-only access, mandatory reason, bounded export scope/volume, explicit column allowlist, protected actor-bound artifact, download-time authorization recheck and mandatory export/download evidence. Retention/legal hold/artifact expiry remain pre-production gates. No implementation authorization is implied.


## Owner decision — AdminSecurityAudit export is not required (2026-11-19)

The product owner explicitly confirmed that export of the AdminSecurityAudit feed is not needed at all. This is the current and controlling decision and supersedes all earlier readiness notes that approved admin audit-feed export, patient-identifying columns in that export, export artifacts, download authorization, export evidence and artifact TTL.

Readiness implications:
- Keep admin-only read access to the global AdminSecurityAudit feed and event details.
- Do not implement audit-feed export controls, endpoints, commands, permissions, files, artifacts, downloads or export-specific access logging.
- Do not create an AuditAccessLog solely to record audit-feed export activity.
- Keep ordinary feed opening and event-detail viewing unlogged, as already approved.
- Preserve the distinct audit requirements for successful business-data exports/downloads of patient-identifying data and qualifying reports/manifests. These are not exports of AdminSecurityAudit.
- Retention/legal hold, integrity evidence, and backup reconciliation for audit records remain independent open gates.

This is documentation-only. No source, schema, migrations, tests, workflows or deployment were changed or authorized.

### D-06 — окончательное решение по viewer (2026-11-21)

МедАрхив ещё не введён в эксплуатацию; пользовательских и операционных данных нет. Миграция существующих viewer accounts, сохранение legacy actor references, account mapping/disablement и legacy ACL compatibility не требуются. Роль `viewer` полностью исключается из целевой модели и будущей реализации во всех слоях: schema/enum/CHECK, ORM, API, admin service/forms, route guards, navigation, templates, background paths и tests. Целевые роли: `admin`, `branch`, `central_office`, `archive`. Не назначать права viewer другим ролям автоматически; применяются только отдельно утверждённые полномочия. Статически выявленные viewer search/history/dashboard пути — legacy remnants прототипа, подлежащие удалению при реализации; в частности, глобальный `/history` не должен остаться доступным ни одной роли как общий operational feed. D-06 закрыто как продуктовый/миграционный вопрос; удаление кода и обновление тестов остаются частью будущей реализации после отдельного разрешения. Никакие исходники, ORM, API/UI, миграции или тесты этим решением не изменяются.

### D-03 — окончательное решение по legacy incidents (2026-11-21)

МедАрхив ещё не введён в эксплуатацию; рабочих пользовательских и операционных данных нет. Старый процесс «Инциденты» полностью исключается из целевой системы. Не переносить `Shipment.status='exception'`, exception metadata, exception DomainEvent types, register/resolve commands, receive queues, admin incident pages/routes/API, labels или тестовые ожидания. Миграция/reconciliation/retention старых exception records не требуется. Не создавать новую сущность incident или отдельный incident workflow. Сохраняются общий append-only audit и согласованные способы обработки ошибок/расхождений внутри соответствующих бизнес-процессов; никаких частичных приёмок и молчаливых корректировок custody/location. Статические legacy findings остаются ориентиром для удаления путей при реализации. D-03 закрыто как продуктовый вопрос; реализация требует отдельного разрешения.


## 2026-11-21 — утверждены права чтения AdminSecurityAudit

Глобальный журнал `AdminSecurityAudit` и детали его событий доступны только активному пользователю с ролью `admin`. Роли `branch`, `central_office` и `archive` не получают глобальную ленту, payload событий или альтернативный доступ через прямой URL/API. Проверка выполняется сервером; скрытие элементов интерфейса не считается защитой. Администратор видит только специально разрешённые и allowlisted поля: секреты, пароли, токены и избыточные персональные данные не раскрываются. Открытие ленты и деталей не журналируется; экспорт самого `AdminSecurityAudit` остаётся запрещённым. Продуктовое решение закрыто; до реализации остаются проверка всех read surfaces/guards и негативные тесты по каждой роли. Реализация не разрешена.

## 2026-11-21 — утверждена governance-политика сроков хранения AdminSecurityAudit

Владелец продукта утвердил следующий подход к срокам хранения `AdminSecurityAudit`: конкретные периоды сейчас не устанавливаются, поскольку у организации пока нет утверждённого графика хранения. До ввода системы в эксплуатацию организация должна назначить ответственного за юридические требования, персональные данные и архивное хранение. Этот ответственный организует оценку и утверждает отдельную версионированную политику для каждого класса событий R1–R4. Политика должна определять сроки и начальные точки их исчисления, порядок контролируемого удаления, приостановку удаления при legal hold, периодический пересмотр/снятие hold, а также согласование удаления с резервными копиями и восстановлением. До утверждения такой политики автоматическое удаление записей аудита запрещено; произвольное бессрочное хранение и ad hoc удаление также не допускаются. Конкретные сроки и юридические основания остаются открытым организационным вопросом. Решение не разрешает реализацию или изменение БД.


## 2026-11-21 — logout telemetry approved

The product owner approved recording explicit user-initiated logout as a best-effort security event. Telemetry failure/unavailability must not delay or prevent logout or credential clearing; it is not a mandatory transactional audit write. No guarantee is made for browser close, connectivity loss, or other exits that do not invoke logout. No new session registry is implied. The product decision is closed; technical implementation must fit the bounded telemetry limits and must not create an availability dependency.


## 2026-11-21 — denial-telemetry grouping dimensions approved

For selected high-risk denials/threshold crossings, repeated events are grouped only by controlled operation type/code and denial-reason code. No actor, attempted-identity, source-IP, IP-digest or other per-user/network correlation key may be used. These dimensions are categorical and must not contain free-form request data. Product-level grouping dimensions are closed; implementation readiness still requires explicit windows, thresholds, maximum rate/volume, retention and unavailable-store behavior, while preserving best-effort non-blocking semantics.


## 2026-11-21 — denial telemetry aggregation window approved

The product owner approved a fixed 15-minute aggregation window for selected high-risk authorization-denial telemetry. Events are grouped within the window by controlled operation type/code plus denial-reason code, with an aggregate count instead of one durable row per repeated denial. Actor/attempted-identity/source-IP/IP-digest and other per-user/network correlation dimensions remain prohibited. Technical readiness still requires thresholds, maximum rate/volume, retention and unavailable-store behavior; telemetry remains best-effort and non-blocking.


## 2026-11-21 — denial telemetry threshold approved

The product owner approved a threshold of five matching denials within a fixed 15-minute aggregation window. Matching uses only controlled operation type/code plus denial-reason code. At the fifth denial, one aggregate event is recorded for that group/window, not one durable event per attempt. No actor/attempted-identity/source-IP/IP-digest or other per-user/network correlation key is permitted. Counter update/finalization mechanics and maximum rate/volume remain technical parameters. Best-effort, non-blocking behavior remains mandatory; telemetry failure must not alter the authorization result.


## 2026-11-21 — denial telemetry hourly cap approved

The product owner approved an application-wide cap of 100 persisted aggregate denial-telemetry events per hour. After the cap is reached, further aggregates are dropped for the remainder of that hour. This is limited to best-effort security telemetry and must never block/delay business operations or alter authorization results. Existing decisions remain: 15-minute aggregation window, threshold of five matching denials, grouping only by controlled operation type/code and denial-reason code, and no per-user/network correlation keys. Concurrency-safe enforcement and hour-boundary mechanics remain technical implementation details.


## 2026-11-21 — denial telemetry retention approved

The product owner approved 30 calendar days of retention for bounded aggregate authorization-denial telemetry, followed by automatic deletion. This is distinct from AdminSecurityAudit, DomainEvent and authoritative business-record retention policies. Implementation must use bounded cleanup that does not block or change request processing. Approved parameters now include: 15-minute aggregation window, five matching denials per group/window, maximum 100 aggregate events application-wide per hour, and 30-calendar-day retention.


## 2026-11-21 — denial telemetry unavailable-store behavior approved

The product owner approved drop-on-failure behavior for optional denial telemetry: when the telemetry store is unavailable or a write fails, do not retry and do not create a deferred queue; drop the event. Request processing continues without delay, authorization/business-operation outcomes remain unchanged, and internal technical details are not exposed to users. This applies only to best-effort denial telemetry and does not alter mandatory atomic DomainEvent/AdminSecurityAudit write requirements.


## 2026-11-21 — telemetry-loss monitoring approved

The product owner approved no separate user-interface notification when optional denial telemetry is dropped or its store is unavailable. Operational monitoring must track store availability and telemetry write failures through technical health signals only, without personal data. It must remain separate from AdminSecurityAudit and must not create a request-path dependency or block/change authorization or business-operation outcomes. No retry or deferred queue is introduced. Implementation remains unauthorized.

## 2026-11-21 — operational telemetry monitoring access approved

The product owner approved restricting technical monitoring metrics and telemetry-store/write-failure diagnostics to infrastructure administrators and personnel explicitly responsible for system operations. Ordinary MedArchive users, including application-role `admin`, do not receive access to internal infrastructure metrics or telemetry diagnostics through the application. This is separate from application-role permissions and AdminSecurityAudit. Exact infrastructure identities/grants remain deployment-specific. Implementation remains unauthorized.

## 2026-11-21 — technical monitoring access-view audit boundary approved

The product owner approved that views of technical monitoring metrics are not written to MedArchive `AdminSecurityAudit`. Monitoring access is controlled by infrastructure mechanisms. Any access log required by the infrastructure is a separate infrastructure/operations record, not part of MedArchive's business/security audit. This does not create an application audit event or change the approved monitoring access boundary. Implementation remains unauthorized.

## 2026-11-21 — no telemetry-outage notifications approved

The product owner decided not to notify infrastructure/operations personnel about denial-telemetry store unavailability, including prolonged outages. Passive technical health metrics/diagnostics may still be available to authorized infrastructure operators, but no alert/notification workflow is required for this condition. Drop-on-failure, no retry/deferred queue, and non-blocking request behavior remain unchanged.

## 2026-11-21 — telemetry health is not exposed in the application UI

The product owner decided that denial-telemetry storage availability, health, write failures, and diagnostics must not be displayed anywhere in the MedArchive application UI, including administrative screens. Any passive technical health information remains outside the application and within the separately controlled infrastructure/operations monitoring boundary. The application `admin` role does not grant access to it.

## 2026-11-21 — separate AdminSecurityAudit integrity verification not required

The product owner decided that the target product does not need a separate feature to verify cryptographic integrity of `AdminSecurityAudit`. Do not design or implement hash chains, MACs, signatures, checkpoints, verification jobs, integrity dashboards, or related UI/API solely for this purpose. Existing append-only controls, restricted write permissions, mandatory atomic audit writes, retention governance, and backup/restore procedures remain independent requirements. This decision does not authorize weakening those controls. Key custody/rotation remains deferred.

## 2026-11-21 — failed-login audit/telemetry excluded

The product owner decided not to persist individual failed login attempts or login-throttle denials in `AdminSecurityAudit`, `DomainEvent`, or bounded security telemetry. Candidate event types `authn.login_failed` and `authn.login_throttled` are excluded from the target catalog. Login throttling itself remains active and may retain only the internal counters/state necessary for its separate control contract; these are not audit/telemetry events, and bucket keys must not be copied into audit records. Successful-login audit and selected high-risk authorization-denial telemetry remain governed by their separate approved rules.

## 2026-11-21 — ordinary session expiration is not audited

The product owner decided that normal session expiration due to timeout/expiry does not create an `AdminSecurityAudit`, `DomainEvent`, or bounded security-telemetry record. No session-expiration event or session registry is to be introduced solely for this purpose. Successful login and explicit user-initiated logout retain their previously approved handling. This decision concerns ordinary expiration, not a new policy for rejected requests using expired/invalid sessions.

## 2026-11-21 — no per-session event for administrative invalidation

When an account is deactivated or otherwise administratively disabled, the account-management action is recorded under the existing mandatory `AdminSecurityAudit` contract. No separate audit, domain, or telemetry event is created for each session consequently invalidated/terminated. Existing decisions for successful login, explicit logout, and ordinary session expiration remain unchanged.

## 2026-11-21 — успешное самостоятельное изменение пароля

Владелец продукта утвердил регистрацию успешного самостоятельного изменения пароля сотрудником как отдельного события безопасности `authn.password_changed` в `AdminSecurityAudit`. В событии допускаются только необходимые безопасные метаданные; пароль, его хеш, reset/recovery secrets, токены и иные секретные данные не сохраняются. Неудачные попытки самостоятельного изменения пароля отдельно не журналируются. Это решение не меняет ранее утверждённое исключение неудачных попыток входа и отказов login throttle. Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — атомарность самостоятельного изменения пароля и аудита

Владелец продукта утвердил применение общего правила обязательной атомарной записи к самостоятельному изменению пароля: изменение пароля и обязательная запись `AdminSecurityAudit` с событием `authn.password_changed` выполняются в одной транзакции. Если запись события не удаётся, вся операция изменения пароля откатывается, новый пароль не вступает в силу, а прежний пароль остаётся действующим. Событие не должно записываться отдельно после успешного изменения пароля. Это решение не меняет исключение неудачных попыток смены пароля из журналирования. Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — аудит успешного административного сброса пароля

Владелец продукта утвердил регистрацию успешного административного сброса/замены учётных данных отдельным обязательным событием `admin.credential_reset` в `AdminSecurityAudit`. Событие содержит только идентификатор целевой учётной записи и безопасный код способа сброса. Пароли, их хеши, reset/recovery secrets, токены и иные секретные данные запрещено сохранять. Неудачные попытки административного сброса отдельно не журналируются. Запись события и изменение учётных данных выполняются атомарно по общему правилу обязательного аудита: при ошибке записи вся операция откатывается. Это согласуется с уже существующей строкой каталога `admin.credential_reset`; данное решение уточняет её payload и поведение. Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — завершение активных сессий при смене/сбросе пароля

Владелец продукта утвердил следующие правила:
- При успешной самостоятельной смене пароля все остальные активные сессии этой учётной записи должны стать недействительными; текущая сессия, в которой выполнена смена, сохраняется.
- При успешном административном сбросе пароля все активные сессии целевой учётной записи должны стать недействительными.
- Изменение пароля, обязательная запись соответствующего `AdminSecurityAudit` event и применение инвалидации сессий должны иметь согласованную атомарную семантику: операция не считается успешной, пока обязательные изменения и аудит не зафиксированы. Истечение/инвалидация сессий не создаёт отдельные события на каждую сессию.
- События `authn.password_changed` и `admin.credential_reset` остаются единственными обязательными audit-событиями для соответствующих операций; отдельное событие на каждую завершённую сессию не вводится.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — подтверждение действующего пароля при самостоятельной смене

Владелец продукта утвердил следующие правила:
- При самостоятельной смене пароля сотрудник обязан подтвердить операцию вводом действующего пароля.
- При административном сбросе пароля действующий пароль целевой учётной записи не требуется; это отдельная привилегированная операция.
- Эти правила не меняют ранее согласованные требования к обязательному аудиту, атомарности изменения учётных данных и инвалидации остальных активных сессий.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — повторная проверка пароля администратора при сбросе чужого пароля

Владелец продукта утвердил требование: непосредственно перед выполнением административного сброса пароля другого сотрудника администратор должен повторно подтвердить свою личность вводом собственного действующего пароля. Это дополнительная проверка для привилегированной операции и не заменяет обычную серверную проверку активной учётной записи, роли и полномочий администратора. Действующий пароль целевой учётной записи не требуется. Повторная проверка не меняет ранее согласованные правила обязательной атомарной записи `admin.credential_reset` и инвалидации всех активных сессий целевой учётной записи.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — ограничение неудачных повторных подтверждений пароля администратора

Владелец продукта утвердил применение существующего механизма защиты от частых попыток входа к неудачным повторным подтверждениям пароля администратора перед административным сбросом пароля другого сотрудника. Отдельный механизм блокировки/счётчиков для этой операции не создаётся. Неудачные повторные подтверждения не записываются в `AdminSecurityAudit`, `DomainEvent` или bounded security telemetry как отдельные события; действующие ограничения на сохранение неудачных попыток входа остаются в силе. Успешный административный сброс продолжает фиксироваться обязательным событием `admin.credential_reset` с ранее утверждённой атомарностью и инвалидацией активных сессий целевой учётной записи.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — fail-closed инвалидация сессий при административном сбросе пароля

Владелец продукта утвердил строгое fail-closed поведение административного сброса пароля: операция считается успешной только если все активные сессии целевой учётной записи успешно сделаны недействительными. Если инвалидация хотя бы одной активной сессии не удалась, административный сброс не завершается успешно: изменение пароля и обязательная запись `admin.credential_reset` должны быть отменены/откачены в рамках согласованной атомарной операции. Нельзя оставлять новое значение пароля действующим при сохранении хотя бы одной прежней активной сессии. Требование дополняет ранее утверждённое обязательное завершение всех сессий целевой учётной записи и повторное подтверждение собственного пароля администратора.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — уведомление сотрудника об административном сбросе пароля

Владелец продукта утвердил уведомление целевого сотрудника об успешном административном сбросе пароля при его следующем успешном входе в МедАрхив. Уведомление должно сообщать только факт административного сброса; личность администратора не раскрывается. Внешняя отправка email/SMS и иные внешние каналы уведомления не вводятся. Уведомление не заменяет обязательное событие `admin.credential_reset` в `AdminSecurityAudit` и не меняет атомарность сброса, обязательную инвалидацию всех активных сессий и fail-closed поведение при ошибке инвалидации.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — обязательная смена пароля после административного сброса

Владелец продукта утвердил обязательную смену пароля целевым сотрудником после административного сброса. При следующем успешном прохождении аутентификации сотрудник должен сначала установить собственный новый пароль; до завершения этой процедуры доступ к остальным функциям МедАрхива не предоставляется. Уведомление о факте административного сброса показывается в рамках этого следующего входа; личность администратора не раскрывается, внешние каналы уведомления не используются. Установка нового пароля выполняется по правилам самостоятельной смены: сотрудник задаёт собственный пароль, но повторно вводить сброшенный административный пароль как действующий пароль не требуется. После успешной установки нового пароля обязательное событие `authn.password_changed` записывается атомарно с изменением пароля; при ошибке обязательной записи операция смены пароля откатывается, а ограниченный режим до завершения смены сохраняется. Это не отменяет обязательную инвалидацию всех прежних сессий при административном сбросе.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — срок действия административного временного пароля

Владелец продукта утвердил, что для временного пароля, установленного администратором, отдельный срок действия не задаётся. После успешной аутентификации с таким паролем сотрудник остаётся в ограниченном режиме: доступ к остальным функциям МедАрхива закрыт до самостоятельной установки нового пароля. Для завершения обязательной смены сотрудник не должен повторно вводить административный временный пароль как действующий пароль. Временный пароль передаётся сотруднику безопасным способом вне МедАрхива; внешняя доставка самим приложением не вводится. При успешной смене обязательно атомарно записывается `authn.password_changed`; при ошибке записи смена откатывается и ограниченный режим сохраняется.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — генерация административного временного пароля

Владелец продукта утвердил автоматическую генерацию временного пароля системой при административном сбросе. Администратор не задаёт временный пароль вручную. Значение генерируется криптографически стойким случайным способом, показывается администратору только один раз и не может быть повторно просмотрено через интерфейс или API. В хранилище учётных данных сохраняется только парольный хеш; открытое значение временного пароля не записывается в журналы, события, telemetry или иные постоянные хранилища МедАрхива. Передача временного пароля сотруднику выполняется безопасным способом вне МедАрхива. После входа с временным паролем сотрудник остаётся в ограниченном режиме до самостоятельной установки нового пароля; отдельный срок действия временного пароля не устанавливается. Все ранее утверждённые требования к аудиту, инвалидации сессий и обязательной смене пароля сохраняются.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — утраченный или недоступный для повторного просмотра временный пароль

Владелец продукта утвердил, что открытое значение временного пароля после его однократного показа администратору повторно не отображается и не восстанавливается. Если администратор потерял пароль или закрыл страницу до его передачи сотруднику, он выполняет новый административный сброс. Новый сброс генерирует новый временный пароль и делает предыдущий временный пароль недействительным. Все активные сессии целевой учётной записи при каждом успешном сбросе подлежат инвалидации в рамках обязательной атомарной операции; если инвалидация хотя бы одной сессии не удалась, сброс и обязательная запись аудита откатываются. Каждый успешно завершённый повторный сброс регистрируется отдельным событием `admin.credential_reset` без включения пароля, его хеша или иных секретов в payload. Хранение открытого временного пароля для повторного просмотра запрещено.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — уведомление после повторного административного сброса

Владелец продукта утвердил, что после повторного административного сброса сотруднику при следующем успешном входе показывается одно уведомление о последнем успешном сбросе. Уведомление не раскрывает историю предыдущих сбросов и личность администратора. Несколько последовательных сбросов до следующего входа не приводят к показу списка или серии уведомлений: показывается только факт последнего сброса. Это правило дополняет ранее утверждённое уведомление о факте административного сброса и обязательную смену временного пароля; внешние каналы уведомления не вводятся.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — запрет административного сброса собственного пароля

Владелец продукта утвердил, что административный механизм сброса пароля нельзя использовать для собственной учётной записи администратора. Администратор изменяет собственный пароль только через обычную процедуру самостоятельной смены с обязательным подтверждением действующего пароля и атомарной записью события `authn.password_changed`. Привилегированная операция `admin.credential_reset` доступна только для другой целевой учётной записи. Это ограничение не вводит отдельную процедуру восстановления доступа; recovery/break-glass остаётся отдельным ранее выделенным процессом.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — административный сброс пароля другой учётной записи администратора

Владелец продукта утвердил, что активный администратор может выполнить административный сброс пароля другой активной учётной записи администратора. Запрет административного сброса собственной учётной записи сохраняется. Следовательно, административный сброс пароля другого администратора допускается только при условии, что инициатор и целевая учётная запись различны и обе активны; инициатор проходит повторное подтверждение собственного действующего пароля и все общие проверки полномочий. Сохраняются автоматическая генерация временного пароля, однократный показ, обязательная инвалидация всех активных сессий целевой учётной записи, уведомление о последнем сбросе при следующем входе, обязательная смена временного пароля и атомарная запись `admin.credential_reset`. Никаких специальных исключений из общих правил безопасности для учётных записей администраторов не вводится.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — аварийное восстановление доступа единственного активного администратора

Владелец продукта утвердил отдельную аварийную процедуру (*break-glass*) на случай, когда в организации остаётся только один активный администратор и он теряет доступ к своей учётной записи. Восстановление выполняет уполномоченный инфраструктурный специалист вне приложения МедАрхив. Обычный административный сброс через интерфейс не расширяется и не получает исключения для сброса собственного пароля. Действия аварийной процедуры подлежат обязательной фиксации; конкретный состав доказательств, место и формат журналирования, порядок подтверждения полномочий и операционный runbook должны быть определены в отдельной эксплуатационной спецификации до ввода в эксплуатацию. Процедура не должна превращаться в постоянный HTTP/API endpoint или штатный пользовательский workflow.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — создание учётной записи другого администратора

Владелец продукта утвердил возможность создания активным администратором учётной записи другого администратора. Создаваемая учётная запись не получает активный статус до завершения предусмотренной процедуры первоначальной активации. Факт создания фиксируется в `AdminSecurityAudit` обязательной записью в рамках атомарной операции. Создатель не может использовать эту операцию для назначения себе дополнительных полномочий или изменения собственной роли. Самостоятельное повышение привилегий через создание/изменение учётной записи запрещено. Конкретные шаги первоначальной активации и допустимый способ первоначальной передачи секрета должны быть определены в технической спецификации до реализации; секреты не включаются в аудит.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — деактивация другой учётной записи администратора

Владелец продукта утвердил, что активный администратор может деактивировать учётную запись другого администратора. Деактивация собственной учётной записи через эту операцию запрещена. Нельзя деактивировать последнюю активную учётную запись администратора организации: перед такой деактивацией должна быть создана и активирована другая учётная запись администратора. Проверка наличия другой активной учётной записи должна выполняться атомарно с деактивацией, чтобы параллельные административные операции не могли оставить организацию без активного администратора. Действуют общие правила деактивации: обязательная запись действия в `AdminSecurityAudit`, аннулирование активных сессий целевой учётной записи и атомарный откат всей операции при невозможности выполнить обязательные действия. Отдельные события на каждую аннулированную сессию не создаются.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — изменение роли другого сотрудника

Владелец продукта утвердил возможность активного администратора изменять роль другого сотрудника в пределах закрытого набора ролей `branch`, `central_office`, `archive` и `admin`. Изменение собственной роли через эту операцию запрещено. Назначение роли `admin` требует повторного подтверждения действующего пароля инициатора. Проверка того, что операция не оставит организацию без активного администратора, выполняется атомарно с изменением роли; нельзя понизить или лишить активности последнюю действующую административную учётную запись так, чтобы в организации не осталось ни одного активного администратора. Факт успешного изменения роли обязательно фиксируется событием `admin.user_role_changed` в `AdminSecurityAudit` в одной транзакции с изменением. При невозможности обязательной записи аудита или выполнения защитных проверок вся операция откатывается. Это решение не разрешает самоназначение роли `admin` или обход отдельного запрета на изменение собственной роли.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — изменение организационной привязки сотрудника

Владелец продукта утвердил возможность активному администратору изменять организационную привязку другого сотрудника: переводить его между филиалами, центральным офисом и архивом в соответствии с назначенной ролью. Новая привязка должна ссылаться на существующую и активную организационную единицу допустимого типа; несовместимая комбинация роли и организационной области отклоняется. Изменение организационной привязки обязательно фиксируется в `AdminSecurityAudit` с идентификатором целевой учётной записи и безопасными кодами прежней и новой привязки. Изменение и обязательная запись аудита выполняются атомарно; при ошибке проверки или записи аудита операция полностью откатывается. Это решение не меняет роль сотрудника и не предоставляет дополнительных полномочий само по себе. Детальные ограничения на перевод сотрудника с незавершёнными операциями/процессами должны быть учтены в техническом контракте до реализации.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — редактирование персональных данных сотрудника администратором

Владелец продукта утвердил возможность активному администратору редактировать профиль другого сотрудника, включая ФИО и контактные данные. Изменение собственного профиля администратором через эту административную операцию запрещено. Пароль и любые учётные секреты не являются редактируемыми полями профиля и изменяются только через предусмотренные процедуры управления учётными данными. Изменение профиля другого сотрудника обязательно фиксируется в `AdminSecurityAudit`; запись должна содержать идентификатор целевой учётной записи и безопасное описание изменённых категорий полей, без включения прежних/новых значений персональных данных и любых секретов. Изменение профиля и обязательная запись аудита выполняются атомарно; при ошибке проверки или записи аудита вся операция откатывается. Допустимые поля профиля, ограничения на их формат и точный allowlist категорий для аудита определяются в техническом контракте до реализации.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — запрет самостоятельного редактирования профиля сотрудником

Владелец продукта отклонил возможность самостоятельного редактирования сотрудником персональных данных своего профиля. Изменение ФИО и контактных данных выполняется только активным администратором в отношении другого сотрудника по утверждённой административной процедуре. Самостоятельное изменение этих полей через профиль, API или иные пользовательские поверхности не допускается. Ранее утверждённые ограничения сохраняются: администратор не редактирует собственный профиль этой операцией; пароли и учётные секреты изменяются только через отдельные процедуры управления учётными данными.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — неизменяемость логина сотрудника

Владелец продукта утвердил запрет на изменение логина после создания учётной записи. Если требуется другой логин, активный администратор создаёт новую учётную запись и деактивирует прежнюю с использованием предусмотренных процедур управления учётными записями. Исторические действия и записи аудита остаются связанными с исходной учётной записью; автоматическое объединение учётных записей, перепривязка истории или перенос авторства на новую учётную запись не выполняются. Новая учётная запись проходит обычную процедуру первоначальной активации и получает права только в соответствии с отдельно назначенными ролью и организационной привязкой. До реализации необходимо учесть незавершённые операции и процессы старой учётной записи в техническом контракте деактивации.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — отсутствие email-адресов сотрудников в профиле

Владелец продукта подтвердил, что адреса электронной почты сотрудников не нужны и не должны храниться в профиле МедАрхива. Поле email сотрудника не входит в целевую модель профиля; администратор не может добавлять или редактировать такой адрес. Email не используется для уведомлений, подтверждения личности или восстановления доступа. Внешние email-уведомления не вводятся этим решением.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — утверждённый состав профиля сотрудника

Владелец продукта утвердил закрытый состав данных профиля сотрудника МедАрхива: ФИО, логин, роль, организационная привязка и статус учётной записи (активна/деактивирована). Иные персональные и контактные поля в профиль не входят. В частности, номера телефонов и адреса электронной почты сотрудников не хранятся. Логин неизменяем после создания учётной записи. ФИО и контактные данные не редактируются самим сотрудником; при этом контактные данные в целевой модели отсутствуют. Административные изменения разрешены только в пределах ранее утверждённых полномочий: администратор может изменять ФИО, роль и организационную привязку другого сотрудника, а также управлять статусом учётной записи через отдельные процедуры. Администратор не может изменять собственный профиль этой административной операцией. Пароли и учётные секреты не являются полями профиля и управляются только отдельными процедурами. Перечень полей и операций должен быть отражён в целевой модели данных, API-контрактах, UI и проверках доступа до реализации.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — фотография сотрудника не входит в профиль

Владелец продукта подтвердил, что фотография сотрудника в МедАрхиве не нужна. Фотоизображения сотрудников не собираются и не хранятся; поле фотографии, загрузка/замена изображения и связанные с ними API, UI и процессы в целевую модель не входят. Состав профиля остаётся закрытым: ФИО, неизменяемый логин, роль, организационная привязка и статус учётной записи. Самостоятельное редактирование профиля запрещено; изменения ФИО, роли и организационной привязки выполняются администратором в пределах ранее согласованных полномочий.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — деактивация сотрудника при наличии незавершённых операций

Владелец продукта утвердил, что наличие незавершённых операций не препятствует деактивации учётной записи сотрудника. Деактивация не удаляет и автоматически не отменяет незавершённые операции; история действий сотрудника сохраняется. Дальнейшая судьба и продолжение таких операций определяются правилами соответствующего бизнес-процесса. После деактивации сотрудник теряет доступ к системе; продолжение допустимых операций выполняют другие уполномоченные активные сотрудники в соответствии с их ролью, организационной областью и правилами конкретного процесса. До реализации необходимо определить технические ограничения и безопасную передачу ответственности за операции, для которых требуется участие или авторство сотрудника.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — передача незавершённых операций деактивированного сотрудника

Владелец продукта утвердил, что при деактивации сотрудника незавершённые операции не переназначаются автоматически другому сотруднику. Другой активный и уполномоченный сотрудник может принять такую операцию в работу только предусмотренным правилами соответствующего бизнес-процесса способом. Первоначальный автор, история действий и фактические участники сохраняются; принятие операции другим сотрудником не переписывает авторство и не переносит исторические действия. Необходимо предусмотреть явную фиксацию фактического продолжения/принятия ответственности там, где это требуется контрактом конкретного процесса.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — сохранение учётной записи после деактивации

Владелец продукта утвердил, что запись учётной записи деактивированного сотрудника сохраняется в МедАрхиве бессрочно и не удаляется штатными операциями. Сохраняются логин, ФИО, идентификатор учётной записи и связи с историческими действиями, бизнес-операциями и аудитом. Деактивация прекращает доступ, но не удаляет историческую идентичность и авторство. Повторная активация допускается только по административной процедуре уполномоченным активным администратором с соблюдением действующих проверок роли, организационной привязки и наличия активного администратора. Правовые основания и применимость сроков хранения персональных данных должны быть подтверждены организацией до production; утверждение о бессрочном хранении является целевым правилом продукта и не отменяет обязательные требования законодательства.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — повторная активация деактивированной учётной записи

Владелец продукта утвердил возможность повторной активации ранее деактивированной учётной записи активным администратором. При повторной активации используется прежняя учётная запись и неизменяемый логин; создание новой учётной записи только ради возвращения сотрудника не требуется. Сохраняются прежний идентификатор учётной записи, исторические действия, авторство и связи с аудитом. До активации администратор должен назначить/проверить актуальные роль и организационную привязку, а система — проверить их допустимость, отсутствие конфликта идентичности и соблюдение инварианта наличия хотя бы одного активного администратора. Активация и обязательная запись события AdminSecurityAudit выполняются атомарно; доступ предоставляется только после успешного commit. Конкретные коды события и технический контракт определяются в спецификации управления учётными записями.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — обязательная причина деактивации учётной записи

Владелец продукта утвердил обязательное указание причины при административной деактивации учётной записи сотрудника. Причина выбирается из закрытого контролируемого перечня; предварительные категории: увольнение, длительное отсутствие, организационные изменения, другое. Свободный комментарий остаётся необязательным. Выбранный код причины фиксируется в обязательной записи AdminSecurityAudit; произвольный текст комментария не должен попадать в структурированные поля события и подлежит отдельному ограничению/санитизации в техническом контракте. Точный закрытый перечень кодов и правила комментария уточняются в технической спецификации до реализации. Деактивация и обязательная запись аудита остаются атомарными.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — отдельный журнал истории изменений профиля сотрудника не создаётся

Владелец продукта подтвердил, что отдельный журнал истории изменений данных сотрудника не нужен. Не вводить отдельную таблицу, журнал, ленту или самостоятельный интерфейс истории профиля. Административные изменения ФИО, роли и организационной привязки фиксируются в существующем AdminSecurityAudit в рамках ранее утверждённых правил; отдельное дублирование истории не создаётся.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — просмотр истории учётной записи из её карточки

Владелец продукта утвердил возможность для активного администратора просматривать из карточки учётной записи историю относящихся к ней изменений и административных действий. Источник данных — существующий AdminSecurityAudit; отдельный журнал, таблица или самостоятельная система истории не создаются. Представление ограничивается событиями, связанными с выбранной учётной записью, и теми полями, которые разрешены действующим allowlist для просмотра аудита. Доступ проверяется на сервере для каждого запроса до формирования ответа; остальные роли не получают эту историю ни через UI, ни через прямые URL/API. Это представление не расширяет общие права чтения AdminSecurityAudit, не разрешает экспорт аудита и не раскрывает секреты или избыточные персональные данные. Просмотр истории не создаёт отдельного audit event.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — отображение исполнителя в истории учётной записи

Владелец продукта утвердил отображение в истории учётной записи сведений о том, кто выполнил административное действие. Для каждого отображаемого события показываются ФИО инициатора (на момент события, если это значение доступно в сохранённом событии/аудитном представлении), дата и время, тип действия и результат. Идентификатор учётной записи инициатора сохраняется как надёжная внутренняя связь; отображаемое имя не должно подменять историческое авторство. Если историческое ФИО не зафиксировано, интерфейс не должен угадывать его по текущему профилю; следует показать доступное безопасное обозначение инициатора. Представление остаётся доступным только активному администратору, использует существующий AdminSecurityAudit и его allowlist, не раскрывает секреты/избыточные PII и не разрешает экспорт аудита. Точный состав полей события и поведение для старых записей без snapshot имени уточняются в технической спецификации.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — фильтрация истории учётной записи по типу действия

Владелец продукта утвердил фильтрацию истории учётной записи по типу административного действия. Активный администратор может ограничить отображаемый список выбранным типом события (например, изменение роли, сброс пароля, деактивация или повторная активация). Фильтрация применяется только к уже разрешённым для просмотра событиям выбранной учётной записи и не расширяет права доступа, состав доступных полей или область AdminSecurityAudit. Фильтрация выполняется на сервере после проверки полномочий и до формирования результата; количество, наличие и значения недоступных событий не должны раскрываться. Отдельный журнал, экспорт или аудит самого факта фильтрации не вводятся.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — фильтрация истории учётной записи по периоду

Владелец продукта утвердил возможность фильтровать историю учётной записи по временному диапазону: дата/время начала и дата/время окончания. Диапазон применяется совместно с фильтром типа действия и только к событиям, уже разрешённым активному администратору для выбранной учётной записи. Проверка полномочий и ограничение области данных выполняются на сервере до формирования результата; временной фильтр не должен раскрывать наличие или количество недоступных событий. Границы диапазона и отображение времени используют согласованные для МедАрхива правила хранения timestamp и представления локального времени; точные технические границы включения начала/окончания закрепляются в API/UX-спецификации. Отдельный журнал или аудит применения фильтра не вводится.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — просмотр полной истории учётной записи за всё время

Владелец продукта утвердил, что активный администратор может просматривать полную историю относящихся к выбранной учётной записи событий AdminSecurityAudit за всё время, без обязательного ограничения временным диапазоном. Фильтр периода остаётся доступным как дополнительное сужение результатов и может применяться совместно с фильтром типа действия; отсутствие заданного периода означает просмотр всей доступной истории. Представление по умолчанию может открываться с последними событиями для удобства, но администратор должен иметь явную возможность перейти ко всей истории. Доступ по-прежнему ограничен активными администраторами, только событиями выбранной учётной записи и разрешёнными allowlist полями; серверная проверка полномочий и области данных выполняется до формирования результата. Это не разрешает экспорт AdminSecurityAudit, не создаёт отдельный журнал и не требует аудита самого просмотра или применения фильтров.

## 2026-11-21 — деактивация организационной единицы

Владелец продукта утвердил порядок деактивации филиала или иной организационной единицы. Деактивация запрещена, пока к единице привязаны активные учётные записи сотрудников или остаются незавершённые операции. До деактивации сотрудники должны быть переведены на допустимые активные организационные привязки, а незавершённые операции — завершены либо обработаны предусмотренным соответствующим бизнес-процессом способом; автоматическое удаление или произвольная отмена операций не допускаются. После выполнения предварительных условий активный администратор может деактивировать организационную единицу. Исторические данные, события, связи и авторство сохраняются; физическое удаление организационной единицы штатными операциями запрещено. Деактивация и обязательная запись AdminSecurityAudit выполняются атомарно. Проверки должны учитывать конкурентные изменения сотрудников и операций, чтобы нельзя было обойти условия деактивации.


## 2026-11-21 — повторная активация организационной единицы

Владелец продукта утвердил возможность повторной активации ранее деактивированного филиала или другой организационной единицы уполномоченным активным администратором. Повторная активация выполняется для прежней записи с сохранением прежнего идентификатора; создавать новую организационную единицу только ради возобновления работы не требуется. Исторические данные, связи, события и авторство сохраняются и продолжают относиться к той же организационной единице. До активации проверяются актуальные параметры и допустимость организационной единицы, а также действующие ограничения доступа и целостности. Активация и обязательная запись AdminSecurityAudit выполняются атомарно. Конкретный технический контракт и состав проверок закрепляются в спецификации управления справочными данными.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — изменение названия организационной единицы

Владелец продукта утвердил возможность изменения названия филиала или другой организационной единицы уполномоченным активным администратором без создания новой записи. При переименовании сохраняются прежний идентификатор, связи, история, события и авторство; переименование не считается созданием новой организационной единицы и не меняет её идентичность. В исторических представлениях должно использоваться название, зафиксированное на момент события, если такой snapshot предусмотрен и доступен; текущее название не должно выдаваться за историческое. Изменение названия и обязательная запись AdminSecurityAudit выполняются атомарно. Технические ограничения формата/уникальности названий и способ хранения исторического отображаемого значения определяются в спецификации справочных данных.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — перевод сотрудника в другую организационную единицу

Владелец продукта утвердил возможность перевода сотрудника в другую активную организационную единицу уполномоченным активным администратором. При переводе сохраняются прежняя учётная запись, логин, идентификатор и вся история действий; создавать новую учётную запись не требуется. Новая организационная привязка применяется к последующим операциям после успешного завершения перевода. Исторические действия и операции сохраняют сведения об организационной области, действовавшей на момент их выполнения; перевод не переписывает прошлое авторство или область ответственности. Перевод и обязательная запись AdminSecurityAudit выполняются атомарно. Перевод должен учитывать ранее утверждённые ограничения на деактивацию организационной единицы и правила продолжения незавершённых операций. Технический контракт и проверки допустимости новой привязки определяются в спецификации управления учётными записями.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — просмотр истории организационных переводов сотрудника

Владелец продукта утвердил просмотр истории организационных переводов сотрудника из его карточки активным администратором. История формируется на основании существующего AdminSecurityAudit; отдельный журнал или таблица переводов не создаются. Для доступных событий отображаются прежняя и новая организационная привязка, дата и время перевода, а также ФИО инициатора на момент события, если оно было сохранено. Исторические значения не подменяются текущими данными справочника или профиля сотрудника; если историческое значение недоступно, интерфейс показывает безопасное обозначение и не пытается его восстановить по текущему состоянию. Доступ ограничен активными администраторами и проверяется на сервере до формирования результата. Просмотр истории переводов не расширяет права на глобальный AdminSecurityAudit, не разрешает его экспорт и сам по себе не журналируется. Состав полей, необходимых для отображения исторических организационных значений, закрепляется в технической спецификации.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — деактивация курьера

Владелец продукта утвердил возможность деактивации курьера уполномоченным активным администратором, если курьер больше не должен назначаться на новые отправления. После деактивации курьер недоступен для выбора при создании или изменении назначения курьера в новых отправлениях; уже сохранённые отправления и исторические доставки не переписываются. Исторические связи, сведения о курьере, авторство/факт назначения и события сохраняются, чтобы прежние отправления оставались корректно читаемыми. Деактивация не должна автоматически отменять, переназначать или изменять отправления, в которых курьер уже указан; их дальнейшее выполнение определяется правилами соответствующего процесса. Деактивация и обязательная запись AdminSecurityAudit выполняются атомарно. Физическое удаление курьера штатными операциями не допускается. Точные ограничения для незавершённых отправлений определяются технической спецификацией без потери исторических данных.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — повторная активация курьера

Владелец продукта утвердил возможность повторной активации ранее деактивированного курьера уполномоченным активным администратором. Повторно используется прежняя запись курьера и её идентификатор; создание дубликата не требуется. После успешной активации курьер снова доступен для назначения на новые отправления в соответствии с действующими правилами процесса. Исторические отправления, доставки, назначения и события сохраняются без переписывания. Активация и обязательная запись AdminSecurityAudit выполняются атомарно. Проверки допустимости активации и актуальности записи выполняются на серверной стороне.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — минимальный состав данных курьера

Владелец продукта утвердил минимальный состав данных курьера: ФИО и статус (активен / деактивирован). Идентификатор записи используется системой технически. Телефон, email, фотография и иные дополнительные персональные сведения о курьере не собираются и не хранятся; соответствующие поля, формы ввода, загрузки и связанные интерфейсы/API не предусматриваются. Сведения о курьере, необходимые для отображения исторических отправлений и доставок, сохраняются согласно правилам хранения исторических записей.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — управление карточками курьеров

Владелец продукта утвердил, что создавать и редактировать карточки курьеров может только активный администратор. Сотрудники филиалов, центрального офиса и архива не управляют карточками курьеров; в рамках своих разрешённых процессов они могут выбирать активного курьера при оформлении отправления. Это правило действует совместно с утверждённым минимальным составом карточки (ФИО и статус; технический идентификатор используется системой), запретом дополнительных персональных данных, деактивацией и повторной активацией курьера. Изменения карточек выполняются с обязательной атомарной записью в AdminSecurityAudit. Реализация не разрешена.

## 2026-11-21 — завершение отправления деактивированным курьером

Владелец продукта утвердил, что деактивация курьера не препятствует завершению уже назначенного ему отправления. Деактивация запрещает новые назначения курьера, но не отменяет и не переназначает автоматически отправления, назначенные до деактивации. Курьер может завершить такое отправление в рамках предусмотренного процесса и действующих правил подтверждения доставки. Историческое назначение и сведения о курьере сохраняются. Реализация не разрешена.

## 2026-11-21 — изменение ФИО курьера

Владелец продукта утвердил возможность изменения ФИО курьера только активным администратором. Изменение ФИО не должно переписывать исторические отправления, доставки и назначения: в историческом представлении сохраняется ФИО курьера на момент соответствующего назначения/события, если такое значение зафиксировано. Текущее ФИО используется для новых назначений и текущих карточек. Историческое авторство и сведения не подменяются текущими значениями. Изменение ФИО и обязательная запись AdminSecurityAudit выполняются атомарно. Реализация не разрешена.

## 2026-11-21 — запрет физического удаления карточек курьеров

Владелец продукта утвердил запрет физического удаления записей курьеров штатными операциями МедАрхива. Запись курьера сохраняется независимо от текущего статуса и наличия новых назначений, поскольку связана с историческими отправлениями, доставками и событиями. Прекращение использования курьера оформляется деактивацией; повторная активация использует ту же запись и идентификатор. Исторические назначения и сведения о курьере не удаляются и не переписываются. Реализация не разрешена.

## 2026-11-21 — закрытие продуктового блока управления курьерами

Владелец продукта подтвердил закрытие продуктового блока управления курьерами. Согласованы создание и редактирование карточек только активным администратором; минимальный состав данных (ФИО и статус, технический идентификатор); изменение ФИО с сохранением исторического значения; деактивация и повторная активация прежней записи; запрет физического удаления; выбор активного курьера уполномоченными сотрудниками при оформлении отправлений; возможность завершить уже назначенное отправление после деактивации. Дополнительные продуктовые вопросы по этому блоку не задаются. Недостающие технические детали определяются при подготовке спецификации без изменения утверждённых правил. Реализация не разрешена.

## 2026-11-21 — управление справочником архивных полок

Владелец продукта утвердил, что создавать, переименовывать, деактивировать и повторно активировать записи архивных полок может только активный администратор. Сотрудники архива используют активные полки для размещения коробок в рамках своих полномочий, но не управляют справочником полок. Это правило не меняет утверждённые ограничения на размещение коробок и сохранение исторических BoxPlacement. Изменения справочника выполняются с обязательной атомарной записью AdminSecurityAudit; физическое удаление и влияние на исторические размещения не предполагаются. Реализация не разрешена.

## 2026-11-21 — условия деактивации архивной полки

Утверждено: архивную полку нельзя деактивировать, пока на ней размещена хотя бы одна коробка с активным BoxPlacement. До деактивации сотрудники архива должны переместить все размещённые коробки на другие активные полки в пределах своих полномочий. После отсутствия активных размещений активный администратор может деактивировать полку. Исторические записи BoxPlacement сохраняются; автоматическое перемещение коробок, закрытие или удаление исторических размещений не выполняется. Проверка отсутствия активных размещений и деактивация должны быть защищены от конкурентных изменений и выполняться атомарно с обязательной записью AdminSecurityAudit. Физическое удаление полки не допускается. Реализация не разрешена.

## 2026-11-21 — повторная активация архивной полки

Утверждено: активный администратор может повторно активировать ранее деактивированную архивную полку. Используется прежняя запись и прежний идентификатор; новая запись не создаётся. Исторические BoxPlacement, события и связи сохраняются без переписывания. После успешной активации полка снова доступна сотрудникам архива для размещения коробок в рамках их полномочий. Активация и обязательная запись AdminSecurityAudit выполняются атомарно. Реализация не разрешена.

## 2026-11-21 — переименование архивной полки

Утверждено: активный администратор может переименовать архивную полку. Используется прежняя запись и идентификатор; новая полка не создаётся. Исторические BoxPlacement, события и связи сохраняются. В исторических представлениях отображается название полки на момент события, если соответствующее историческое значение сохранено; текущее название не должно подменять историческое. Переименование и обязательная запись AdminSecurityAudit выполняются атомарно. Технические ограничения формата и уникальности названий, а также способ хранения исторического отображаемого значения определяются в технической спецификации. Реализация не разрешена.

## 2026-11-21 — запрет физического удаления архивных полок

Утверждено: физическое удаление записей архивных полок штатными операциями МедАрхива запрещено. Если полка больше не используется, активный администратор деактивирует её после выполнения ранее утверждённых условий: на ней не должно оставаться коробок с активным BoxPlacement. Запись полки, её идентификатор, исторические размещения, события и связи сохраняются. Повторная активация прежней записи разрешена по утверждённой процедуре. Реализация не разрешена.

## 2026-11-21 — создание архивных полок

Утверждено: создавать новые записи архивных полок может только активный администратор. Сотрудники архива используют существующие активные полки для размещения коробок в рамках своих полномочий, но не создают новые записи и не управляют справочником. Правило действует совместно с утверждёнными полномочиями администратора по переименованию, деактивации и повторной активации полок, сохранением исторических BoxPlacement и запретом физического удаления. Создание записи полки и обязательная запись AdminSecurityAudit выполняются атомарно. Реализация не разрешена.

## 2026-11-21 — неизменяемость короткого номера коробки

Утверждено: короткий номер является постоянным человекочитаемым идентификатором физической коробки и не изменяется после создания коробки в рамках обычных операций. Сотрудники архива не могут переименовывать коробку или менять её короткий номер. Если обнаружена ошибка в номере, исправление не выполняется произвольным редактированием; для такого случая требуется отдельная контролируемая процедура, которая должна быть согласована до её применения. Настоящее решение не вводит автоматическую перенумерацию, не разрешает изменение номера через P-07 и не меняет исторические связи, циклы, размещения и события коробки. Реализация не разрешена.

## 2026-11-21 — глобальная уникальность короткого номера коробки

Утверждено: короткий номер коробки уникален в пределах всего МедАрхива, а не только отдельного филиала, центрального офиса или архива. Один и тот же номер не может быть назначен другой записи коробки. Уникальность сохраняется для всех записей, включая выведенные из использования и списанные коробки: номер не освобождается и повторно не используется. Это обеспечивает однозначную идентификацию физической коробки и сохраняет корректность исторических связей и событий при перемещениях между локациями. Реализация не разрешена.

## 2026-11-21 — автоматическое присвоение короткого номера коробки

Утверждено: при создании новой физической коробки МедАрхив автоматически присваивает ей короткий номер. Сотрудник не вводит и не выбирает номер вручную. Механизм присвоения должен обеспечивать глобальную уникальность номера в пределах всего МедАрхива, не допускать повторного использования ранее назначенных номеров и быть безопасным при одновременном создании нескольких коробок. После создания номер остаётся неизменяемым согласно ранее утверждённому правилу. Конкретный формат номера и технический механизм генерации определяются в спецификации без изменения этих продуктовых ограничений. Реализация не разрешена.

## 2026-11-21 — полномочия на создание физических коробок

Утверждено: физические коробки могут создавать уполномоченные активные сотрудники филиалов, центрального офиса и архива — каждый в пределах своей операционной области и действующих полномочий. Создание коробки не является исключительной административной операцией; администратор не подменяет обычный процесс создания коробок. При создании МедАрхив автоматически присваивает коробке глобально уникальный короткий номер по ранее утверждённым правилам. Проверка активности учётной записи, роли, организационной области и права на операцию выполняется на серверной стороне. Реализация не разрешена.

## 2026-11-21 — начальная учётная локация новой коробки

Утверждено: при создании физической коробки МедАрхив автоматически устанавливает её начальную учётную локацию по операционной области уполномоченного сотрудника, выполнившего создание: соответствующий филиал, центральный офис или архив. Создание коробки само по себе не создаёт физическое размещение на архивной полке и не создаёт BoxPlacement. Если коробка создаётся в архивной учётной локации, её размещение на конкретной активной полке выполняется отдельно уполномоченным сотрудником архива командой box_place по действующим правилам. Начальная локация и права создания проверяются серверной стороной; реализация не разрешена.

## 2026-11-21 — создание пустой коробки без немедленного заполнения

Утверждено: физическую коробку разрешено создать без немедленного добавления в неё медицинских карт. Такая коробка может оставаться пустой и ожидать последующего заполнения. Само создание коробки не создаёт BoxCycle и не создаёт membership. Первый BoxCycle создаётся атомарно с добавлением первой карты командой box_add_card по ранее утверждённым правилам. Начальная учётная локация определяется при создании коробки; размещение на архивной полке не возникает автоматически и выполняется отдельно по правилам box_place. Реализация не разрешена.

## 2026-11-21 — вывод из использования ошибочно созданной пустой коробки

Утверждено: если физическая коробка создана ошибочно, ещё пуста и не участвовала в операционных процессах, её разрешено вывести из использования отдельной операцией списания коробки box_write_off по действующим правилам P-05 и DB-операции. Для этого случая обязательны отсутствие карт и membership, отсутствие BoxCycle, shipment и иных операционных размещений/перемещений; операция не должна обходить существующие проверки полномочий и области доступа. Физическое удаление записи коробки запрещено. Её короткий номер остаётся навсегда закреплённым за этой записью и не может быть повторно использован. История и событие списания сохраняются. Это уточнение не вводит отдельный механизм удаления или отмены создания и не меняет ранее утверждённые общие правила box_write_off. Реализация не разрешена.

## 2026-11-21 — отдельное название коробки не используется

Утверждено: у физической коробки нет отдельного пользовательского названия. Для её идентификации и поиска используется автоматически присваиваемый глобально уникальный короткий номер. Этот номер постоянен и не изменяется после создания. Дополнительное поле названия, его редактирование и переименование коробки не вводятся. Для отображения и поиска также могут использоваться её учётная локация, текущее физическое размещение и состояние — без подмены ими идентификатора. Реализация не разрешена.

## 2026-11-21 — причина создания коробки не фиксируется

Утверждено: при создании физической коробки не требуется и не сохраняется причина создания. Не вводятся поле причины, обязательный комментарий или свободное пояснение. Это решение относится только к причине создания и не отменяет фиксацию самого факта создания и стандартных технических атрибутов события (инициатор, дата и время) в соответствии с общей моделью учёта и аудита. Реализация не разрешена.

## 2026-11-21 — полномочия добавления медицинских карт в коробку

Утверждено: добавлять медицинские карты в коробку может только уполномоченный активный сотрудник филиала, центрального офиса или архива в пределах своей операционной области и учётной локации. Перед добавлением МедАрхив проверяет доступность карты для помещения в коробку, соответствие учётной локации коробки области сотрудника, отсутствие списания коробки и её участия в активном отправлении, а также отсутствие дублирующего размещения карты. Операция выполняется по действующим атомарным правилам box_add_card и не должна создавать конкурирующие активные membership/размещения. Реализация не разрешена.

## 2026-11-21 — состав коробки при заполнении в филиале, ЦО и архиве

Утверждено уточнение правила состава коробки: ограничение на добавление в одну коробку карт только одного филиала применяется к этапу заполнения коробок в филиалах. Оно не распространяется на операции заполнения и перегруппировки коробок в центральном офисе и архиве. В ЦО и архиве допускается совместное нахождение в одной коробке карт, поступивших из разных филиалов, при условии соблюдения действующих полномочий, учётной локации, custody, доступности карт и остальных инвариантов box_add_card. Это уточнение не разрешает филиалу добавлять в одну коробку карты другого филиала. Реализация не разрешена.

## 2026-11-21 — запрет добавления карты, уже находящейся в другой коробке

Утверждено: сотрудник не может добавить медицинскую карту в коробку, если карта уже состоит в активном membership другой коробки. До добавления карта должна быть извлечена из прежней коробки по предусмотренной операции; только после успешного закрытия прежнего membership допускается добавление в новую коробку. Прямое создание второго одновременного активного membership запрещено. Проверка и изменение выполняются атомарно с защитой от конкурентных операций; история прежнего и нового размещения сохраняется. Реализация не разрешена.

## 2026-11-21 — лимит вместимости коробки пока не устанавливается

Утверждено: на текущем этапе максимальная вместимость коробки по количеству медицинских карт не устанавливается, поскольку допустимые лимиты не определены. При добавлении карты проверка по максимальному количеству карт не выполняется; искусственный программный лимит не вводится. Вопрос может быть пересмотрен после определения фактических размеров используемых коробок и допустимой загрузки. Реализация не разрешена.

## 2026-11-21 — порядок карт внутри коробки не учитывается

Утверждено: МедАрхив не фиксирует и не контролирует физический порядок медицинских карт внутри коробки. Нумерация мест, позиции карт, последовательность укладки и обязательная сортировка внутри коробки не вводятся. Система учитывает состав коробки через действующие связи membership; это решение не отменяет учёт факта добавления и извлечения карт и сохранение истории соответствующих операций. Реализация не разрешена.

## 2026-11-21 — два равноправных сценария добавления медицинских карт в коробку

Утверждено: филиал использует МедАрхив прежде всего для документального оформления отправки собственных медицинских карт в архив. Сотруднику предоставляются два равноправных сценария, выбор между которыми остаётся за сотрудником:

1. Сначала создать записи медицинских карт, затем сформировать коробку и добавить в неё ранее созданные карты.
2. Сначала создать коробку, затем непосредственно в её составе находить и добавлять существующие карты либо создавать отсутствующие в системе карты с немедленным добавлением в эту коробку.

Для поиска и добавления карты должны быть доступны оба способа идентификации: сканирование штрихкода и ручной поиск/ввод данных. Ручной поиск должен поддерживать ФИО пациента, номер медицинской карты и телефон. Доступ к поиску по телефону подчиняется действующим отдельным правам доступа к чувствительным данным; наличие поля поиска не расширяет полномочия сотрудника. Ручной ввод данных карты также должен быть доступен как альтернатива сканированию штрихкода.

Если при добавлении карта не найдена, сотрудник может создать её запись в рамках того же сценария и сразу добавить карту в открытую коробку; не требуется выходить из процесса работы с коробкой и отдельно повторно искать созданную карту. Создание записи карты и её добавление в коробку должны выполняться как единый согласованный бизнес-сценарий с соблюдением обязательных проверок, атомарности, защиты от дублирования и действующих ограничений по роли, организационной области, custody и membership. Если подходящая существующая карта обнаружена, система должна предложить использовать её, а не создавать дубликат автоматически.

Оба сценария должны приводить к одинаковому учётному результату и использовать общие правила создания карты, box_add_card и дальнейшей подготовки коробки к отправке. Интерфейс должен явно поддерживать оба пути и не навязывать сотруднику единственную последовательность действий. Реализация не разрешена.

## 2026-11-21 — уточнение: филиал оформляет отправление, а не заявку

Уточнение владельца продукта: филиал не создаёт заявки на передачу карт в архив. Заявки относятся к отдельному процессу ЦО → архив (P-09); создавать их могут сотрудники ЦО в рамках согласованных полномочий. Для обычного маршрута филиал → архив сотрудник филиала оформляет отправление по ранее согласованному процессу. В частности, отправляющая локация работает с черновиком отправления и может изменять перечень коробок до фактической передачи; после подтверждения handover действуют согласованные правила перевозки и смены ответственности.

Два равноправных сценария создания/поиска карт и добавления их в коробку, описанные выше в этом документе, относятся к подготовке карт и коробок филиала к обычному отправлению. Они не создают заявку P-09, не вводят для филиала новый тип заявки и не меняют согласованный процесс оформления отправления branch → archive. Реализация не разрешена.


## 2026-11-21 — ограничение состава отправления филиала: только непустые коробки

Утверждено владельцем продукта: в обычное отправление по маршруту филиал → архив разрешено включать только коробки, содержащие не менее одной медицинской карты. Пустая коробка не может быть добавлена в состав отправления. Пустую коробку филиал оставляет для повторного использования либо списывает по действующим правилам; отдельная отправка пустой коробки в архив не предусматривается. Ограничение проверяется серверной бизнес-операцией при формировании/изменении состава отправления, а не только интерфейсом. Проверка должна учитывать актуальный состав коробки и быть защищена от конкурентного изменения: после включения коробки в отправление нельзя допустить обход ограничения через параллельное извлечение последней карты; применяются действующие атомарные правила и блокировки процесса отправления. Это правило относится к отправлениям филиала и не меняет общие правила существования, повторного заполнения, размещения или списания пустых коробок в других процессах. Реализация не разрешена.


## 2026-11-21 — выбор коробок филиалом при оформлении отправления

Утверждено владельцем продукта: при оформлении отправления филиалу показывается список доступных для отправки коробок в его операционной области. Для каждой коробки отображаются её короткий номер и актуальное количество медицинских карт. Сотрудник выбирает из списка одну или несколько коробок, включаемых в отправление. Пустые коробки не предлагаются как доступные для отправки и не могут быть включены; серверная проверка непустого состава остаётся обязательной и согласуется с правилом ограничения состава отправления. Для выбора коробок используется список и отметка нужных позиций; сканирование штрихкода/QR коробки не вводится. Это не меняет ранее согласованное правило, что QR-код коробки не используется, а короткий номер является её человекочитаемым идентификатором. Список должен отражать только коробки, доступные текущему сотруднику по роли, операционной области, учётной локации и состоянию. Реализация не разрешена.


## 2026-11-21 — изменение состава отправления филиала до передачи курьеру

Утверждено владельцем продукта: пока отправление филиала находится в статусе черновика и ещё не подтверждена фактическая передача курьеру, уполномоченный сотрудник отправляющей локации может изменять его состав — добавлять и удалять коробки — в пределах действующих правил доступа и допустимости коробок. При каждом изменении повторно проверяются актуальная принадлежность коробки филиалу, отсутствие участия в другом несовместимом активном процессе и наличие не менее одной медицинской карты. При подтверждении фактической передачи курьеру состав отправления фиксируется. После этого обычное редактирование состава запрещено; исправление ошибочно зафиксированного состава возможно только через отдельно предусмотренный и согласованный корректирующий процесс, а не прямым изменением исходной записи. Фиксация handover и неизменяемость состава после неё должны быть обеспечены серверными атомарными проверками и защитой от конкурентных изменений. Реализация не разрешена.


## 2026-11-21 — подтверждение передачи отправления курьеру филиалом

Утверждено владельцем продукта: фактическую передачу коробок курьеру подтверждает в МедАрхиве уполномоченный сотрудник отправляющего филиала, который осуществляет передачу. Курьер должен быть назначен в отправлении заранее; подтверждение передачи фиксирует именно факт handover назначенному курьеру. Система автоматически сохраняет дату и время подтверждения, идентификатор и историческое отображение ФИО сотрудника-подтверждающего (snapshot при наличии), а также назначенного курьера и состав отправления на момент передачи. Подтверждение является атомарной операцией: проверяется активность и полномочия сотрудника, принадлежность отправления его филиалу, назначение активного курьера, допустимость и непустой состав каждой коробки, а также отсутствие конфликтующих операций. После успешного подтверждения состав отправления фиксируется и обычное редактирование запрещено. Нельзя подменять фактическое подтверждение передачей только по факту назначения курьера. Реализация не разрешена.


## 2026-11-21 — курьер не подтверждает получение отправления в МедАрхиве

Утверждено владельцем продукта: курьер не подтверждает получение коробок в МедАрхиве. Для курьера не создаётся учётная запись и не предоставляется доступ к приложению; отдельное действие, экран или электронное подтверждение получения со стороны курьера не вводятся. Факт передачи курьеру фиксирует уполномоченный сотрудник отправляющего филиала в момент фактического handover. Последующие этапы доставки и приёмки оформляются сотрудниками организации по соответствующему процессу. Решение согласуется с ранее утверждённой минимальной карточкой курьера (ФИО, статус, технический ID) и запретом дополнительных контактных данных. Реализация не разрешена.


## 2026-11-21 — приёмка отправления архивом

Утверждено владельцем продукта: поступившее отправление принимает уполномоченный активный сотрудник архива в пределах своей операционной области. Сотрудник сверяет фактически поступившие коробки с составом отправления в МедАрхиве и подтверждает приёмку. Система автоматически фиксирует дату и время приёмки, идентификатор и историческое отображение ФИО сотрудника-принимающего (snapshot при наличии), а также фактически принятый состав коробок. Если фактическое количество коробок не совпадает с указанным в отправлении, сотрудник должен иметь возможность зафиксировать расхождение; расхождение сохраняется как отдельный факт и не должно незаметно переписывать исходный состав отправления или автоматически считаться устранённым. Подтверждение приёмки и запись её обязательных событий выполняются атомарно с серверной проверкой полномочий, scope и текущего состояния отправления. Курьер не выполняет подтверждение в системе. Конкретные дальнейшие действия и влияние расхождения на статус отправления/ответственность сторон требуют отдельного согласования; этим решением они не определяются. Реализация не разрешена.


## 2026-11-21 — неполная приёмка отправления

Утверждено владельцем продукта: если архив фактически получил не все коробки, уполномоченный сотрудник фиксирует приёмку только фактически поступивших коробок и отдельно фиксирует расхождение по недостающим коробкам. Такое отправление не считается полностью принятым и не закрывается как полностью принятое, пока расхождение не обработано в предусмотренном порядке. Исходный состав отправления и подтверждённый handover сохраняются без переписывания; частичная приёмка не означает автоматического закрытия отправления, признания недостающих коробок утраченными или подтверждения их доставки. Конкретные статусы, действия по розыску/возврату и порядок окончательного разрешения расхождения не определяются этим решением и должны быть согласованы отдельно, если потребуются для проектирования соответствующего процесса. Реализация не разрешена.


## 2026-11-21 — позднее поступление недостающей коробки

Утверждено владельцем продукта: если коробка, отсутствовавшая при первоначальной приёмке отправления, поступает в архив позднее, архив принимает её в рамках того же отправления. Для поздно поступившей коробки создаётся отдельная запись/событие фактической приёмки с собственной датой и временем и идентификатором уполномоченного сотрудника-принимающего (историческое отображение ФИО — snapshot при наличии). Первоначальная частичная приёмка и зафиксированное тогда расхождение сохраняются без переписывания; позднее поступление фиксируется как последующее событие и позволяет отразить устранение соответствующего расхождения. Отправление может быть признано полностью принятым только после сверки всего исходного состава и обработки всех расхождений. Нельзя задним числом менять время первоначальной приёмки или представлять позднее поступление как часть первоначальной приёмки. Реализация не разрешена.


## 2026-11-21 — лишняя коробка при приёмке отправления

Утверждено владельцем продукта: если при приёмке фактически поступила коробка, отсутствующая в зафиксированном составе отправления, уполномоченный сотрудник архива фиксирует её как расхождение (лишняя/непредусмотренная коробка). Такая коробка не добавляется автоматически в исходный состав отправления и не считается принятой на архивное хранение в рамках этого отправления. Исходный состав и подтверждённый handover сохраняются без изменений. Факт физического поступления и сведения, необходимые для выяснения происхождения коробки, фиксируются отдельно в рамках доступного процесса учёта расхождений; нельзя приписывать коробку отправлению или менять её custody/location автоматически. Дальнейшее решение о принадлежности, возврате либо отдельном оформлении коробки требует выяснения и предусмотренного отдельного процесса; этот вопрос данным решением не предрешается. Реализация не разрешена.

## 2026-11-21 — поштучная приёмка коробок в рамках отправления

Утверждено владельцем продукта: архив может принимать коробки из отправления по одной, последовательно сверяя фактически поступившую коробку с зафиксированным составом отправления. Для каждой коробки отдельно фиксируется факт приёмки с фактическими датой и временем и идентификатором уполномоченного сотрудника-принимающего (историческое отображение ФИО — snapshot при наличии). Не требуется подтверждать получение всего отправления одной общей операцией. Частичная приёмка сохраняет ранее зафиксированные события и расхождения; отправление не считается полностью принятым до сверки всего исходного состава и обработки всех расхождений. Поздно поступившие коробки принимаются в рамках того же отправления отдельными событиями. Коробка, отсутствующая в исходном составе, не может быть принята как штатная коробка отправления и оформляется как отдельное расхождение. Все операции должны выполнять серверные проверки полномочий, scope, состояния отправления и принадлежности коробки; фиксация приёмки и обязательных событий атомарна. Реализация не разрешена.

## 2026-11-21 — custody коробки после поштучной приёмки

Утверждено владельцем продукта: каждая коробка переходит под ответственность архива сразу после индивидуального подтверждения её фактической приёмки уполномоченным сотрудником архива. Для перехода custody не требуется ожидать приёмки остальных коробок отправления или полного закрытия отправления. Частично принятое отправление может одновременно содержать уже принятые архивом коробки и ещё не принятые коробки, по которым сохраняется соответствующее состояние и расхождение. Переход custody выполняется атомарно с записью события приёмки и обязательных событий; сервер проверяет полномочия сотрудника, scope, принадлежность коробки зафиксированному составу отправления и допустимое состояние. Физическое размещение принятой коробки на архивной полке остаётся отдельной операцией и не возникает автоматически. Реализация не разрешена.

## 2026-11-21 — размещение принятой коробки до завершения всего отправления

Утверждено владельцем продукта: архив вправе размещать коробку на активной архивной полке отдельной операцией `box_place` сразу после индивидуальной приёмки этой коробки, не дожидаясь поступления или приёмки остальных коробок того же отправления. Условием размещения остаётся успешная приёмка конкретной коробки и переход её под ответственность архива; сама по себе физическая доставка или наличие коробки в исходном составе отправления недостаточны. Действуют все ранее утверждённые проверки `box_place`, включая активность целевой полки, архивную учётную локацию коробки, отсутствие активного размещения и участия в активном отправлении, полномочия и scope сотрудника. Приёмка и размещение остаются разными операциями и отдельными историческими фактами. Реализация не разрешена.

## 2026-11-21 — состав и состояние коробок в карточке отправления

Утверждено владельцем продукта: в карточке отправления сотруднику архива доступен сводный просмотр по каждой коробке, позволяющий различать: (1) коробка ожидается — входит в зафиксированный состав отправления, но её индивидуальная приёмка архивом ещё не подтверждена; (2) коробка принята — индивидуальная фактическая приёмка подтверждена архивом; (3) коробка размещена на полке — после индивидуальной приёмки для неё создано действующее архивное размещение (BoxPlacement). Состояние размещения является отдельным признаком и не подменяет факт приёмки: принятая коробка может ещё не иметь размещения на полке. Просмотр отражает исходный зафиксированный состав отправления, индивидуальные факты приёмки и текущие активные размещения; он не переписывает состав отправления, не создаёт события приёмки/размещения и не меняет custody или местоположение. Доступ предоставляется только уполномоченным сотрудникам архива в пределах их операционной области; действуют общие серверные проверки доступа и минимизации данных. Реализация не разрешена.

## 2026-11-21 — сведения о сотруднике и времени приёмки коробки в карточке отправления

Утверждено владельцем продукта: в карточке отправления сотруднику архива для каждой индивидуально принятой коробки отображаются дата и время фактической приёмки, а также ФИО сотрудника, подтвердившего приёмку. Для коробок, которые ещё ожидаются и не имеют подтверждённого факта приёмки, сведения о сотруднике и времени приёмки не отображаются. Для исторического авторства используется сохранённый snapshot ФИО на момент операции, если он имеется; текущее ФИО из профиля не подставляется вместо исторического. Если исторический snapshot отсутствует, применяется безопасное обозначение отсутствующих исторических сведений, без угадывания личности. Просмотр не меняет события приёмки и не расширяет права доступа: действуют общие серверные проверки полномочий, операционной области и минимизации данных. Реализация не разрешена.

## 2026-11-21 — компактное отображение архивного размещения коробки в карточке отправления

Утверждено владельцем продукта: в карточке отправления для каждой коробки с действующим архивным размещением показываются номер архивной полки и дата/время оформления текущего размещения. Эти сведения отображаются компактно как вторичная строка/деталь в строке коробки, не наравне с основными сведениями о коробке и приёмке. Для ожидающей коробки сведения о размещении не показываются; для принятой, но ещё не размещённой коробки явно отображается «Не размещена». Основная информация о коробке и её состоянии остаётся визуально приоритетной; сведения о приёмке (дата/время и историческое ФИО сотрудника) и размещении не объединяются в один факт. При отсутствии действующего размещения нельзя показывать устаревший номер полки или дату предыдущего размещения как текущее местоположение. Просмотр использует факты индивидуальной приёмки и актуальное активное BoxPlacement, не изменяет историю, custody или местоположение. Доступ ограничивается уполномоченными сотрудниками архива в пределах их операционной области с обычными серверными проверками. Реализация не разрешена.

## 2026-11-21 — сводный счётчик коробок в карточке отправления

Утверждено владельцем продукта: в карточке отправления показывается компактная сводка количества коробок по исходному зафиксированному составу: общее количество, количество индивидуально принятых коробок и количество ожидающих приёмки коробок. Формат отображения: «Всего N · Принято N · Ожидается N». Значения рассчитываются по коробкам исходного состава отправления и фактам индивидуальной приёмки; принятая коробка учитывается ровно один раз, независимо от наличия активного архивного размещения. Размещение на полке не влияет на счётчик приёмки. Лишние физически поступившие коробки, не входящие в зафиксированный состав, в эти значения не включаются. Сводка является только представлением данных и не меняет состав отправления, факты приёмки, расхождения, custody или размещение. Доступ — в рамках уже утверждённых прав на просмотр карточки отправления и её состава. Реализация не разрешена.

## 2026-11-21 — отдельное отображение расхождений в карточке отправления

Утверждено владельцем продукта: расхождения при приёмке отображаются в карточке отправления в отдельном блоке «Расхождения», который показывается только при наличии зафиксированных расхождений. Для каждого факта показываются тип расхождения, номер коробки (если известен), дата и время фиксации, а также ФИО сотрудника, зафиксировавшего факт, если сохранён исторический snapshot. Недостающая коробка остаётся в исходном зафиксированном составе отправления и продолжает учитываться как ожидающая приёмки до индивидуального подтверждения её фактического получения. Лишняя физически поступившая коробка отображается только в блоке расхождений, не включается в исходный состав отправления и не считается принятой в рамках этого отправления. Факты расхождений сохраняются в истории и не исчезают автоматически после последующих действий; отображение не вводит неподтверждённый процесс разрешения/закрытия расхождений. Блок не изменяет исходный состав, факты приёмки, custody или местоположение коробок. Доступ — в рамках уже утверждённых прав на просмотр карточки отправления и соответствующих фактов. Реализация не разрешена.


## 2026-11-21 — хронология событий в карточке отправления

Утверждено владельцем продукта: карточка отправления для уполномоченных сотрудников архива включает компактную хронологию по времени, построенную по существующим фактам: создание отправления, назначение курьера, подтверждение передачи курьеру, индивидуальная приёмка каждой коробки, размещение на архивной полке и фиксация расхождений. Для события показываются дата/время, тип и историческое ФИО инициатора/исполнителя при наличии сохранённого snapshot; актуальное ФИО не используется для подмены исторического. Частичные и поздние приёмки отражаются отдельными событиями в фактической последовательности. Хронология — read-only представление, не создающее событий и не меняющее состав, custody, местоположение или состояние отправления. Она ограничена карточкой конкретного отправления и существующими правами/операционной областью; это не глобальный журнал, не экспорт и не доступ к другим отправлениям. Требуются серверная авторизация до выдачи данных, allowlist полей и минимизация. Реализация не разрешена.


## 2026-11-21 — свёрнутая хронология событий отправления

Утверждено владельцем продукта: хронология событий в карточке отправления свёрнута по умолчанию и показывает несколько последних событий; доступно действие раскрытия полной доступной истории. Это UI-поведение не изменяет состав данных, последовательность событий, авторство или права доступа. Полная история остаётся доступной уполномоченному сотруднику архива в рамках конкретного отправления. Реализация не разрешена.

## 2026-11-21 — индивидуальное подтверждение приёмки коробки в карточке отправления

Утверждено владельцем продукта: в списке коробок карточки отправления архива напротив каждой коробки, ожидающей приёмки, размещается отдельное действие «Принять». Уполномоченный сотрудник архива подтверждает фактическое получение каждой коробки непосредственно из её строки; одна операция относится только к выбранной коробке и не подтверждает приёмку остальных коробок отправления. После успешного подтверждения сведения этой строки автоматически обновляются: отображается состояние «Принята» и предусмотренные карточкой дата/время фактической приёмки и историческое ФИО принявшего при наличии snapshot. Действие доступно только для коробок, которые входят в зафиксированный состав отправления, ещё не приняты и доступны сотруднику по действующим полномочиям и scope. Сервер повторно проверяет состав, состояние, полномочия и допустимость операции; изменение состояния и обязательные события фиксируются атомарно. Ручная кнопка общего обновления карточки не вводится: после успешных операций приёмки и размещения интерфейс автоматически отражает актуальные сведения. Приёмка и размещение остаются разными действиями. Реализация и тестирование не выполнялись и не разрешены.

## 2026-11-21 — идентификация коробки при приёмке отправления

Утверждено владельцем продукта: для подтверждения приёмки коробки сотрудник архива сверяет её человекочитаемый короткий номер с номером соответствующей коробки в строке карточки отправления и использует действие «Принять». Обязательное сканирование штрихкода/QR коробки для приёмки не вводится; QR коробки не используется. Система должна показывать номер достаточно отчётливо для визуальной сверки. Подтверждение по-прежнему выполняется отдельно для каждой коробки и только в отношении коробки из зафиксированного состава отправления, ожидающей приёмки. Серверные проверки принадлежности, состояния, полномочий и scope, атомарная фиксация приёмки и обязательных событий сохраняются. Решение не меняет ранее утверждённое обязательное сканирование штрихкода медицинской карты в процессах добавления карты. Реализация и тестирование не выполнялись и не разрешены.

## 2026-11-21 — отдельное размещение принятой коробки на архивной полке из карточки отправления

Утверждено владельцем продукта: приёмка коробки и её размещение на архивной полке выполняются как две самостоятельные операции. После индивидуальной приёмки сотрудник архива при необходимости отдельно запускает действие «Разместить» для этой коробки и выбирает целевую активную архивную полку. Размещение не выполняется автоматически при нажатии «Принять» и не является частью подтверждения приёмки. Действие «Разместить» доступно только для уже принятой коробки, которая находится под ответственностью архива и ещё не имеет действующего размещения; применяются все ранее утверждённые проверки box_place, включая полномочия и scope сотрудника, принадлежность коробки архивной учётной локации, отсутствие активного отправления/другого размещения и активность полки в той же архивной области. Создание BoxPlacement и обязательного события выполняется атомарно. После успешного размещения карточка автоматически обновляет сведения о текущей полке и времени размещения. Это не меняет ранее утверждённое правило: принятую коробку можно разместить сразу, не дожидаясь приёмки остальных коробок отправления. Реализация и тестирование не выполнялись и не разрешены.

## 2026-11-21 — контролируемое исправление ошибочной приёмки коробки

Утверждено владельцем продукта: ошибочную приёмку коробки нельзя отменить обычной кнопкой, удалением события или прямым изменением статуса. Если факт приёмки был зафиксирован ошибочно, исправление допускается только через отдельную контролируемую корректирующую операцию, предусмотренную для такого случая. Первоначальная приёмка, её дата/время и авторство остаются в истории неизменными; корректировка фиксируется отдельным событием и не переписывает исходный факт. Нельзя считать само наличие корректировки основанием для автоматического изменения других фактов отправления, размещения или custody без явно определённых правил корректирующей операции. Состав полномочий, допустимые исходные состояния и точное влияние такой операции должны быть специфицированы до реализации; это решение само по себе не разрешает реализацию и не вводит произвольную отмену приёмки. Реализация и тестирование не выполнялись и не разрешены.

## 2026-11-21 — полномочия для исправления ошибочной приёмки коробки

Утверждено владельцем продукта: отдельную контролируемую корректирующую операцию для исправления ошибочной приёмки коробки вправе выполнять только активный сотрудник архива, которому предоставлены соответствующие полномочия и который действует в пределах своей операционной области (scope). Роль администратора сама по себе не даёт права выполнять эту операцию и не позволяет подменять штатный процесс архива. Сервер обязан проверять активность учётной записи, роль, специальное полномочие, операционную область, исходный факт приёмки и допустимое состояние коробки непосредственно при выполнении команды. Корректировка и обязательное событие фиксируются атомарно; первоначальный факт приёмки и его историческое авторство сохраняются без изменений. Реализация и тестирование не выполнялись и не разрешены.

## 2026-11-21 — обязательная причина исправления ошибочной приёмки коробки

Утверждено владельцем продукта: при выполнении контролируемой корректирующей операции для исправления ошибочной приёмки сотрудник архива обязан указать причину. Причина выбирается из закрытого перечня кодов; свободный комментарий не требуется и не вводится как обязательное поле. Выбранный код причины сохраняется вместе с отдельным событием корректировки и связан с исходным фактом ошибочной приёмки. Перечень кодов определяется в технической спецификации до реализации; произвольный текст вместо кода не допускается. Первоначальная приёмка и её исторические сведения остаются неизменными. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — исправление ошибочной приёмки не меняет состояние автоматически

Утверждено владельцем продукта: после контролируемого исправления ошибочной приёмки коробка не возвращается автоматически в состояние «Ожидается». Сам факт корректировки не отменяет приёмку автоматически и не меняет автоматически фактическое местонахождение коробки, её учётную локацию или custody (ответственность). Корректирующая операция должна учитывать фактическое положение коробки и допустимые для него действия; любые необходимые изменения оформляются только явно предусмотренными отдельными правилами и событиями, а не побочным эффектом исправления приёмки. Первоначальный факт приёмки, его дата/время и авторство сохраняются неизменными; корректировка сохраняется отдельным событием с обязательным кодом причины из закрытого перечня. Это решение не разрешает реализацию и не заменяет спецификацию допустимых состояний и последствий корректирующей операции. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — исправление ошибочной приёмки не снимает коробку с архивной полки автоматически

Утверждено владельцем продукта: контролируемое исправление ошибочной приёмки коробки само по себе не закрывает и не отменяет действующее архивное размещение (BoxPlacement). Если коробка физически находится на указанной архивной полке, действующее размещение сохраняется, даже если факт приёмки был ошибочным. Если ошибочным является также само размещение либо сведения о нём не соответствуют фактическому положению коробки, оно исправляется отдельно через предусмотренную контролируемую операцию с собственными проверками, полномочиями, причиной (если она обязательна для соответствующей операции) и отдельным историческим событием. Нельзя автоматически снимать коробку с полки или менять её физическое размещение как побочный эффект исправления приёмки. Первоначальные факты приёмки и размещения, их авторство и временные отметки не переписываются; корректирующие события сохраняются отдельно. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — фактические перемещения коробки сохраняются при исправлении ошибочной приёмки

Утверждено владельцем продукта: если после ошибочной приёмки коробка была фактически перемещена на другую архивную полку, контролируемое исправление приёмки не отменяет и не переписывает это перемещение. История всех фактически оформленных размещений и перемещений сохраняется; текущим местоположением считается последнее действующее размещение, соответствующее фактическому положению коробки. Исправление ошибочной приёмки само по себе не возвращает коробку на прежнюю полку, не закрывает текущее размещение и не изменяет историю перемещений. Если отдельный факт размещения или перемещения также ошибочен, он исправляется самостоятельной предусмотренной корректирующей операцией с отдельной исторической записью. Первоначальная приёмка и все последующие операционные события остаются неизменными. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — отображение исправления ошибочной приёмки в хронологии отправления

Утверждено владельцем продукта: в хронологии конкретного отправления сохраняются и отображаются как отдельные события первоначальная приёмка коробки и последующая корректировка ошибочной приёмки. Первоначальное событие не скрывается и не заменяется корректировкой. Для каждого события показываются дата/время и тип события; исполнитель/инициатор отображается по сохранённому историческому ФИО snapshot, если он имеется, без подстановки текущего ФИО. Корректировка отображается отдельной записью с причиной из обязательного закрытого списка кодов. Хронология остаётся read-only, ограниченной конкретным отправлением и действующими правами доступа; по умолчанию свёрнута с возможностью раскрыть полную историю. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — краткая причина корректировки в хронологии отправления

Утверждено владельцем продукта: причина корректировки ошибочной приёмки показывается непосредственно в хронологии отправления рядом с событием корректировки, без необходимости открывать подробности события. Отображается понятное человеку наименование причины из утверждённого закрытого списка кодов; внутренний код при необходимости остаётся техническим значением и не подменяет пользовательский текст. Первоначальное событие приёмки и отдельное событие корректировки остаются видимыми. Историческое авторство и дата/время берутся из сохранённых данных события. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — повторная приёмка коробки после корректировки ошибочной приёмки

Утверждено владельцем продукта: если факт первоначальной приёмки коробки в рамках отправления был признан ошибочным и оформлена отдельная корректировка, допускается повторно принять эту же коробку в рамках того же отправления, когда она фактически поступила и все проверки приёмки пройдены. Повторная приёмка оформляется новым отдельным событием с собственной датой/временем и исполнителем; первоначальная приёмка и событие корректировки сохраняются без изменений. Повторная приёмка не является восстановлением или редактированием старого события. Перед выполнением сервер повторно проверяет принадлежность коробки исходному составу отправления, отсутствие уже действующего факта приёмки по этому отправлению, допустимое текущее состояние коробки, фактическую возможность приёмки, полномочия и scope сотрудника, а также отсутствие конфликтующих операций. Изменение custody/location и размещение на полке выполняются только в соответствии с отдельными правилами приёмки и размещения; повторная приёмка не должна автоматически отменять или переписывать реальные последующие перемещения и размещения. Операция и обязательное событие фиксируются атомарно. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — обновление статуса коробки после повторной приёмки

Утверждено владельцем продукта: после успешной повторной приёмки коробки в рамках того же отправления её текущий статус в карточке отправления автоматически обновляется на «Принята». Обновление выполняется по результату подтверждённой сервером операции, без ручного обновления страницы. Если после повторной приёмки коробка отдельно размещена на архивной полке, отображается соответствующий статус «Размещена на полке» с актуальными сведениями действующего размещения. Статус приёмки и статус размещения остаются разными фактами; повторная приёмка сама по себе не создаёт размещение и не меняет историю размещений. При ошибке операции интерфейс не должен показывать успешную приёмку или преждевременно менять статус. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — перемещение коробки между архивными полками

Утверждено владельцем продукта: перемещение коробки между архивными полками является отдельной операцией, а не редактированием существующей записи размещения. Выполнять её может активный сотрудник архива, обладающий полномочием на размещение коробок (`box_place`), в пределах своей операционной области. Сервер проверяет текущее действующее размещение коробки, её допустимое состояние и отсутствие несовместимых активных процессов, активность и доступность целевой полки в той же архивной области, полномочия сотрудника и scope. При успешном перемещении прежнее `BoxPlacement` закрывается, создаётся новое `BoxPlacement` для целевой полки и отдельное событие перемещения с датой/временем и исполнителем. История прежних размещений сохраняется; текущим считается новое действующее размещение. Проверки, закрытие прежнего размещения, создание нового размещения и обязательное событие выполняются атомарно и защищены от конкурентных операций. Перемещение не изменяет состав коробки, `BoxCycle`, membership, custody или учётную локацию. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — перемещение заполненных и пустых коробок

Утверждено владельцем продукта: перемещать между архивными полками разрешено как заполненные медицинскими картами, так и пустые коробки. Перемещается физическая коробка целиком; состав карт и memberships не изменяются. Для обоих случаев действуют одинаковые проверки полномочий сотрудника, scope, текущего размещения, целевой активной полки в той же архивной области и отсутствия несовместимых активных процессов. Перемещение оформляется закрытием прежнего `BoxPlacement`, созданием нового `BoxPlacement` и отдельного события в одной атомарной операции. Для пустой коробки перемещение не создаёт `BoxCycle` и не добавляет карты. История всех размещений сохраняется. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — отсутствие требования указывать причину перемещения коробки

Утверждено владельцем продукта: при обычном перемещении коробки между архивными полками причина не запрашивается. В интерфейсе нет обязательного поля причины, свободного комментария или дополнительного пояснения. Система автоматически фиксирует прежнюю и новую полки, дату/время и идентификатор сотрудника-исполнителя; отдельное событие перемещения и история размещений сохраняются. Это правило относится к обычному перемещению и не отменяет требования о причине для иных корректирующих операций, где такая причина отдельно установлена. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — история перемещений в карточке коробки

Утверждено владельцем продукта: в карточке коробки доступна история её перемещений между архивными полками. Каждая запись показывает прежнюю и новую полки, дату/время и ФИО сотрудника-исполнителя только из сохранённого исторического snapshot, если он имеется; при отсутствии snapshot текущее ФИО профиля не подставляется. Текущее действующее размещение показывается отдельно от истории. История доступна только сотрудникам, которым разрешён просмотр соответствующей коробки и её размещений, с применением действующих server-side проверок роли, scope и доступа к объекту. История read-only; просмотр не изменяет события и размещения. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — массовое перемещение коробок на одну архивную полку

Утверждено владельцем продукта: активному сотруднику архива с полномочием `box_place` разрешено за одну операцию перемещать несколько выбранных коробок на одну выбранную целевую архивную полку в пределах своей операционной области. Для каждой коробки сервер отдельно проверяет полномочия и scope сотрудника, наличие и актуальность действующего размещения, допустимое состояние коробки, отсутствие несовместимых активных процессов и доступность целевой активной полки в той же архивной области. Массовая операция выполняется атомарно: если хотя бы одна выбранная коробка не проходит проверку, ни одна коробка не перемещается. При успехе для каждой коробки отдельно закрывается прежнее `BoxPlacement`, создаётся новое `BoxPlacement` и записывается собственное событие перемещения с прежней и новой полкой, датой/временем и исполнителем. История размещений каждой коробки сохраняется; состав карт, `BoxCycle`, memberships, custody и учётная локация не меняются. Реализация и тестирование не выполнялись и не разрешены.


## 2026-11-21 — карты, числящиеся за филиалом, блокируют его деактивацию

Перед реализацией административной деактивации филиала необходимо обеспечить серверную проверку отсутствия медицинских карт, учётно числящихся за ним. Все такие карты предварительно переводятся явной операцией по предусмотренному бизнес-процессу в другое действующее подразделение либо в архив; автоматическое изменение принадлежности при деактивации запрещено. Проверка должна охватывать актуальное состояние карт и выполняться транзакционно с деактивацией и обязательной записью AdminSecurityAudit, с защитой от конкурентных изменений. Перевод карты не должен побочно переписывать custody, фактическую локацию или исторические события. Существующие блокирующие условия по активным сотрудникам и незавершённым операциям сохраняются. Реализация и тестирование не выполнялись и не разрешены.

## 2026-11-22 — бизнес-правила, подтверждённые после исходного readiness review

Следующие решения приняты и должны считаться закрытыми для UX-06; повторное согласование не требуется:

- Активные маршруты: филиал → архив (коробки), архив → ЦО (карты по заявке ЦО), ЦО → архив (коробки). Маршруты филиал → ЦО и ЦО → филиал исключены.
- Для ЦО → Архив отправление создаёт активный сотрудник ЦО с нужными полномочиями; отправление коробочное, курьер обязателен, фактическую передачу подтверждает автор, архив выполняет полную приёмку коробок. Размещение после приёмки отдельно.
- При приёмке коробок не пересчитывают карты внутри; количество карт не проверяется. Пломбы и упаковка не проверяются.
- Архив может атомарно массово размещать принятые коробки на одной полке и массово перемещать несколько коробок с одной/нескольких полок на одну целевую. Массовое извлечение карт из нескольких коробок запрещено.
- По заявке ЦО архив выдаёт медицинские карты частями. По каждой карте раздельно фиксируются выдача и фактическое получение ЦО; невыданные карты остаются ожидающими.
- Статус «Не найдена» устанавливается отдельно для карты с обязательной причиной из закрытого справочника; комментарий необязателен. Карта не списывается и не удаляется; после обнаружения её можно выдать в рамках той же заявки.
- До handover заявку отменяет только уполномоченный сотрудник архива; после handover отмена запрещена.
- ЦО подтверждает фактическое получение каждой карты; разрешено массовое атомарное подтверждение нескольких фактически поступивших карт. До подтверждения ответственность сохраняет архив; заранее подтверждать нельзя.

### Оставшиеся технические действия

1. Переписать прежние P-09 full-receipt statements в command contracts, data model, shipment route audit, object detail, invariants и очередях так, чтобы item-level issuance/receipt и partial outcomes были единообразны.
2. Добавить/проверить DB invariants для полного manifest и единого handover, item-level custody/receipt, исправления результатов только до draft и атомарного bulk receipt.
3. Сверить UI-карточку заявки и timeline: отдельные события по картам, статусы ожидания/выдачи/получения/не найдено/отменено, отображение причины и автора.
4. Синхронизировать progress и cross-document review; после этого выполнить полный перечень оставшихся UX-06 technical/security gates.

Эти действия не требуют новых бизнес-ответов по уже перечисленным правилам. Реализация по-прежнему не разрешена до формального завершения UX-06.
