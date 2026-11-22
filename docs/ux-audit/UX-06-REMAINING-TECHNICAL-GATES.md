# UX-06 — остаточные технические и операционные gates

**Дата:** 2026-11-18  
**Базовый HEAD:** `d3fc9d541676ae40096875bfc4282482598e2de3`  
**Статус:** read-only audit synthesis; no implementation authorization.

## 1. Актуальные утверждённые основания

- P-04/D-13 и P-07 продуктово согласованы. P-07 card master-data allowlist пуст, включая `Card.phone`.
- DB-SEC-01…04 и command idempotency baseline утверждены.
- Один append-only/versioned DomainEvent stream используется для бизнес-фактов; отдельный AdminSecurityAudit — для security/privileged actions; command ledger — для idempotency/result replay.
- Для корректировки бизнес-агрегата DomainEvent и AdminSecurityAudit атомарны, но семантически различны; глобальный admin/security feed не дублирует операционные бизнес-события.

## 2. Остаточные gates и рекомендуемая последовательность

| Gate | Что ещё требуется | Почему блокирует |
|---|---|---|
| External consumers and grants | Подтвердить cron/systemd, ETL/BI, read replicas, ручные SQL consumers, интеграции и effective grants | Нельзя безопасно менять event schema/payload/retention, пока неизвестны читатели и обходные права |
| Deployment topology | Зафиксировать реальные runtime/migrator/backup/monitoring/maintenance identities, DB URLs и secret distribution | Нельзя окончательно проектировать grants, rotation и operational access на предположительной топологии |
| Typed command/event catalog | P-09 batch/item event types, command boundaries and transaction semantics normalized in UX-06. Still define exact integer schema versions, machine-validated payload schemas, typed API error mapping and remaining P-04/P-07 contracts | Нужна однозначная DB/API/UI/test boundary до DDL |
| Admin/security audit policy | Retention/legal hold, IP/identity digest, HMAC key lifecycle, privileged-read scope for business-data exports, business-export access logging, tamper evidence, outage behavior | Без политики нельзя проектировать retention, индексы, key handling и безопасный failover |
| Command ledger retention | Составить retry horizon для browser clients, workers, scheduled jobs и integrations; определить reversible/irreversible command catalog | TTL до этого может разрешить повторное исполнение старого command_id |
| Migration/reconciliation | Read-only preflight фактических SQLAlchemy/Alembic таблиц; additive P-09 поля `shipments.request_id`, `archive_request_items.fulfillment_batch_id`, `shipment_cards.request_item_id/received_by/received_at`, `cards.custody_location_id`; расширение `partially_received` и замена shipment-wide guards 0002; deterministic legacy request→parent/item map; доказуемое сопоставление shipment/item; custody/receipt reconciliation; checksums, quarantine и idempotent rerun; отдельно legacy SQLite, viewer, incident, CardPlacement и admin event evidence | Нельзя безопасно выполнять backfill/cutover или destructive retirement без точного mapping и отчётов по неоднозначным данным |
| Evidence matrix | Positive/negative role tests, DB grants, append-only, atomicity, concurrent replay, rollback, migration verification | Нельзя считать технические контракты реализуемыми без проверяемых критериев приёмки |

## 3. Утверждённая политика при недоступности audit store (2026-11-18)

Владелец продукта утвердил baseline **fail closed** для привилегированных изменений и успешного входа:

- не выполнять privileged mutation, если audit запись не может быть зафиксирована в той же транзакции;
- не выдавать новую authenticated session после успешной проверки credentials, если обязательное событие успешного входа не сохранено;
- не блокировать автоматически обычные чтения только из-за недоступности audit store;
- не превращать все 401/403 в синхронно обязательные audit writes без отдельной оценки шума/устойчивости; policy для denial events должна иметь отдельный bounded design.

Это утверждённое целевое поведение, но не реализация. Для privileged mutation обязательная AdminSecurityAudit запись и бизнес-изменение фиксируются одной транзакцией; при ошибке вставки/commit откатывается вся операция. При успешном входе новая сессия не выдаётся до commit обязательного auth-success события. Обычные чтения автоматически не блокируются. Синхронное обязательное журналирование каждого 401/403 не утверждено и остаётся отдельной ограниченной политикой. Текущая реализация не менялась и требует отдельного внедрения/проверки.

## 4. Неизменяемые ограничения

- Не менять master и не сливать PR без отдельного разрешения.
- Не менять application source, ORM, SQL/DDL, migrations, tests, workflows или deployment на этой стадии.
- Не запускать/не заявлять PostgreSQL, pytest, CI или production проверки без фактического запуска и разрешённой среды.
- Не удалять legacy SQLite/viewer/incident/CardPlacement до отдельного migration/retirement plan.
- Не открывать UX-07 до закрытия UX-06 gates и read-only preflight.

## 5. Evidence

Документ основан на статической сверке Markdown-документов и Git tree на указанном HEAD. Это не подтверждает состояние production grants, внешних jobs или deployment. Runtime/DB/CI/tests не запускались.


## 6. AdminSecurityAudit event catalog — 2026-11-19

Added `UX-06-ADMIN-SECURITY-EVENT-CATALOG.md` as a versioned design proposal. It separates mandatory transactional audit records from bounded denial/security telemetry and defines a proposed common envelope, per-event payload allowlists, prohibited data, read permissions and acceptance evidence.

**PR-READ-01 resolved (2026-11-19):** the product owner rejected audit records for mass card lookup/search, including large-result and cross-scope searches. Keep successful exports/downloads and report/manifest operations that export patient-identifying data in scope. Ordinary single-card views, routine search, bulk lookup, dashboards and object timelines are excluded.

Retention/legal hold, digest/IP and key lifecycle, business-data export/access logging, tamper evidence, external consumers/grants and deployment topology remain open technical/operational gates. No statutory retention period is assumed.


## 7. Retention and integrity controls — 2026-11-19

Added `UX-06-AUDIT-RETENTION-INTEGRITY-PROPOSAL.md`. It proposes R1 privileged action, R2 sensitive export, R3 bounded telemetry and R4 integrity-checkpoint retention classes, with no guessed durations; legal hold and controlled purge/backup reconciliation; no raw IP/identity digest by default; per-event HMAC with externally managed keys; and signed checkpoint manifests in independently administered immutable storage. A global linear hash chain is not recommended as first cut because concurrent transactions and serialized chain-head writes create ordering/contention concerns.

The product owner confirmed (2026-11-19) that the organization has no approved retention periods for administrative audit, security events or personal-data export records. Legal/records/privacy assessment and approval of a versioned R1–R4 schedule is therefore a blocking pre-production governance gate; no interim duration is inferred. Also requires digest necessity decision, KMS/HSM and key custody confirmation, checkpoint storage/cadence, DBA/operator threat model, business-data export audit/access logging and external consumer/grant inventory.


## 8. Audit access/export policy — 2026-11-19

Updated `UX-06-AUDIT-ACCESS-AND-EXPORT-POLICY-PROPOSAL.md` to the final admin-only read contract. The product owner rejected AdminSecurityAudit export entirely; no audit-feed export artifacts or export access-log events are in scope. Feed opening and individual event-detail views are not logged. Separate business-data exports/downloads remain auditable under the event catalog.


## 9. Audit-feed export decision (2026-11-19)

Historical decision, superseded 2026-11-19: the product owner later rejected AdminSecurityAudit export entirely. This earlier approval is not part of the current target contract.


## Owner decision — patient-identifying data in audit export (2026-11-19)

Historical decision, superseded 2026-11-19: the product owner later rejected AdminSecurityAudit export entirely. The earlier patient-identifier export permission, artifact controls and download evidence are not current requirements. This does not affect separately audited business-data exports.


## 10. Owner decision — no AdminSecurityAudit export (2026-11-19)

The product owner confirmed that exporting the AdminSecurityAudit feed is not needed at all. This supersedes sections 8–9 and the patient-identifier export approval above insofar as they describe audit-feed export.

Consequences:
- No AdminSecurityAudit export endpoint, UI, permission, command, file, artifact, download, artifact TTL or export-specific access log is required or permitted in the target product.
- Admin-only read access to the global feed and event details remains.
- Opening the feed and viewing details remain unlogged.
- Separate successful business-data exports/downloads of patient-identifying data and qualifying reports/manifests remain auditable under the event catalog; do not confuse them with audit-feed export.
- Retention/legal hold, HMAC/checkpoint integrity, external consumer/grant inventory and business-data export audit remain separate gates.

The organization has no approved audit retention periods; this remains a blocking pre-production governance gate. No duration is inferred. Documentation only; implementation is not authorized.

## 2026-11-22 — superseding decisions and revised UX-06 worklist

The product decisions below are closed and must not be re-asked: the three active routes; item-level partial archive-to-CO card issuance and receipt; archive custody until actual CO receipt; mandatory reason for not_found; partial cancellation after issuance; atomic bulk receipt of actually received cards; no card-count/seal/package inspection at box receipt; atomic bulk box placement and movement; no multi-box card extraction.

The remaining UX-06 work is now primarily documentation and technical readiness:

1. **Transport batch implementation contract:** one-to-many parent → fulfillment batches is the selected target model. Finish exact persisted columns/constraints, item-to-batch uniqueness, batch state projection, courier snapshot and migration/backfill design. This is technical specification work, not an open product decision.
2. **Command contract normalization:** make all P-09 command sections agree on item-level issuance/receipt, partial fulfillment, per-card custody, bulk atomic receipt and partial cancellation. Resolve any obsolete full-receipt language, including historical text that is not clearly marked superseded.
3. **Read surfaces:** finish synchronizing object detail, request timeline, archive/CO queues, dashboard counters and card/box details with the latest item states and privacy scope.
4. **DB invariants:** validate DB-62…DB-70 against existing DB-01…DB-61, define exact state transitions and typed command signatures, and ensure guards preserve append-only history.
5. **Event catalog and idempotency:** complete producer/consumer mapping, event payload schemas/versions, ordinary command TTL/retry horizons and final API error envelope.
6. **Security and deployment:** complete effective grants/principals, role/location scope and read permissions, RLS decision, bootstrap/login throttle, infrastructure principals and rollout compatibility.
7. **Audit governance:** obtain retention/privacy/legal approval, key custody/KMS/HSM and checkpoint storage decisions; keep audit feed export excluded.
8. **Final verification:** complete static cross-document review and evidence checklist. Database preflight, SQL/DDL, pytest, CI and production readiness are separate gates and have not been performed in this documentation-only pass.

No further product question is currently required for the closed workflow rules above. UX-06 remains open until the documentation is fully reconciled and the required technical gates are explicitly dispositioned. UX-07 must not start earlier. No implementation is authorized.

## 2026-11-22 — technical reconciliation progress

The foundational storage model, DB transaction architecture and command mapping have now been amended to supersede the obsolete P-09 full-handover/full-receipt model. The technical data model now specifies target relational links and additive migration/backfill rules for parent → batches, item → current batch and immutable ShipmentCard → request item, including composite parent-consistency FKs, manifest guards, quarantine of ambiguous legacy receipt/custody and idempotent reruns. The current required workflow is item-level partial issue and actual receipt, archive custody until CO confirms actual receipt, atomic bulk receipt of arrived cards, and cancellation of only unissued remainder after partial issue. Ordinary box receipt and bulk placement/movement rules are also stated in the command and invariant contracts.

Remaining tasks:
1. Consolidate the old contradictory paragraphs in the main bodies of long technical documents so readers do not have to resolve addenda themselves.
2. Define exact parent/item persisted states, transitions and terminality, including the mixed state where some cards are received, some are not_found, some are still pending, and some have been issued but not received.
3. Reconcile DomainEvent catalog, consumer inventory, UI queues, dashboard counters and object timelines to those states.
4. Verify that DB-62…DB-70 align with earlier DB invariants and command signatures.
5. Finish security, grants, audit-retention and deployment gates, then run the final static cross-document review.

No further product clarification is currently necessary for these already approved rules. UX-06 remains open; UX-07 and implementation remain blocked.


## 2026-11-22 — результат статической инвентаризации Alembic

Завершена проверка файловой цепочки всех Alembic revisions: граф линейный, единственная файловая head — `0011_login_throttles`. Похожие префиксы файлов `0007_*` не означают две ветви: один файл содержит revision `0007_transition_meta`, второй — revision `0009_external_org_custody`, следующий за `0008_shipment_integrity`.

Initial revision исполняет встроенную замороженную копию SQL, которая сама помечена draft/not production migration; отдельный `db/postgresql/001_initial_schema.sql` также draft. `migrations/env.py` использует `target_metadata = None`, поэтому ORM diff не автоматизирован. Файловая head не доказывает фактический head работающей БД.

**Снят вопрос о графовой развилке.** Остаточный DB preflight конкретизирован: получить read-only `alembic_version`; introspect PostgreSQL catalogs для таблиц/колонок/types/defaults/nullability, PK/FK/unique/CHECK/indexes, функций/триггеров и grants; сверить результат с ORM и каждой применённой revision; определить schema drift и bootstrap status. До доступа к реальной БД эти пункты остаются неподтверждёнными.


## 2026-11-22 — статическая сверка покрытия таблиц ORM и миграций

По именам таблиц ORM (16) сопоставлена с initial Alembic revision (15): единственное добавление после baseline — `card_placements` в revision `0003_card_placements`. `auth_login_throttles` создаётся revision `0011_login_throttles`, но не объявлена ORM-моделью и должна проверяться как SQL-managed объект. Это только проверка имён таблиц; сравнение колонок/ограничений/индексов/триггеров остаётся открытым и требует PostgreSQL catalog inventory.


## 2026-11-22 — dashboard queue/counter gate expanded

Static review of `postgresql_dashboard.py` shows current counters are based on legacy per-card ArchiveRequest statuses `new/in_progress` and shipment status `in_transit`, with broad from/to location predicates. This does not implement the approved target projections:

- Archive P-09 work queue: parent status `submitted` + `processing`.
- Archive navigation new-request badge: only parent status `submitted`.
- CO P-09 list: only own parent requests, with parent creator/owner scope.
- P-09 shipment counters: outstanding request items only; include `partially_received` batches and exclude already received items.
- Ordinary incoming shipment counters: original manifest boxes counted once by individual receipt state; placement does not affect receipt count.
- Shipment lists/counters: exact allowed route pairs and role direction, not any shipment touching a location.

Separate target read models and tests are required. Legacy dashboard SQL is not a valid implementation specification for target queues.


## 2026-11-22 — статическая проверка AdminSecurityAudit и DB principals

Проверены целевой event catalog, access policy, DB-SEC decisions, PostgreSQL admin service/router, ORM DomainEvent, Alembic revisions и draft initial SQL.

### Подтверждённые target decisions

- Глобальная лента и детали AdminSecurityAudit доступны только активной роли admin; прямые URL/API должны иметь server-side guard. Просмотр ленты/деталей не журналируется. Экспорт самой audit-ленты полностью исключён.
- DomainEvent остаётся журналом бизнес-фактов; AdminSecurityAudit — отдельным authoritative store для привилегированных/security действий. Для команды, затрагивающей бизнес-агрегат и требующей оба события, оба события и изменение должны фиксироваться атомарно.
- Не собирать raw IP, IP digest, attempted username/identity digest в initial security audit/telemetry. Массовый поиск карт не журналируется; успешные бизнес-экспорты персональных данных и qualifying reports/manifests остаются в scope.
- Организация не утвердила сроки хранения R1–R4; legal/privacy/records approval — блокирующий pre-production governance gate, без предположительного TTL.

### Статические implementation gaps

1. PostgreSQLAdminService._audit_admin_change() по-прежнему добавляет admin.* в DomainEvent и делает flush(). Отдельной ORM-модели/таблицы AdminSecurityAudit в просмотренной модели нет. Router commits происходят после вызова service, поэтому текущая запись DomainEvent попадает в ту же session/transaction как часть admin mutation, но это не реализует требуемое разделение журналов.
2. Текущие admin payloads шире target allowlist: user events включают username/full_name и branch reference; courier events включают full_name/phone; branch/shelf updates включают before/after values. Target catalog требует минимальные typed fields/changed-field names и запрещает произвольные model snapshots/секреты/PII. Требуется event-by-event payload mapping, а не копирование текущих JSON payload.
3. /admin/audit защищён require_pg_role("admin"), но читает общий DomainEvent с event_type LIKE 'admin.%'. Для target это должен быть read-only typed AdminSecurityAudit reader; legacy admin.* остаются историческими evidence, не переписываются.
4. DB migration содержит append-only trigger на UPDATE/DELETE domain_events. Это защищает от обычного изменения/удаления строк, но само по себе не доказывает ограничение INSERT, ownership separation или невозможность обхода суперпользователем/владельцем таблицы. Эффективные права и owners не проверялись.
5. Статический поиск по Alembic revisions и draft initial SQL нашёл только комментарий о REVOKE UPDATE/DELETE; исполняемых GRANT/REVOKE, CREATE ROLE/USER, default privileges или SECURITY DEFINER bootstrap в просмотренных миграциях не найдено. DB-SEC-01…04 прямо оставляют bootstrap principals/grants контролируемой DBA/infra процедурой. Следовательно, матрица фактических principals/grants не может быть выведена из репозитория.

### Остаточные security gates

- Специфицировать отдельную таблицу/ORM и DB command API AdminSecurityAudit, включая actor trust, event envelope, typed payload schemas и атомарные ссылки на DomainEvent/command ledger.
- Сопоставить каждый текущий admin producer с утверждённым event catalog и безопасным payload allowlist; отдельно определить судьбу legacy admin.* read path.
- Подготовить матрицу runtime/migrator/backup/monitoring/maintenance principals и grants по schema/table/sequence/function, затем сверить её с реальными pg_roles, pg_auth_members, information_schema/pg_catalog, ownership и default privileges на целевой БД.
- Подтвердить активную роль, server-side audit read authorization и запрет audit-feed export тестами; фактические grants/RLS и DBA threat model остаются внешним preflight.

Проверка статическая. PostgreSQL, pg_catalog, SQL, Alembic, pytest и CI не запускались; application source/ORM/DDL/migrations не менялись.


## 2026-11-22 — успешный вход: обязательный audit event отсутствует в текущем PostgreSQL path

Статически проверены src/medarchive/routers/auth.py и src/medarchive/services/postgresql_auth_service.py. Для PostgreSQL login пароль и активность пользователя проверяются внутри pg_session.begin(); login throttle обновляется/очищается в этой же транзакции. После выхода из транзакции router создаёт session token и устанавливает cookie. В этом пути не обнаружена запись authn.login_succeeded / auth.login_succeeded в AdminSecurityAudit или DomainEvent.

Target policy требует сохранить обязательный успешный login audit до выдачи session cookie; при невозможности commit audit запись сессия не выдаётся. Следовательно, текущая реализация не соответствует fail-closed target: транзакция authentication/throttle завершается без обязательного audit record, а cookie выдаётся после неё. Нужен единый transaction boundary, который фиксирует успешное authentication outcome и обязательный audit event до token/cookie issuance. При этом raw username/IP и их digests не включать в audit payload; throttle bucket остается отдельным механизмом.

Logout сейчас только удаляет cookie; по target catalog logout — best-effort telemetry, поэтому отсутствие durable mandatory logout event не блокирует текущую операцию. Это статический source finding; runtime auth flow и тесты не запускались.
