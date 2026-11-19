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
| Admin/security audit policy | Retention/legal hold, IP/identity digest, HMAC key lifecycle, privileged-read scope, export/access logging, tamper evidence, outage behavior | Без политики нельзя проектировать retention, индексы, key handling и безопасный failover |
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

Retention/legal hold, digest/IP and key lifecycle, export/access logging, tamper evidence, external consumers/grants and deployment topology remain open technical/operational gates. No statutory retention period is assumed.


## 7. Retention and integrity controls — 2026-11-19

Added `UX-06-AUDIT-RETENTION-INTEGRITY-PROPOSAL.md`. It proposes R1 privileged action, R2 sensitive export, R3 bounded telemetry and R4 integrity-checkpoint retention classes, with no guessed durations; legal hold and controlled purge/backup reconciliation; no raw IP/identity digest by default; per-event HMAC with externally managed keys; and signed checkpoint manifests in independently administered immutable storage. A global linear hash chain is not recommended as first cut because concurrent transactions and serialized chain-head writes create ordering/contention concerns.

The product owner confirmed (2026-11-19) that the organization has no approved retention periods for administrative audit, security events or personal-data export records. Legal/records/privacy assessment and approval of a versioned R1–R4 schedule is therefore a blocking pre-production governance gate; no interim duration is inferred. Also requires digest necessity decision, KMS/HSM and key custody confirmation, checkpoint storage/cadence, DBA/operator threat model, audit export/access logging and external consumer/grant inventory.


## 8. Audit access/export policy — 2026-11-19

Added `UX-06-AUDIT-ACCESS-AND-EXPORT-POLICY-PROPOSAL.md`. It preserves admin-only global audit access; separates viewing from export; proposes mandatory export reason, bounded/minimized export, actor-bound expiring artifacts, and separate append-only logging for export/download facts only. Resolved: neither feed opening nor individual event-detail views are logged. Still open: artifact expiry/limits and whether audit exports may contain patient-identifying data.


## 9. Audit-feed export decision (2026-11-19)

The product owner approved audit-feed export for any existing `admin` user with a mandatory non-empty reason. No separate export permission or second-admin approval is required. Export must be bounded, field-allowlisted, protected and durably recorded. Other roles remain excluded.


## Owner decision — patient-identifying data in audit export (2026-11-19)

Resolved: AdminSecurityAudit feed exports may include patient-identifying fields, including patient full names and medical-card numbers. This permission is limited to explicitly allowlisted export columns and does not relax event-payload minimization or admin-only access. The export still requires a non-empty reason, bounded scope and volume, protected artifact handling, authorization recheck on download, and mandatory durable export/download evidence. Retention, legal hold and artifact expiry remain open gates. Documentation only; implementation is not authorized.
