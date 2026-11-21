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
| Typed command/event catalog | Завершить schemas, versions, event types, guards, outcomes и API mapping для P-04/P-07 и остальных critical workflows | Нужна однозначная DB/API/UI/test boundary до DDL |
| Admin/security audit policy | Retention/legal hold, IP/identity digest, HMAC key lifecycle, privileged-read scope for business-data exports, business-export access logging, tamper evidence, outage behavior | Без политики нельзя проектировать retention, индексы, key handling и безопасный failover |
| Command ledger retention | Составить retry horizon для browser clients, workers, scheduled jobs и integrations; определить reversible/irreversible command catalog | TTL до этого может разрешить повторное исполнение старого command_id |
| Migration/reconciliation | Legacy SQLite, viewer, incident, CardPlacement и admin event evidence; mapping/checksums/reconciliation/rollback | Нельзя безопасно планировать destructive retirement или cutover |
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

1. **Command contract normalization:** make all P-09 command sections agree on item-level issuance/receipt, partial fulfillment, per-card custody, bulk atomic receipt and partial cancellation. Resolve any obsolete full-receipt language, including historical text that is not clearly marked superseded.
2. **Read surfaces:** finish synchronizing object detail, request timeline, archive/CO queues, dashboard counters and card/box details with the latest item states and privacy scope.
3. **DB invariants:** validate DB-62…DB-70 against existing DB-01…DB-61, define exact state transitions and typed command signatures, and ensure guards preserve append-only history.
4. **Event catalog and idempotency:** complete producer/consumer mapping, event payload schemas/versions, ordinary command TTL/retry horizons and final API error envelope.
5. **Security and deployment:** complete effective grants/principals, role/location scope and read permissions, RLS decision, bootstrap/login throttle, infrastructure principals and rollout compatibility.
6. **Audit governance:** obtain retention/privacy/legal approval, key custody/KMS/HSM and checkpoint storage decisions; keep audit feed export excluded.
7. **Final verification:** complete static cross-document review and evidence checklist. Database preflight, SQL/DDL, pytest, CI and production readiness are separate gates and have not been performed in this documentation-only pass.

No further product question is currently required for the closed workflow rules above. UX-06 remains open until the documentation is fully reconciled and the required technical gates are explicitly dispositioned. UX-07 must not start earlier. No implementation is authorized.

## 2026-11-22 — technical reconciliation progress

The foundational storage model, DB transaction architecture and command mapping have now been amended to supersede the obsolete P-09 full-handover/full-receipt model. The current required workflow is item-level partial issue and actual receipt, archive custody until CO confirms actual receipt, atomic bulk receipt of arrived cards, and cancellation of only unissued remainder after partial issue. Ordinary box receipt and bulk placement/movement rules are also stated in the command and invariant contracts.

Remaining tasks:
1. Consolidate the old contradictory paragraphs in the main bodies of long technical documents so readers do not have to resolve addenda themselves.
2. Define exact parent/item persisted states, transitions and terminality, including the mixed state where some cards are received, some are not_found, some are still pending, and some have been issued but not received.
3. Reconcile DomainEvent catalog, consumer inventory, UI queues, dashboard counters and object timelines to those states.
4. Verify that DB-62…DB-70 align with earlier DB invariants and command signatures.
5. Finish security, grants, audit-retention and deployment gates, then run the final static cross-document review.

No further product clarification is currently necessary for these already approved rules. UX-06 remains open; UX-07 and implementation remain blocked.
