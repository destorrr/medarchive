# UX-06 — Audit retention and integrity protection proposal

**Status:** technical/operational proposal; retention durations and deployment controls are not approved.  
**Prepared:** 2026-11-19  
**Baseline:** branch `docs/ux-audit-project`, HEAD `6294fd5bc05529bd3bff7bdf8936982d032b779f`.

## 1. Objectives and constraints

The policy must preserve reliable evidence for privileged actions and sensitive exports while minimizing personal data, limiting telemetry growth and supporting legally required preservation/deletion. It must not invent statutory retention periods or imply that a database-only control protects against a privileged database administrator.

Existing approved boundaries remain:
- AdminSecurityAudit is distinct from business DomainEvent and command ledger.
- Mandatory privileged mutation and successful-login audit writes fail closed in the same transaction/commit boundary.
- Ordinary reads are not automatically blocked by audit-store outage.
- Every 401/403 is not required to create a durable event.
- Mass card lookup/search is explicitly excluded, including large-result and cross-scope searches.
- Successful exports/downloads and report/manifest operations that export patient-identifying data remain in the audit scope.
- No source, schema, migration or runtime behavior is changed by this proposal.

## 2. Retention classes — no durations proposed

A single global TTL is not recommended. Assign each event a retention class through a closed catalog mapping, not a caller-supplied arbitrary value.

| Class | Event families | Retention treatment |
|---|---|---|
| R1 — privileged action evidence | Successful login, admin/user/role/reference-data changes, credential reset, P-07 corrections | Dedicated schedule approved by legal/records owner; preserve actor, target, action, outcome and references for the approved period |
| R2 — sensitive export evidence | Export generation/download and report/manifest export containing identifying data | Separate schedule; preserve export metadata and opaque artifact reference, not exported rows or file contents |
| R3 — bounded security telemetry | Failed/throttled login, rejected session, selected authorization denials, logout | Shorter, purpose-limited schedule; aggregates may be retained instead of raw event rows where operationally sufficient |
| R4 — integrity checkpoints | Signed checkpoint manifests, verification results and key-version metadata | Retain at least as long as the event ranges they attest to, plus any approved verification/audit period |

**Product-owner confirmation (2026-11-19): the medical organization currently has no approved retention periods** for administrative audit, security events or personal-data export records. No number of days/months/years is selected here. This is an explicit governance gap, not permission to retain records indefinitely or delete them ad hoc. Before production release, the organization's designated legal/privacy and records-management owners must assess jurisdiction, record category, limitation periods, healthcare-sector rules, investigation needs and applicable contracts, then approve a versioned schedule for R1–R4. Do not infer a period from a generic security standard.

Until that schedule is approved, the target policy remains incomplete. If an interim operating period is needed before final approval, it must be a separately approved, documented, time-limited policy with an owner, review/expiry date and hold/purge rules; this proposal does not invent interim durations.

### 2.1 Lifecycle and legal hold

- Retention starts from a defined authoritative timestamp, proposed as database `occurred_at`; the policy owner must confirm whether some classes start from event date, account closure, export completion or another trigger.
- A legal hold suspends scheduled purge for the scoped records and dependent checkpoint evidence. Hold creation/release must be authorized, attributable, time-bounded or periodically reviewed, and separately auditable.
- Hold metadata should reference a case/authority identifier and scope, not copy case documents or unnecessary patient data.
- Expiry must not mean silent physical deletion. A controlled purge/archive job should produce a minimal purge manifest with range, class, count, timestamp, policy version and executor identity, without copying deleted payloads.
- Backup retention and restore behavior must be consistent: restore must not resurrect expired records indefinitely or discard held records. Define how purge manifests are replayed after restore.
- If immutable backup copies cannot be selectively purged, document their bounded expiry and access restrictions; do not describe them as immediately deleted.
- Any statutory deletion/erasure obligation must be reconciled with evidentiary preservation by legal/privacy owners; application code must not independently decide conflicts.

## 3. Data minimization and digest policy

**Approved product decision (2026-11-21): do not collect or retain source-IP or attempted-identity digests for AdminSecurityAudit or bounded security telemetry in the initial product.**

- Do not store raw source IP addresses, IP hashes/digests, attempted usernames, login hashes/digests or other stable identifiers derived from these values in security-event payloads.
- Do not correlate security events across attempts by source IP or attempted identity.
- Use the separately approved login-throttle counters for their narrow rate-limiting purpose; throttle state is not an audit/event history and must not be exposed as an event-correlation facility. Its internal bucket-key design remains governed by the separate login-throttle contract and must not be copied into AdminSecurityAudit.
- Do not duplicate network/identity identifiers from infrastructure logs into the MedArchive audit records. Any independently operated infrastructure logging is outside this application audit decision and remains subject to its own ownership and retention policy.
- Reconsider collecting a specific identifier only after a concrete abuse-detection need, privacy/legal assessment and a new explicit product decision.

This decision removes source/identity digest collection from the initial audit scope. It does not remove the separate HMAC key requirements for protecting audit-record integrity.


## 4. Integrity and tamper-evidence design

### 4.1 Database baseline

- Keep the audit table append-only through database privileges, not only ORM conventions.
- `medarchive_runtime` receives only the minimum command/function permissions needed to append approved event types and read authorized views; no direct UPDATE/DELETE/TRUNCATE.
- `medarchive_owner` remains NOLOGIN. Migration and runtime credentials remain separate.
- Mutations of audit rows are prohibited. Corrections to audit interpretation/attribution are new typed correction records referencing the original event; the original bytes and metadata remain unchanged.
- Restrict direct access by migration/operations principals and alert on privileged maintenance access. A database superuser can bypass ordinary grants; this threat must be addressed outside the database.

### 4.2 Per-event authentication tag

Proposed integrity metadata:
- `integrity_version`;
- `key_version`;
- `payload_mac` (HMAC-SHA-256).

Compute the MAC over a deterministic canonical serialization of the immutable envelope and allowlisted payload, including event ID, schema version, event type, occurred_at, actor/target references, outcome/reason, command/correlation IDs and payload. Exclude mutable presentation fields; ideally there are none. Canonicalization algorithm/version must be explicit and test vectors frozen.

The HMAC key must be held outside PostgreSQL in an approved KMS/HSM or equivalent secret-management boundary. Runtime should not have unrestricted key export. If the application computes the MAC, the key must be available to the trusted audit-writing component; if a signing service is used, its availability and transaction semantics must be reconciled with fail-closed atomicity before selecting it. No external signing call may create a false claim of same-transaction atomicity.

HMAC detects modification only while the key remains protected. It does not by itself prove that an event was never deleted, nor protect against an attacker who can alter rows and obtain the MAC key.

### 4.3 External checkpoint manifests

A global linear hash chain is not recommended as the first design: concurrent PostgreSQL transactions do not naturally provide a simple total order, and serializing every audit insert behind one chain head can create contention and failure coupling.

Prefer periodic checkpoint manifests:
1. Select a closed, committed range of events using a stable monotonic ingestion/sequence boundary, with explicit handling for late commits and transaction visibility.
2. Canonicalize and hash each immutable event; create a deterministic manifest containing range boundaries, event IDs/hashes, count, schema/catalog versions and creation time.
3. Sign the manifest with a separate checkpoint-signing key.
4. Store the signed manifest in independently administered, retention-locked/immutable storage outside the primary PostgreSQL failure and credential domain.
5. Record checkpoint status and verification outcomes; alert on missing, delayed or invalid checkpoints.
6. On restore or audit, recompute event hashes and manifest signature and compare with the external checkpoint.

Checkpoint cadence, storage vendor, retention lock mode, signing algorithm and recovery objectives remain deployment decisions. A checkpoint only attests to events included in its committed range; the maximum undetected deletion window is bounded by checkpoint cadence and must be explicitly accepted.

### 4.4 Operational verification

- Scheduled verifier reads a bounded committed range, validates schema/canonicalization version, recomputes MACs and verifies external checkpoint signatures.
- Verification identity is read-only and separate from runtime/migrator.
- Failures create an alert outside the potentially compromised audit database and preserve the failed range/checkpoint for investigation.
- Monitor checkpoint lag, missing ranges, unexpected row count changes, key-version gaps, failed verification and privileged DB access.
- Backups must include required metadata and be tested for restore + integrity verification; backup success alone is not proof of audit integrity.

## 5. Audit access, export and access logging

- Global AdminSecurityAudit remains admin-only as previously approved; do not broaden roles.
- Prefer curated typed views and event-specific rendering over raw JSON payload display.
- Audit export should be a distinct privileged action with explicit permission, reason where appropriate, bounded date/range filters, row limits, secure artifact handling and expiry.
- Export contains only allowlisted audit fields; never export keys, raw credentials or prohibited payload data.
- Log successful audit exports and access to sensitive audit detail through a separate protected security/access channel or a carefully designed non-recursive mechanism. Avoid recursive insertion into the same feed on every event read.
- Failed export/access attempts follow the bounded denial policy.
- Define whether admin access to audit detail is itself audited and how the access log remains independently verifiable.

## 6. Decisions and evidence still required

Before implementation/DDL:
1. **Blocking governance gate:** establish an accountable legal/privacy and records-management owner, complete a jurisdiction/category assessment, and approve a versioned retention schedule for R1–R4, including hold, purge and backup-expiry rules. The product owner confirmed no such periods currently exist.
2. Confirm whether any IP/identity digest is necessary; default is none.
3. Confirm KMS/HSM capability, key custody, rotation, backup and destruction process.
4. Choose independent immutable checkpoint storage and checkpoint cadence after deployment topology is known.
5. Define threat model for DB superusers, infrastructure operators and backup administrators.
6. Define audit export permission, reason requirements, access logging and artifact expiry.
7. Inventory external log pipelines/SIEM/ETL/backup consumers and effective DB grants.
8. Define alert ownership, verification schedule, recovery objectives and evidence retention.

## 7. Acceptance criteria (not executed)

- Retention class cannot be selected or changed by arbitrary caller payload.
- Legal hold prevents purge and survives backup/restore reconciliation.
- Purge job is idempotent, emits a minimal manifest and never deletes held records.
- Runtime cannot UPDATE/DELETE/TRUNCATE audit data; ordinary roles cannot read global audit.
- Event MAC test vectors are stable across supported application versions.
- Tampering with any immutable envelope/payload field fails verification.
- Missing/deleted events within a checkpointed range are detected by manifest reconciliation.
- Checkpoint signatures verify independently of the primary database.
- Key rotation and old-key verification work across retention windows without exposing key material.
- Audit export is permission-checked, bounded, minimized, expires and produces an access record.
- Backup restore preserves holds, checkpoint references and integrity-verification capability.

No PostgreSQL, SQL/DDL, migration, application tests, CI or production checks were run for this proposal.

## 8. Non-goals

- No retention duration is invented or approved here; the organization currently has no approved schedule, and this remains a pre-production governance gate.
- No assumption that HMAC alone prevents deletion or privileged DBA tampering.
- No requirement to store raw IP, usernames, patient data or exported content in AdminSecurityAudit.
- No implementation or infrastructure change is authorized by this document.


## 9. Audit access and export

The companion proposal `UX-06-AUDIT-ACCESS-AND-EXPORT-POLICY-PROPOSAL.md` recommends admin-only read access; export as a distinct privileged action with a mandatory reason, bounded scope, field minimization and actor-bound expiring artifact; and a separate append-only access-log channel to avoid recursive writes into the audit feed. Export permission split, detail-view logging, feed-open telemetry failure behavior, artifact limits/expiry and access-log retention/integrity remain unresolved.


## 10. Audit-feed export is out of scope

Product owner decision (2026-11-19): exporting the AdminSecurityAudit feed is not предусмотрено. No audit-feed export endpoint, artifact or download flow is to be designed. This must not be confused with business-data export events, which remain in scope.

## 2026-11-21 — утверждена governance-политика сроков хранения AdminSecurityAudit

Владелец продукта утвердил следующий подход к срокам хранения `AdminSecurityAudit`: конкретные периоды сейчас не устанавливаются, поскольку у организации пока нет утверждённого графика хранения. До ввода системы в эксплуатацию организация должна назначить ответственного за юридические требования, персональные данные и архивное хранение. Этот ответственный организует оценку и утверждает отдельную версионированную политику для каждого класса событий R1–R4. Политика должна определять сроки и начальные точки их исчисления, порядок контролируемого удаления, приостановку удаления при legal hold, периодический пересмотр/снятие hold, а также согласование удаления с резервными копиями и восстановлением. До утверждения такой политики автоматическое удаление записей аудита запрещено; произвольное бессрочное хранение и ad hoc удаление также не допускаются. Конкретные сроки и юридические основания остаются открытым организационным вопросом. Решение не разрешает реализацию или изменение БД.
