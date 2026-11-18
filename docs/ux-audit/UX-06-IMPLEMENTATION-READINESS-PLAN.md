# UX-06 — сводный implementation/readiness plan

Дата: 2026-11-18  
Статус: план подготовки к реализации; не является разрешением на реализацию  
Основа: UX-06 storage model, technical data model, invariants/test matrix, DB security/transaction architecture, DB command mapping, DML/principals inventory, command contracts, grants matrix; решения D-01…D-18, P-01…P-09 и DB-SEC-01…04.

## 1. Резюме

Целевая модель МедАрхива требует не локального изменения одной таблицы или экрана, а согласованного перехода нескольких связанных контуров:

1. физическое хранение: Card → BoxCardMembership → BoxCycle → BoxPlacement;
2. групповые заявки ЦО: ArchiveRequest parent + ArchiveRequestItem;
3. единое отправление archive → central_office и атомарные handover/receive-all;
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
- До начала processing состав редактирует ЦО; после начала — только архив.
- Начало processing атомарно извлекает все подлежащие обработке карты.
- Архив фиксирует по каждой позиции результат: отправка, removed_by_archive или not_found.
- Оставшиеся карты уходят одним shipment и одним handover.
- Полная приёмка завершает отправление и родителя; частичная приёмка/частичная отправка запрещены.
- Отмена до handover и handover сериализуются; после фактической передачи отмена недоступна.

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
| F. Request/shipment workflows | parent request, items, extraction, cancel, single handover, receive-all | B, C, E, G | P-06/P-09/D-02 инварианты выполняются атомарно |
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
- create draft, submit, edit submitted;
- start processing + extraction одной транзакцией;
- remove item + возврат в коробку;
- mark not_found;
- cancel до/после processing с разными actor и атомарным возвратом;
- завершение без shipment, если все позиции имеют допустимые терминальные исходы.

**5C. Отправления**
- единственный archive → central_office shipment на parent;
- все оставшиеся extracted позиции в одном manifest;
- один handover;
- receive-all только получателем;
- завершение parent только после полной приёмки.

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
- CardPlacement оставить read-only на согласованный период; физическое удаление — отдельное решение.
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
| Handover | parent lock, один shipment, полный manifest, все items in_shipment, custody transfer, events | partial shipment или повторный handover |
| Receive all | полный manifest validation, все карты/коробки перемещены, shipment received, items completed, parent completed при выполнении условий | частичная приёмка или completed parent при непринятом shipment |
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
- exactly one fulfillment shipment per parent;
- manifest equals all and only outbound extracted items;
- parent cannot complete before all terminal results and receipt;
- parent cannot cancel after handover;
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
- edit submitted vs start processing;
- cancel vs handover;
- duplicate start processing;
- duplicate handover;
- duplicate receive-all;
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

### Продуктовые/операционные — требуют решения владельца продукта
1. **P-04/D-13 — списание медицинской карты:** определить основания, подтверждения и обязательные поля для инициирования ЦО и исполнения архивом. Открыто.
2. **P-07 — admin correction:** утвердить allowlist корректирующих действий, допустимые состояния, обязательные причины и границы before/after audit. Открыто.

### Миграционные и операционные планы — не новые продуктовые решения
3. **D-06 — viewer:** подготовить стратегию миграции существующих пользователей и legacy ACL; read-only аудит выявил потенциально чрезмерный history scope. Не закрыто.
4. **D-03 — legacy incidents:** подготовить отдельный план retirement/reconciliation старых exception shipment paths и UI, не возвращая incident в целевой процесс. Не закрыто.

### Технические решения и доказательства — требуют завершения до соответствующих DDL/cutover gates
5. **DB command idempotency:** аудит рекомендовал command ledger с уникальным command_id, server-side canonical input hash и версионируемым сохранённым результатом. Техническая рекомендация описана, но точные canonicalization, replayable negative outcomes и retention не утверждены. Не закрыто.
6. **DB result contract:** предварительно предпочтён versioned JSON result envelope с result_code/result_version; требуется зафиксировать контракт и error mapping. Не закрыто.
7. **DB schemas:** выбрать public или отдельные app/api schemas. Не закрыто.
8. **RLS/actor context:** определить необходимость RLS и окончательный механизм actor/scope enforcement; GUC не считать доказательством actor identity. Не закрыто.
9. **Login throttle:** выбрать SECURITY DEFINER command path либо строго ограниченные права; сохранить отсутствие широкого runtime DML. Не закрыто.
10. **Bootstrap:** спроектировать отдельный безопасный bootstrap path для первого admin и system locations с учётом owner/migrator/runtime principals. Не закрыто.
11. **Infrastructure principals:** определить backup/restore/monitoring/maintenance permissions по фактической deployment topology. Не закрыто.
12. **DomainEvent consumers:** имеющийся аудит выявил admin audit page и shipment history read path, но полный inventory всех внутренних/внешних consumers и compatibility plan не завершён. Не закрыто.
13. **Read permissions:** завершить таблицу SELECT/views/PII/scope по каждой роли и проверить фактическую ACL/RLS на целевой среде. Не закрыто.
14. **Rollout compatibility:** определить поддерживаемые версии приложения и dual-path период. Не закрыто.
15. **Legacy retention:** определить период read-only хранения CardPlacement и критерии безопасного удаления. Не закрыто.

### Технические вопросы, закрытые read-only аудитом
- **D-10/P-03 — физическая тара и BoxCycle:** текущая модель уже разделяет постоянную физическую тару Box и последовательные BoxCycle; ORM и интеграционные тесты подтверждают связь BoxCycle.box_id и повторное использование тары с новым циклом. Осталось сохранить эту семантику в target schema и миграции; отдельного продуктового решения сейчас не требуется.
- **D-17 — QR карты:** фактический parser contract извлечён из кода и интеграционных тестов; формат не менять. Перед реализацией сохранить совместимость и включить регрессионные проверки.
- **DomainEvent append-only UPDATE/DELETE:** миграция 0002 создаёт trigger и trigger function, запрещающие UPDATE/DELETE. Это не доказывает отсутствие INSERT abuse или обхода через привилегированные principals; effective grants и trigger bypass проверяются на preflight/cutover.
- **DomainEvent ownership и command ledger:** разделение authoritative records и рекомендуемая ledger-модель описаны в UX-06-EVENT-OWNERSHIP-AND-COMMAND-IDEMPOTENCY-AUDIT.md; это проектная рекомендация, не реализованная схема и не согласование всех параметров.

Не следует задавать эти вопросы повторно, если соответствующее решение уже дано в проектной переписке; этот список — реестр для закрытия только оставшихся пунктов.

## 11. Что не входит в этот план

- Не изменять код, ORM, API, UI, tests, migrations, workflows или deployment до отдельного разрешения на реализацию.
- Не удалять SQLite, legacy viewer, CardPlacement, incidents или старые ArchiveRequest до отдельных миграционных решений.
- Не менять формат QR карты.
- Не создавать новую сущность incident.
- Не внедрять дополнительные маршруты сверх D-18.
- Не выполнять production SQL, GRANT/REVOKE, backfill или data correction в рамках проектного этапа.
- Не считать текущую реализацию соответствующей целевой модели до фактического cutover и тестирования.

## 12. Текущее состояние и следующий шаг

UX-06 теперь имеет согласованный набор проектных документов: storage model, technical data model, DB invariants/test matrix, security/transaction architecture, command mapping, DML/principals inventory, protected DB command contracts, grants matrix и этот consolidated readiness plan.

Это означает, что основные технические зависимости и последовательность будущей реализации описаны. Это **не означает**, что все открытые решения закрыты или что реализация разрешена.

Следующий шаг — закрыть два продуктовых решения P-04/D-13 и P-07, завершить технические контракты/инвентаризации из раздела 10, выполнить read-only preflight по данным и deployment на согласованной непроизводственной копии, затем пройти итоговую проверку согласованности и Gate 0–2. UX-06 может быть представлен к рассмотрению после этих действий; реализация и production cutover требуют отдельных разрешений и gates.