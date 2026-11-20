# UX-06 — Object detail, timeline and read authorization contract

**Status:** design specification for review; not an implementation authorization.  
**Inspection baseline:** `docs/ux-audit-project`, HEAD `5779eeaea8f6504255ad10d90eaff696b5ff6b9b` (2026-11-18).  
**Scope:** card, box/logistical cycle, shipment and Central Office archive request.

## 1. Purpose and governing decisions

This contract translates D-07, D-08, D-09, D-11, D-16, P-08 and P-09 into object-detail read surfaces. It does not create a global operational history page and does not change any approved business process.

- Admin has global search and global access to each object's detail and object-scoped history, without requiring a synthetic `Location.kind='admin'`.
- Operational roles see only objects and history allowed by their current location, custody and shipment participation.
- A branch loses access to a card's subsequent history after handover to archive (D-08); this does not remove its permanent access to the manifest and timeline of its own shipment (P-08/D-09).
- The global audit surface is reserved for administrative and security audit. Operational DomainEvents remain business history attached to an object.
- Shipment manifest PII is visible to sender and receiver, throughout and after completion, but not to non-participants.
- Archive storage follows D-11: cards are stored in boxes; only boxes are shelf-placed. A temporarily extracted card during an archive request is not an independent shelf-placement mode.

## 2. Target routes and object identity

The following are logical route contracts, not a commitment to a specific URL syntax:

| Object | Detail surface | History source |
|---|---|---|
| Card | Card detail by stable card ID | Card aggregate events plus relevant custody/box/shipment references, filtered by viewer scope |
| Box | Physical box detail, with current cycle clearly distinguished | Box aggregate events, cycle transitions, membership and placement changes, shipment references |
| Shipment | Shipment detail and immutable manifest | Shipment aggregate events; manifest rows from shipment membership snapshots |
| Archive request | Parent request detail with all card items and item outcomes | Request/typed DomainEvent history linked to parent and items |

Object identity must not be inferred from a display number alone. Resolve the stable ID, then apply authorization before loading/rendering sensitive detail. Unknown and unauthorized identifiers should not disclose whether a protected object exists; the API may use a consistent not-found response where that is the established application convention.

The physical Box and its BoxCycle are distinct identities (D-10/P-03). Box detail must show the current cycle and retain access to prior cycles without overwriting their memberships or movement history.

## 3. Authorization model

Authorization is evaluated server-side for every detail and timeline request. Hiding navigation or omitting search links is not an access control.

### 3.1 Admin

Admin may open any card, box, shipment or archive-request detail and its object history globally. This path is role-based and must not call the operational `_user_location` resolver or require an admin Location row.

Admin object history is still object-scoped. It is not a substitute for, or a link to, a global operational event feed. Administrative/security audit remains a separate surface and data source.

### 3.2 Branch

- Card detail: only while the card is within the branch's authorized custody/location scope.
- Card timeline: only the events visible to the branch under D-08. Once handover to archive is committed, subsequent archive and downstream events must not be returned, including through pagination, search snippets, exports or alternate endpoints.
- Box detail/timeline: only boxes in the branch's authorized scope, plus the branch's own shipment context where P-08 requires it.
- Shipment: only shipments for which this branch is sender or receiver. The branch retains its own manifest and shipment timeline after receipt/handover completion (P-08/D-09).
- Archive request: no general access merely because a requested card originated in the branch.

### 3.3 Archive

- Card and box detail/timeline: objects in archive custody/scope, including the temporarily extracted state required by P-06/P-09.
- Shipment: only when archive is sender or receiver; retain its manifest and timeline after completion.
- Archive shipment queue: each row exposes only the shipment number, route, creation date, sender, box count for incoming branch/CO → archive routes or card count for archive → CO, assigned courier when present, and current status. Patient names and card numbers are not displayed in the queue. The full manifest and shipment timeline remain available inside the shipment detail to authorized sender/receiver participants under P-08/D-09; queue minimization does not narrow that detail access.
- Archive request: archive staff may open requests assigned to/handled by the archive and see all request items and their allowed outcomes. Request actions remain governed by P-06/P-09; read permission does not imply mutation permission.
- Archive P-09 working queue: includes submitted and processing requests that remain operationally open; cancelled and completed requests are excluded. Each row exposes only the request number, creation date, Central Office initiator, number of cards in the request and current status. Patient names and card numbers are not displayed in the queue; the complete item list is available only after opening an authorized request detail. Drafts are not visible to the archive. A request remains in the working queue throughout `submitted` and `processing`, including while its fulfillment shipment awaits full receipt by Central Office. The P-09 queue tracks the request lifecycle; the separate outgoing-shipment queue tracks the physical shipment. Cancelled and completed submitted requests are removed from the working queue, but their records and event histories remain retained for authorized historical review. For cancelled requests, the cancellation reason, actor and timestamp are retained. Historical availability does not broaden the archive staff member's request-detail or item permissions.

### 3.4 Central Office

- Card/box detail: only objects in the Central Office's current authorized custody/location scope, not every object that has ever passed through it.
- Shipment: only shipments for which Central Office is sender or receiver, permanently retaining its own manifest and timeline (P-08/D-09).
- Central Office shipment queue: each row exposes only the shipment number, route, creation date, sender, box count for outgoing Central Office → archive shipments or card count for incoming archive → Central Office shipments, and current status. Courier, patient names and card numbers are not displayed in the queue. The full manifest and shipment timeline remain available inside the shipment detail to authorized participants under P-08/D-09; queue minimization does not narrow that detail access.
- Archive request: while a P-09 request is `draft`, only its author in Central Office may open it. After transition to `submitted`, the request and its complete item list/outcomes are readable by all employees of the same Central Office location. This shared read access continues through later states and historical review, subject to the location scope. It does not grant mutation rights: after submission no Central Office employee may edit the item set or cancel the request; the cancellation authority remains with archive under P-09. No employee may open requests belonging to another Central Office location. All such employees may also read the complete request-scoped event timeline for the entire request lifecycle, including creation, submission, start of processing, item outcomes, cancellation and its reason, fulfillment-shipment creation and handover, and full receipt/completion where applicable. The timeline identifies the employee and timestamp for each recorded action and shows the recorded reason/comment where the workflow requires one. This does not expose unrelated card history or grant access to requests from another Central Office location.
- Central Office P-09 request queue: each row for the requesting location exposes only the request number, creation date, number of cards and current status. Patient names and card numbers are not displayed in the queue. Drafts are visible only to their author; submitted requests and their full details are visible to all employees of that Central Office location. The queue does not expose unrelated requests.

### 3.5 Viewer and other roles

The legacy `viewer` role is excluded from all target detail, search, dashboard and history routes (D-06). Existing accounts and ACL require a separate migration/containment plan; do not silently reinterpret them as another role.

No role obtains access solely from knowing a UUID, card number, box number, shipment ID or request ID.

## 4. Timeline composition and event visibility

Use the existing append-only, typed DomainEvent stream for business facts. Do not add a separate RequestEvent stream. The request aggregate and its items need explicit, typed event linkage so a parent timeline can reconstruct:
- request creation and submission;
- transition into processing and atomic card extraction;
- each item outcome committed independently as it is processed, including item removal with required archive comment;
- terminal not-found outcome (comment optional);
- cancellation with required reason;
- shipment creation and single full handover;
- full receipt and request completion.

For a submitted P-09 request, the parent request timeline is readable by every employee of the requesting Central Office location, including after completion or cancellation. It presents the complete request lifecycle and all request/item-linked business events: creation, submission, processing start, each item outcome (including any required archive comment), cancellation and its mandatory reason, fulfillment-shipment draft creation, actual handover, full receipt and completion where those events occur. Each event displays its actor and organization-local timestamp. An item outcome appears in the timeline as soon as its individual command commits; there is no batch-results confirmation step. The request remains in `processing` while unresolved items or subsequent fulfillment/receipt steps remain, and already committed item outcomes survive an interruption and are not repeated. This is request-scoped visibility only: it does not grant access to the independent card timeline or unrelated card events. The author-only rule continues to apply to a `draft` request.

For a P-09 request timeline, display events in reverse chronological order: newest event first, oldest last. Use the organization-local event timestamp as the primary sort key and a stable unique event identifier as the deterministic tie-breaker when timestamps are equal. The ordering changes presentation only; it does not alter event timestamps, append-only history, authorization scope or event retention.

Ordinary opening or viewing of a P-09 request or its timeline is not recorded as a business-history event. The request timeline contains business events that reflect lifecycle transitions or processing actions; passive reads do not append DomainEvents or change the request history. This rule does not specify or replace any separate administrative/security access-audit mechanism.

For card timelines, include only card-relevant events and references. For box timelines, include cycle, membership, placement and shipment transitions without conflating physical box identity with cycle identity. For shipment timelines, include handover, receipt and allowed resolution facts, with manifest snapshot semantics.

A timeline query must apply authorization and event visibility in SQL/query construction before pagination and count calculation. It is not sufficient to fetch global events and hide rows in the template. The same predicate must govern:
- detail and timeline endpoints;
- search result snippets and autocomplete;
- dashboard summaries;
- pagination totals and exports;
- any future API or integration read path.

D-08 requires a branch-specific cutoff at the committed handover boundary. The exact implementation may use event-time/custody transition predicates or a persisted visibility boundary, but it must be deterministic under retries and concurrent reads. Do not infer visibility only from the card's current location, because that would make historical access change unpredictably.

## 5. Manifest and PII contract

P-08/D-09 grant sender and receiver access to the complete manifest of their own shipment at all shipment stages and after completion. The fields in that manifest depend on the route and shipment type:

- For ordinary box shipments branch → archive and Central Office → archive, the manifest consists only of boxes (`ShipmentBox`). Individual cards are not manifest rows; the ordinary shipment detail must not expand box contents into card numbers or patient names for archive staff. Shipment participation does not by itself grant access to an independent card detail or card timeline; those reads use the separate object authorization rules.
- For the P-09 fulfillment shipment archive → Central Office, the manifest consists of individual cards (`ShipmentCard`). The sender and receiving Central Office may view the complete card manifest, including card number and patient name, throughout the shipment and after full receipt.

Manifest access does not grant access to a different shipment or to unrelated card history.

Other object detail pages should expose only fields necessary for the workflow and the role's task. Admin retains the globally identifying object view under D-07. Search previews must not reveal more than the corresponding detail authorization allows.

Audit/event payloads must not be rendered wholesale. Use explicit per-event field allowlists and redaction; never expose password hashes, credentials, session tokens, authentication secrets or unrestricted before/after JSON. This is a separate admin/security audit design requirement, not a reason to duplicate business facts in the global audit.

## 6. Current implementation gaps confirmed by static inspection

At the inspection baseline:

1. `/shipments/{shipment_id}` is the only confirmed full object detail with a DomainEvent timeline. Its `shipment_reader` permits branch, Central Office and archive, but not admin; the route also resolves an operational Location.
2. No universal PostgreSQL card detail or box detail/history route was found in the inspected router set. `/boxes/{box_id}/label` is a label endpoint, not a detail/timeline.
3. `/requests` is an archive queue; no parent request detail route was found. The present ArchiveRequest model is single-card; the approved multi-card parent/item model is a target migration, not current behavior.
4. Dashboard `/history` remains a global operational DomainEvent read path for admin and viewer; dashboard recent movements also omit a location predicate for those roles. Both conflict with D-06/D-16.
5. Search result cards are not links to implemented object detail routes. Do not add links until the destination route and its authorization predicate exist.
6. The current archive router still exposes `/placement/card` and `/placement/remove-card` shelf-placement actions. These are legacy behavior that conflicts with D-11; the target must remove standalone CardPlacement as an operational archive storage mode. Do not remove legacy schema/data before an explicit migration and retention plan.

These findings are static source inspection only. No HTTP requests, PostgreSQL queries, pytest or CI were run.

## 7. Required authorization and regression test matrix

| Scenario | Expected result |
|---|---|
| Admin opens any card/box/shipment/request detail and timeline | Allowed globally; no admin Location required |
| Admin dashboard | No operational DomainEvent feed |
| Admin global audit | Admin/security records only; no card/shipment business events |
| Branch opens card currently in branch scope | Allowed; only branch-visible history |
| Branch requests card history after archive handover | Post-handover events excluded, including count and later pages |
| Branch opens its own shipment after completion | Manifest and shipment timeline allowed |
| Archive opens request it is handling | Request and permitted item outcomes allowed |
| Request author opens own P-09 draft | Draft detail and item list allowed; other Central Office employees denied |
| Any employee of the requesting Central Office location opens submitted/processing/historical P-09 | Full request detail, item list, outcomes and complete request-scoped event timeline (actors, timestamps and recorded reasons/comments) allowed; unrelated card history and mutation denied |
| Central Office employee opens P-09 belonging to another location | Denied/not-found |
| Central Office opens its own request | All items and outcomes for that request allowed |
| Sender/receiver opens completed own shipment | Full manifest, including card number and patient name, allowed |
| Non-participant guesses shipment UUID | Denied/not-found without manifest disclosure |
| Viewer requests dashboard/search/detail/history directly | Denied |
| User guesses another location's card/box/request ID | Denied/not-found; no PII in response |
| Empty box after repack | Box remains shelf-placed per P-03; detail reflects warning/state without automatic removal |
| Archive card appears on shelf without a box | Invalid target state; no CardPlacement shelf mode |

Tests must cover both positive and negative authorization, direct route access, pagination/counts, search fragments and post-handover visibility. Existing tests encoding viewer access are legacy expectations and must be replaced only as part of an approved implementation/migration.

## 8. Decisions deliberately left open

This specification does not decide:
- exact URL names, page layout or navigation placement;
- whether object timelines are materialized read models or queried from DomainEvent;
- the precise persisted representation of the D-08 visibility boundary;
- final event names/version numbers for request parent/item transitions;
- legacy CardPlacement data retention/conversion and route retirement;
- exact PII field sets per operational role beyond the already approved D-07/D-09 access boundaries.

These require technical design and migration review; none should be guessed during UI implementation.

## 9. Non-goals and safeguards

- No global operational history page.
- No new RequestEvent entity.
- No synthetic admin Location.
- No new shipment routes or partial shipment behavior.
- No incident entity or revival of the legacy incident workflow.
- No direct runtime DML or weakening of the protected DB command boundary.
- No source, schema, test, workflow or deployment changes in this documentation-only stage.


## 10. Обычное входящее отправление: верхняя часть карточки

Для обычных отправлений коробок по маршрутам филиал → архив и ЦО → архив верхняя часть карточки содержит:

1. номер отправления;
2. маршрут;
3. текущий статус;
4. дату и время создания;
5. отправляющую локацию;
6. сотрудника, создавшего отправление;
7. количество коробок в манифесте;
8. назначенного курьера — отображаемое ФИО из сохранённого снимка на момент актуального назначения.

Ниже верхней части размещаются полный манифест коробок и история событий отправления. Для курьера отображается именно snapshot ФИО, сохранённый в отправлении: последующие изменения справочника не должны задним числом менять это значение. Если автор изменил назначение до подтверждения фактической передачи, в карточке показывается актуальное назначение и соответствующий ему snapshot; историю предыдущих назначений не вести.

Состав верхней части карточки не заменяет и не расширяет права доступа: полный манифест и история доступны только отправителю и получателю в соответствии с D-09/P-08. Содержимое коробок — номера медицинских карт и ФИО пациентов — в карточке обычного отправления сотрудникам архива не показывается. В манифесте отображается только список коробок; приёмка подтверждает получение коробок без просмотра и поштучной сверки вложенных карт.


## 11. История обычного отправления коробок: минимальный состав событий

Для обычных отправлений коробок по маршрутам филиал → архив и ЦО → архив промежуточные изменения чернового манифеста не образуют отдельные записи истории. В черновике хранится только его актуальный состав коробок; история добавления, удаления и замены коробок не ведётся.

История обычного отправления содержит только основные факты жизненного цикла:

- создание отправления;
- подтверждение фактической передачи;
- приёмка отправления.

Для этих событий сохраняются предусмотренные контрактом actor и дата/время. Для приёмки дополнительно сохраняется неизменяемый снимок списка коробок из манифеста на момент приёмки. Снимок состава коробок не является историей редактирования черновика. Отдельные события изменения состава и промежуточные снимки манифеста не создаются.

Это правило относится к обычным входящим отправлениям branch → archive и CO → archive и не изменяет отдельную историю fulfillment-отправления P-09.


## 12. Карточка обычного отправления: представление для сотрудника архива

Для сотрудника архива карточка обычного отправления коробок по маршрутам филиал → архив и ЦО → архив показывает только:

1. номер отправления;
2. маршрут;
3. текущий статус;
4. дату и время создания;
5. количество коробок;
6. манифест — список коробок.

ФИО сотрудника, создавшего отправление, и ФИО назначенного курьера в карточке для сотрудника архива не отображаются. Эти сведения не требуются архиву для приёмки коробок.

Это правило уточняет состав полей карточки именно для роли archive и имеет приоритет над общим перечнем полей верхней части карточки из раздела 10. Оно не меняет представление карточки для отправителя и не меняет права доступа других участников.

Манифест обычного отправления для сотрудника архива содержит только коробки. Содержимое коробок — номера медицинских карт и ФИО пациентов — не отображается. Приёмка остаётся подтверждением получения коробок без поштучной сверки вложенных карт.


## 13. История обычного отправления: ФИО участников событий для архива

В истории обычного отправления сотруднику архива показывается ФИО сотрудника, выполнившего каждое из трёх основных действий:

- создание отправления;
- подтверждение фактической передачи;
- приёмка отправления.

ФИО показывается непосредственно в соответствующей записи истории вместе с фактом действия и датой/временем. Это правило относится к истории событий и не отменяет ограничение раздела 12: в основной части карточки архива отдельные поля «создатель отправления» и «назначенный курьер» не отображаются.

ФИО курьера не является ФИО сотрудника, совершившего событие, и в истории событий по этому решению не добавляется. Промежуточные изменения чернового манифеста по-прежнему не образуют событий истории.


## 14. Отмена черновика: видимость в истории для сотрудника архива

Событие отмены обычного черновика отправления, произошедшей до подтверждения фактической передачи, не отображается сотруднику архива в истории отправления. Это уточнение относится к пользовательскому представлению истории и не меняет ранее согласованный состав отображаемых основных событий: создание, подтверждение фактической передачи и приёмка.


## 15. Исторические ФИО участников событий после деактивации учётной записи

Деактивация учётной записи сотрудника не скрывает и не удаляет его ФИО из уже созданных записей истории отправления. В истории сотруднику архива по-прежнему показывается ФИО сотрудника, выполнившего соответствующее действие, в том числе приёмку, независимо от текущей активности его учётной записи. Историческая запись отображает участника и факт действия на момент события; текущий статус учётной записи на видимость этой записи не влияет.


## 16. Изменение ФИО сотрудника

Изменение ФИО сотрудника после совершения действия не рассматривается как ожидаемый сценарий в рамках текущего UX-06. Отдельные правила исторического отображения при изменении ФИО и дополнительные снимки ФИО для этой ситуации не вводятся. Ранее согласованное требование сохранять видимость исторического ФИО после деактивации учётной записи остаётся в силе.


## 17. Точность времени в истории отправления для архива

В истории отправления сотруднику архива для каждого отображаемого события показываются дата и точное время его совершения. Одной даты без времени недостаточно. Требование относится к событиям создания, подтверждения фактической передачи и приёмки, которые входят в согласованный состав истории для архива.


## 18. Часовой пояс времени событий в истории

Дата и точное время событий, отображаемые в истории отправления для сотрудника архива, показываются в часовом поясе медицинской организации. Часовой пояс устройства сотрудника не влияет на отображение времени. Это правило относится к событиям создания, подтверждения фактической передачи и приёмки.


## 19. Обозначение часового пояса в истории

В истории отправления дата и время отображаются в часовом поясе медицинской организации без отдельного текстового обозначения часового пояса рядом с датой или временем.


## 20. Местонахождение коробок и карт во время перевозки

После подтверждения фактической передачи обычного отправления коробки, включённые в его манифест, и все находящиеся в них медицинские карты учитываются как находящиеся в пути в составе этого конкретного отправления. То же правило применяется к коробкам и картам в пределах соответствующего отправления по маршруту ЦО → архив.

На период перевозки прежняя локация не считается местом хранения переданных коробок и карт. Новая локация назначения становится их текущей локацией только после подтверждения приёмки отправления получателем. До приёмки связь объектов с отправлением сохраняется; отдельное отслеживание фактического местоположения курьера или коробок в пути не вводится.

Для карт, передаваемых по fulfillment-отправлению архив → ЦО в рамках P-09, после подтверждения фактической передачи действует тот же принцип: карты учитываются как находящиеся в пути в составе отправления до полной приёмки ЦО. До подтверждения фактической передачи извлечённые карты остаются под ответственностью архива согласно P-09.


## 21. Ответственность за объекты во время перевозки

Ответственность за коробки и находящиеся в них медицинские карты распределяется по этапам отправления:

- до подтверждения фактической передачи отправления ответственность остаётся у отправляющей локации;
- с момента подтверждения фактической передачи ответственность за перевозимые объекты несёт курьер, назначенный в этом отправлении и зафиксированный в нём на момент передачи;
- после подтверждения приёмки ответственность переходит к принимающей локации.

Для маршрута архив → ЦО по fulfillment-отправлению P-09 применяется тот же принцип: до фактической передачи извлечённые карты остаются под ответственностью архива, во время перевозки ответственность несёт назначенный курьер, после полной приёмки — ЦО. Курьер не является пользователем системы и не получает учётную запись или доступ в МедАрхив.


## 22. Смена курьера до фактической передачи

Если назначенный курьер изменён до подтверждения фактической передачи отправления, ответственность за перевозку после передачи несёт курьер, актуально назначенный в отправлении на момент подтверждения фактической передачи. После подтверждения фактической передачи назначение курьера и зафиксированный snapshot его ФИО неизменяемы.


## 23. История передачи курьеру

Отдельное событие передачи коробок или карт непосредственно курьеру с указанием ФИО курьера в истории отправления не создаётся. В истории остаётся согласованный факт подтверждения фактической передачи отправления сотрудником отправляющей локации; назначенный курьер и его snapshot ФИО доступны как атрибуты отправления. Ответственность курьера определяется назначением, зафиксированным на момент подтверждения фактической передачи.


## 24. Подтверждение назначенного курьера при передаче

При подтверждении фактической передачи отправления сотрудник не проходит дополнительное подтверждение личности или ФИО курьера и не подтверждает повторно, кому передаются коробки или карты. Достаточно назначения курьера, уже сохранённого в черновике отправления. Это назначение становится неизменяемым после подтверждения фактической передачи.


## 25. Деактивация назначенного курьера до передачи

Деактивация записи курьера в справочнике не блокирует подтверждение фактической передачи отправления, если этот курьер уже назначен в черновике. Это правило распространяется и на черновики, и на отправления, в которых фактическая передача уже подтверждена. Деактивация запрещает только новое назначение этого курьера; существующее назначение и сохранённый snapshot ФИО не изменяются.


## 26. Замена деактивированного курьера в черновике

Если назначенный курьер деактивирован до фактической передачи, сотрудник — автор черновика — может заменить его на другого активного курьера. Деактивированный курьер не может быть выбран для нового назначения. Замена обновляет ссылку на запись курьера и snapshot ФИО в черновике; отдельная история предыдущих назначений не ведётся. Если замена не выполнена, черновик с прежним назначением может быть передан по правилам раздела 25.


## 27. Замена назначенного курьера в черновике

Автор черновика обычного отправления вправе заменить назначенного курьера на другого активного курьера в любой момент до подтверждения фактической передачи. Право на замену не зависит от активности прежнего курьера: замена разрешена как при деактивации прежнего курьера, так и в обычной ситуации, когда он остаётся активным.

При каждой замене актуальная ссылка на запись курьера и snapshot отображаемого ФИО обновляются совместно. Сохраняется только актуальное назначение; история предыдущих назначений и отдельные события переназначения не ведутся. Изменение справочника курьеров не переписывает уже сохранённый snapshot.

После подтверждения фактической передачи назначение курьера и snapshot ФИО неизменяемы. Ответственность за перевозку несёт курьер, назначенный на момент подтверждения фактической передачи.

Правило применяется к обычным отправлениям филиал → архив и ЦО → архив. Оно не вводит курьерскую учётную запись или отдельную роль.


## 28. Черновик и отправка заявки P-09 в архив

Заявку P-09 создаёт сотрудник Центрального офиса сначала в статусе «Черновик». Пока заявка находится в черновике, редактировать её состав медицинских карт и выполнять действие «Отправить в архив» вправе только сотрудник ЦО, создавший этот черновик. Другие сотрудники ЦО не могут изменять состав карт или отправлять чужой черновик, даже если обладают общими правами на работу с заявками P-09.

Для передачи заявки в работу архива сотрудник ЦО выполняет отдельное явное действие «Отправить в архив». Только после этого заявка становится видимой в очереди архива и доступной архивным сотрудникам для обработки. Черновики P-09 не отображаются в очереди архива и не доступны архиву для обработки.

Отправка заявки в архив является отдельным переходом состояния и не означает начало обработки: обработка начинается позднее отдельным действием сотрудника архива. Отправка окончательно фиксирует набор позиций-карт. После перехода в `submitted` ни ЦО, ни архив не могут добавлять, удалять, заменять или перепривязывать позиции. Архив может только фиксировать результаты обработки существующих позиций; это не считается изменением состава.

Новые отправленные заявки должны быть заметны сотрудникам архива. Согласованный механизм — внутрисистемное информирование и общий счётчик заявок, ожидающих начала обработки, на элементе навигации к очереди P-09; правила его подсчёта приведены в разделе 29.

## 29. Внутрисистемное уведомление о новых заявках P-09

Для новых заявок P-09, отправленных сотрудниками Центрального офиса в архив, используется только внутрисистемное информирование в МедАрхиве. Новые заявки должны быть заметны всем активным сотрудникам архива, имеющим доступ к очереди P-09, независимо от того, кто впоследствии начнёт обработку конкретной заявки. Информирование реализуется через интерфейс системы и счётчик новых заявок на элементе навигации, ведущем к очереди P-09. Отправка уведомлений по электронной почте и другие внешние каналы в рамках этого решения не предусматриваются.

Это информирование не меняет доступность и жизненный цикл заявки: отправленная заявка сразу появляется в очереди архива, а начало обработки остаётся отдельным действием архивного сотрудника. Общий счётчик виден всем активным сотрудникам архива с доступом к очереди P-09 и равен количеству заявок в статусе `submitted`, ожидающих начала обработки. Когда любой сотрудник архива переводит заявку в `processing`, она исключается из счётчика для всех; отменённая до начала обработки заявка также исключается. Открытие очереди или карточки само по себе не уменьшает счётчик. Счётчик отражает общую очередь ожидающих обработки заявок, а не персональные непрочитанные уведомления.


## 2026-11-20 — UX-06: общий счётчик новых заявок P-09 в архиве

Владелец продукта согласовал общий, а не персональный счётчик новых заявок P-09 для всех активных сотрудников архива, имеющих доступ к очереди P-09. Счётчик показывает количество заявок, отправленных в архив и ожидающих начала обработки (статус «Отправлена в архив» / `submitted`). Когда любой сотрудник архива начинает обработку заявки и она переходит в `processing`, заявка перестаёт учитываться в общем счётчике у всех сотрудников. Если заявка отменена до начала обработки, она также перестаёт учитываться. Открытие очереди или карточки заявки само по себе не уменьшает счётчик. Таким образом, счётчик отражает количество заявок, ожидающих начала обработки, а не персональное количество непрочитанных уведомлений.


## 30. Авторство черновика P-09 в Центральном офисе

Черновик заявки P-09 может редактировать и отправить в архив только сотрудник Центрального офиса, который создал этот черновик. Другие сотрудники ЦО не вправе менять состав медицинских карт в чужом черновике или выполнять его отправку в архив, даже если имеют общие полномочия на работу с заявками P-09. Это ограничение относится к этапу черновика; оно не изменяет права сотрудников архива на обработку уже отправленной заявки.


## 31. Единственный незавершённый черновик P-09 на сотрудника ЦО

Один сотрудник Центрального офиса может иметь не более одного собственного незавершённого черновика P-09 одновременно. Пока этот черновик остаётся в статусе «Черновик», сотрудник не может создать второй. После отправки первого черновика в архив или его отмены ограничение снимается, и сотрудник может создать следующий черновик. Ограничение персональное: оно не препятствует другим сотрудникам ЦО создавать собственные черновики P-09.


## 32. Отмена черновика P-09 автором до отправки

До отправки заявки P-09 в архив автор черновика — создавший его сотрудник Центрального офиса — может самостоятельно отменить свой черновик. Указывать причину отмены не требуется. Другие сотрудники ЦО не могут отменять чужие черновики, даже если имеют общие полномочия на работу с заявками P-09. После отправки заявки в архив отмена по этому правилу недоступна; дальнейшие действия определяются жизненным циклом отправленной заявки P-09.


## 33. Отображение отменённых черновиков P-09 в реестре ЦО

После отмены черновик P-09 исключается из пользовательского реестра заявок Центрального офиса и больше не отображается автору в этом реестре. Это правило определяет только видимость в пользовательском интерфейсе: запись заявки и сведения о её отмене сохраняются в системе для аудита. Физическое удаление записи заявки и её истории не выполняется.


## 34. Отмена отправленной заявки P-09 до начала обработки

После отправки заявки P-09 в архив и до начала её обработки сотрудник Центрального офиса не вправе отменить заявку — в том числе её автор. Отменить такую заявку может только сотрудник архива, имеющий соответствующие полномочия. Для отмены архивом сохраняется ранее согласованное требование обязательного указания причины. Это правило относится к периоду после отправки заявки в архив и до перехода в состояние обработки; дальнейшие ограничения отмены определяются согласованным жизненным циклом P-09.


## 35. Отмена P-09 архивом после начала обработки до создания отправления

После начала обработки заявки P-09 сотрудник архива с соответствующими полномочиями может отменить заявку, если черновик отправления archive → Центральный офис для передачи оставшихся карт ещё не создан. При такой отмене указание причины обязательно. Правило действует до создания черновика отправления; после его создания применяются ранее согласованные атомарные правила отмены заявки и черновика отправления до подтверждения фактической передачи. После фактической передачи отмена заявки запрещена.


## 36. Обязательный комментарий при исключении карты из P-09 архивом

При первой фиксации исключения отдельной медицинской карты из заявки P-09 с результатом `removed_by_archive` сотрудник архива обязан указать комментарий с объяснением причины. Если результат исправлен на `extracted`, а затем повторно установлен `removed_by_archive`, новый комментарий не обязателен: первоначальный комментарий остаётся в истории первоначального решения, новое событие фиксирует переход, actor и время. Для `not_found` комментарий необязателен.

## 37. Неизменяемость состава отправленной заявки P-09

После отправки заявки P-09 в архив её автор и другие сотрудники Центрального офиса не могут изменять состав медицинских карт. Если после отправки требуется изменить состав, сотрудник архива отменяет заявку с обязательным указанием причины в соответствии с полномочиями и правилами её текущего состояния. После этого ЦО при необходимости создаёт новую заявку P-09 с требуемым составом. Редактирование уже отправленной заявки не допускается.


## 38. Неизменяемый состав и результаты обработки P-09

Набор позиций P-09 фиксируется при переходе `draft → submitted`. После этого каждая позиция сохраняет исходную идентичность и связь с картой; добавление, удаление, замена, перепривязка или физическое удаление позиции запрещены для всех ролей. Результаты `not_found` и `removed_by_archive` — статусы обработки уже существующих позиций, а не изменение состава. Такие позиции остаются видимыми в деталях заявки с результатом; для `removed_by_archive` обязателен комментарий, а карта возвращается в коробку архивом.

## 39. Исключение отменённых P-09 из рабочей очереди архива

Отправленная в архив заявка P-09 после отмены исключается из текущей рабочей очереди архива: она больше не требует операционных действий. Запись заявки не удаляется. Причина отмены, сотрудник, дата/время и история событий сохраняются для аудита и последующего просмотра уполномоченными сотрудниками. Это правило относится к отменённым отправленным заявкам; черновики ЦО и ранее не отображались в очереди архива. Завершённые заявки также не относятся к рабочей очереди.


## 40. Исключение завершённых P-09 из рабочей очереди архива

После перехода P-09 в `completed` заявка исключается из текущей рабочей очереди архива: все требуемые действия завершены. Запись заявки, состав и результаты обработки, сведения о fulfillment-отправлении и история событий сохраняются для последующего просмотра уполномоченными сотрудниками. Завершённая заявка доступна в историческом представлении, а не в рабочей очереди.


## 41. Видимость P-09 в Центральном офисе после отправки

До отправки заявки в архив (`draft`) карточку и полный состав P-09 видит только сотрудник ЦО, создавший черновик. После успешного перехода в `submitted` заявка и её полный состав становятся доступными для просмотра всем сотрудникам той же локации ЦО. Это право просмотра сохраняется в состояниях `submitted`, `processing`, `completed` и `cancelled`, включая исторический просмотр в пределах разрешённой области.

Общий доступ к просмотру после отправки не меняет полномочия: только автор управляет собственным черновиком до отправки; после отправки состав неизменяем для всех сотрудников ЦО, а отмена выполняется архивом по правилам P-09. Сотрудники другой локации ЦО не получают доступ к заявке. Просмотр полного состава не означает доступ к дальнейшей истории отдельной карты за пределами правил D-08/D-09.


## 42. Результат карты по умолчанию при обработке P-09

При успешном начале обработки все позиции P-09 атомарно получают `extracted`. В интерфейсе это отображается как результат «Найдена, готова к передаче». Сотруднику архива не требуется отдельно подтверждать найденную карту или нажимать для неё действие «Найдена».

Если карта фактически не найдена, сотрудник фиксирует `not_found`; если карта исключается архивом, фиксирует `removed_by_archive` с обязательным комментарием. Эти действия заменяют для соответствующей позиции исходный результат `extracted` и сохраняются независимо сразу после успешного выполнения. В истории заявки отражаются начало обработки/извлечение и отдельные события зафиксированных исключений; отдельное событие подтверждения найденной карты не создаётся.

## 43. Продолжение обработки P-09 другим сотрудником архива

Начало обработки P-09 не закрепляет заявку за сотрудником, который выполнил переход `submitted → processing`. Если он прерывает работу, любой другой активный сотрудник архива, имеющий доступ к очереди P-09 и конкретной заявке, может продолжить обработку незавершённых позиций и зафиксировать `not_found` или `removed_by_archive` по общим правилам. Отдельное назначение заявки ответственному сотруднику и передача ответственности между сотрудниками не требуются.

В истории P-09 для каждого действия указывается фактический сотрудник, который его выполнил: сотрудник, начавший обработку, и сотрудник (или сотрудники), позднее зафиксировавшие результаты позиций. Полномочия проверяются по активности учётной записи и доступу к конкретной заявке на момент выполнения команды; деактивированный сотрудник не может выполнять новые действия.

## 44. Исправление ошибочного результата позиции P-09

Ошибочно зафиксированные `not_found` и `removed_by_archive` можно исправить на `extracted`. Также разрешено повторно зафиксировать `not_found` для позиции со статусом `extracted`, поэтому до создания fulfillment-черновика допустим цикл `not_found → extracted → not_found`. Исправления выполняет любой активный сотрудник архива с доступом к заявке. После создания fulfillment-черновика изменения результатов запрещены.

Для `not_found → extracted` карта должна быть найдена в конкретной архивной коробке и извлечена; для `removed_by_archive → extracted` она повторно извлекается из коробки. Для `extracted → not_found` карта фактически не обнаружена, membership не создаётся и физическое размещение не меняется. При `extracted → removed_by_archive` карта возвращается в коробку. Все необходимые физические изменения, результат и новое событие выполняются одной транзакцией.

История заявки сохраняет каждое решение и исправление отдельным append-only событием с прежним/новым результатом, фактическим сотрудником и временем; исходные комментарии не переписываются. Объяснение исправления не требуется. Timeline содержит всю последовательность переходов. Исправления сериализуются с созданием fulfillment-черновика и отменой P-09.


## 9. Fulfillment shipment: назначенный курьер

В карточке fulfillment-отправления archive → CO показывается актуальное назначение курьера: snapshot ФИО, сохранённый при назначении. Курьер обязателен уже при создании черновика. До фактической передачи автор черновика может заменить его другим активным курьером из общего справочника; ссылка и snapshot обновляются вместе. История предыдущих назначений не отображается и не ведётся. Изменения справочника не переписывают сохранённый snapshot; деактивация уже назначенного курьера не снимает его с отправления. После фактической передачи назначение показывается как неизменяемое. В событии handover исполнителем остаётся сотрудник архива, подтвердивший передачу, а не курьер.


## 28. Переназначение автора черновика отправления

Если первоначальный автор черновика деактивирован до фактической передачи, администратор может переназначить авторство другому активному сотруднику той же отправляющей локации. Правило применяется к обычным отправлениям branch → archive и central_office → archive, а также к fulfillment archive → central_office по P-09.

Переназначение доступно только для отправления в `draft` до фактической передачи. Новый сотрудник должен быть активен и иметь роль, соответствующую отправляющей локации. После переназначения он получает права автора черновика: может выполнять разрешённое для текущей фазы редактирование, менять назначенного курьера и подтверждать фактическую передачу. Для P-09 неизменяемость состава после отправки заявки сохраняется.

Событие создания продолжает показывать первоначального автора. В истории отправления отдельным событием показываются администратор, прежний автор, новый автор и дата/время переназначения. Манифест, состав, назначение курьера и snapshot ФИО курьера не изменяются. Администратор не подтверждает фактическую передачу вместо нового автора. После handover переназначение невозможно.


## 45. Матрица доступных действий по состояниям P-09 и отправлений

Этот раздел сводит пользовательские действия в карточках с целевыми состояниями и DB-командами. Он определяет, какие действия интерфейс может предлагать; серверная команда в любом случае повторно проверяет actor, роль, локацию, авторство, состояние и все предусловия. Скрытие кнопки не заменяет серверную авторизацию.

### 45.1. Родительская заявка P-09

| Состояние/фаза | Доступные действия в карточке | Кто выполняет | Что недоступно |
|---|---|---|---|
| `draft` | Изменить состав карт; отправить в архив; отменить черновик без причины | Только автор черновика в ЦО | Другие сотрудники ЦО не редактируют и не отправляют чужой черновик; архив черновик не видит |
| `submitted` | Начать обработку; отменить заявку с обязательной причиной | Активный сотрудник архива с доступом к очереди и заявке | ЦО только просматривает; изменение состава и отмена со стороны ЦО запрещены |
| `processing`, fulfillment-черновик ещё не создан | Фиксировать результаты позиций; исправлять результаты по согласованным переходам; отменить заявку с обязательной причиной; создать fulfillment-черновик после фиксации результатов всех позиций | Любой активный сотрудник архива с доступом к заявке | ЦО не меняет состав, результаты и не отменяет заявку; после создания fulfillment-черновика исправление результатов запрещено |
| `processing`, fulfillment-черновик создан, передача ещё не подтверждена | Изменить курьера — автору fulfillment-черновика; подтвердить фактическую передачу — автору; отменить родительскую P-09 вместе с черновиком с обязательной причиной — уполномоченному сотруднику архива | Автор черновика для смены курьера и передачи; любой активный сотрудник архива с полномочиями — для отмены P-09 | Изменение результатов и манифеста запрещено; admin не подтверждает передачу вместо автора |
| `processing`, фактическая передача подтверждена | Просмотр состояния и ожидание полной приёмки ЦО | ЦО и архив в пределах доступа к заявке | Отмена, изменение результатов, состава, манифеста и курьера; повторная передача; частичная приёмка |
| `completed` или `cancelled` | Просмотр разрешённых деталей и полной истории заявки | ЦО той же локации и архив в пределах исторического доступа | Любые операционные изменения; заявка не входит в рабочую очередь архива |

При переходе `submitted → processing` все позиции атомарно получают `extracted`; отдельное действие «Найдена» не показывается. Для `removed_by_archive` комментарий обязателен при первой фиксации; при повторной фиксации после исправления новый комментарий не требуется. `not_found` можно исправить на `extracted`, если карта фактически обнаружена и извлечена. Все согласованные повторные исправления доступны до создания fulfillment-черновика и фиксируются в истории.

Если после обработки все позиции имеют окончательные результаты и ни одной карты не осталось к передаче, fulfillment-черновик не создаётся, P-09 автоматически становится `completed`. Если карты к передаче есть, создаётся ровно один fulfillment-черновик с обязательным курьером; создание черновика и фактическая передача — отдельные действия.

### 45.2. Fulfillment-отправление archive → CO по P-09

| Состояние | Доступные действия | Кто выполняет | Ограничения |
|---|---|---|---|
| `draft` | Заменить курьера; подтвердить фактическую передачу; отменить родительскую P-09 вместе с черновиком | Автор черновика меняет курьера и подтверждает передачу; уполномоченный активный сотрудник архива может отменить P-09 с обязательной причиной | Манифест неизменяем; admin может переназначить автора только если прежний автор деактивирован; admin не выполняет передачу |
| `in_transit` | Подтвердить полную приёмку | Активный сотрудник целевой локации ЦО с полномочиями приёмки | Частичная приёмка, отмена и изменение манифеста/курьера запрещены |
| `received` | Просмотр манифеста и истории | Отправитель и получатель в пределах постоянного доступа участника | Операционные изменения запрещены |

### 45.3. Обычные отправления коробок branch → archive и CO → archive

| Состояние | Доступные действия | Кто выполняет | Ограничения |
|---|---|---|---|
| `draft` | Изменить состав коробок; заменить курьера; подтвердить фактическую передачу; отменить черновик без причины | Только автор черновика | Другой сотрудник той же локации не редактирует, не передаёт и не отменяет чужой черновик; при деактивации автора admin может переназначить авторство активному сотруднику той же локации |
| `in_transit` | Подтвердить полную приёмку коробок | Активный сотрудник архива с полномочиями приёмки | Состав и курьер неизменяемы; отмена и частичная приёмка запрещены |
| `received` | Просмотр карточки, манифеста коробок и истории | Отправитель и архив как получатель | Только чтение; доступ к карточке и манифесту сохраняется после приёмки |

В карточке обычного отправления для архива показывается только манифест коробок; ФИО создателя и назначенного курьера в основной части карточки скрыты. Содержимое коробок (номера карт и ФИО пациентов) не показывается. Это не отменяет отдельно согласованного доступа к полной карточке карты и к манифесту fulfillment P-09 для его участников.

### 45.4. Разделение очередей и исторического просмотра

- Рабочая очередь P-09 архива содержит только `submitted` и `processing`; общий счётчик считает только `submitted`.
- Создание fulfillment-черновика не удаляет P-09 из рабочей очереди: заявка остаётся `processing` до полной приёмки или отмены.
- Очередь физических отправлений отдельно отражает движение shipment и не заменяет очередь заявок P-09.
- `completed` и `cancelled` P-09 исключаются из рабочей очереди, но сохраняются для разрешённого исторического просмотра.
- После `received` отправитель и получатель сохраняют доступ к карточке собственного отправления, манифесту и истории; это право чтения не возвращает право редактирования.

Любое действие в таблицах выше является подсказкой интерфейсу, а не обходом DB command boundary. Прямой вызов команды в недопустимом состоянии, другим actor или с неподходящей локацией должен завершаться отказом независимо от того, как сформирован клиентский интерфейс.
