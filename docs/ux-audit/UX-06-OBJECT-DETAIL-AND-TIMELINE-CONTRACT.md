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
- Archive P-09 request queue: each row exposes only the request number, creation date, Central Office initiator, number of cards in the request and current status. Patient names and card numbers are not displayed in the queue; the complete item list is available only after opening an authorized request detail. Queue visibility does not broaden the archive staff member's request-detail or item permissions.

### 3.4 Central Office

- Card/box detail: only objects in the Central Office's current authorized custody/location scope, not every object that has ever passed through it.
- Shipment: only shipments for which Central Office is sender or receiver, permanently retaining its own manifest and timeline (P-08/D-09).
- Central Office shipment queue: each row exposes only the shipment number, route, creation date, sender, box count for incoming Central Office → archive shipments or card count for archive → Central Office shipments, and current status. Courier, patient names and card numbers are not displayed in the queue. The full manifest and shipment timeline remain available inside the shipment detail to authorized participants under P-08/D-09; queue minimization does not narrow that detail access.
- Archive request: the requesting Central Office role can open its own request and all its items/outcomes. It must not gain access to unrelated requests.
- Central Office P-09 request queue: each row for the requesting location exposes only the request number, creation date, number of cards and current status. Patient names and card numbers are not displayed in the queue; the complete item list and outcomes are available inside that user's own authorized request detail. The queue does not expose unrelated requests.

### 3.5 Viewer and other roles

The legacy `viewer` role is excluded from all target detail, search, dashboard and history routes (D-06). Existing accounts and ACL require a separate migration/containment plan; do not silently reinterpret them as another role.

No role obtains access solely from knowing a UUID, card number, box number, shipment ID or request ID.

## 4. Timeline composition and event visibility

Use the existing append-only, typed DomainEvent stream for business facts. Do not add a separate RequestEvent stream. The request aggregate and its items need explicit, typed event linkage so a parent timeline can reconstruct:
- request creation and submission;
- transition into processing and atomic card extraction;
- item removal with required archive comment;
- terminal not-found outcome (comment optional);
- cancellation with required reason;
- shipment creation and single full handover;
- full receipt and request completion.

For card timelines, include only card-relevant events and references. For box timelines, include cycle, membership, placement and shipment transitions without conflating physical box identity with cycle identity. For shipment timelines, include handover, receipt and allowed resolution facts, with manifest snapshot semantics.

A timeline query must apply authorization and event visibility in SQL/query construction before pagination and count calculation. It is not sufficient to fetch global events and hide rows in the template. The same predicate must govern:
- detail and timeline endpoints;
- search result snippets and autocomplete;
- dashboard summaries;
- pagination totals and exports;
- any future API or integration read path.

D-08 requires a branch-specific cutoff at the committed handover boundary. The exact implementation may use event-time/custody transition predicates or a persisted visibility boundary, but it must be deterministic under retries and concurrent reads. Do not infer visibility only from the card's current location, because that would make historical access change unpredictably.

## 5. Manifest and PII contract

P-08/D-09 grant sender and receiver access to the complete manifest, including card number and patient name, at all shipment stages and after completion. They do not grant access to a different shipment or to unrelated card history.

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
