# UX-06 — AdminSecurityAudit: event catalog proposal

**Status:** technical/product proposal for owner review; not an implementation authorization.  
**Prepared:** 2026-11-19  
**Baseline:** branch `docs/ux-audit-project`, HEAD `1659ad150acb9e2cdb95f4fa275c3c15a8b029cd`.  
**Scope:** event taxonomy, common envelope, payload minimization, bounded denial telemetry and privileged-read boundary.

## 1. Contract boundaries

- `AdminSecurityAudit` is the authoritative record for authentication/security actions and privileged administrative actions.
- `DomainEvent` remains authoritative for business facts. A privileged correction writes its typed DomainEvent and a semantically distinct AdminSecurityAudit record atomically; the audit record references the business event and does not copy its full payload.
- The command ledger remains authoritative only for idempotency/result replay.
- The global admin/security feed contains no ordinary card, box, shipment or archive-request lifecycle events.
- DB-SEC-01 remains the accepted actor trust baseline: application-authenticated actor, validated by protected DB command. Audit attribution is not independent cryptographic proof.
- **Approved fail-closed rule (owner decision, 2026-11-21):** every state-changing command whose contract requires a `DomainEvent` and/or `AdminSecurityAudit` record must commit the business mutation, its required event record(s), and the command-ledger result atomically. If any mandatory record cannot be written, the entire transaction rolls back and no business state change is committed. This applies to significant business operations (cards, boxes, shipments, archive requests) as well as privileged administrative mutations. A privileged correction that requires both event classes commits both in the same transaction. Successful-login audit must commit before session issuance; otherwise no session is issued. Ordinary reads are not automatically blocked by audit-store unavailability. Not every 401/403 requires a synchronous durable event; bounded denial telemetry remains non-blocking.

## 2. Event classes and proposed catalog

The catalog distinguishes **mandatory transactional records** from **bounded security telemetry**. A telemetry event must never become an implicit availability dependency for an ordinary request. This exception does not weaken the atomic-write requirement for mandatory business or security records explicitly required by a command contract.

### 2.1 Mandatory transactional events

| Event type | Trigger | Required target/context | Atomicity |
|---|---|---|---|
| `authn.login_succeeded` | Credentials and account state accepted | actor user, authentication method, outcome | Audit commits before session issuance; failure means no new session |
| `admin.user_created` | Admin creates user | target user ID, assigned role/scope summary | Same transaction as mutation |
| `admin.user_profile_updated` | Allowlisted non-credential user attributes change | target user ID, changed field names only | Same transaction as mutation |
| `admin.user_role_changed` | Role/privilege assignment changes | target user ID, old/new role codes | Same transaction as mutation |
| `admin.user_activated` / `admin.user_deactivated` | Account active state changes | target user ID | Same transaction as mutation |
| `admin.credential_reset` | Admin resets/replaces credential | target user ID, reset mechanism code; never credential material | Same transaction as mutation |
| `admin.reference_data_created` / `admin.reference_data_updated` / `admin.reference_data_activated` / `admin.reference_data_deactivated` | Branch, courier, shelf or other approved reference-data mutation | target type/ID, operation, changed field names | Same transaction as mutation |
| `admin.correction_applied` | Approved P-07 correction command | aggregate type/ID, correction type, reason reference/code, source DomainEvent ID, resulting DomainEvent ID | Same transaction as business mutation and DomainEvent |

The reference-data family is a taxonomy proposal; implementation should use explicit event types per aggregate/operation or a closed typed discriminator, not an arbitrary free-form event name. Existing `admin.*` DomainEvent rows remain legacy evidence and must not be rewritten or deleted as part of a future migration.

A failed privileged command may have an outcome record only if its event policy and transaction semantics are explicitly defined. Do not force a failed command to write into the same transaction that is intentionally rolled back; do not misrepresent a failed mutation as a committed successful audit fact. A separate bounded denial/security telemetry path is described below.

### 2.2 Bounded security telemetry

| Event type | Trigger | Proposed handling | Blocking behavior |
|---|---|---|---|
| `authn.login_failed` | Invalid credentials or inactive/unknown principal | Count/rate-limit by keyed identity and source buckets; optionally emit sampled/threshold-crossing events | Never reveal whether account exists; telemetry failure does not turn a failed login into success |
| `authn.login_throttled` | Throttle blocks an attempt | Emit threshold/cooldown transitions or bounded aggregates, not one durable row per retry | No new session; telemetry write is not a second availability dependency |
| `authn.session_rejected` | Invalid, expired, stale-credential or otherwise rejected session | Aggregate by safe reason and bounded source/principal digest; avoid per-request flood | Request remains rejected regardless of telemetry availability |
| `authz.access_denied` | Authorization check returns denial | Routine expected denials are not written to the business journal or as one durable audit row per attempt. Use a separate, access-restricted security telemetry channel only for explicitly selected high-risk classes or threshold crossings; group repeated denials and enforce rate/volume limits | Request remains denied; telemetry failure or unavailability does not block request processing |
| `authn.logout` | Explicit user-initiated logout | Approved best-effort security event; not a mandatory transactional audit write | Telemetry failure/unavailability must not delay or prevent logout or credential clearing; browser close/network loss without explicit logout is not guaranteed to be recorded |

For each telemetry family, the implementation contract must specify: reason-code enum, aggregation key, window, threshold, maximum event rate, retention, and behavior when the telemetry store is unavailable. Do not log a raw HTTP response body, SQL error, stack trace or unbounded exception string. No blanket synchronous durable write for every 401/403 is proposed.

**Owner decision (2026-11-21) — access-denial handling:** routine failed authorization attempts are excluded from the main business journal (`DomainEvent`) and are not individually persisted as mandatory `AdminSecurityAudit` rows. If security monitoring is needed for selected high-risk denials, record it only in a separate, restricted and bounded security-telemetry path. Repeated denials must be grouped/aggregated and rate-limited so a burst cannot flood storage. Telemetry is best-effort: its unavailability or write failure must not block the request path or change the authorization outcome. The denial itself remains denied. This does not weaken atomic persistence of mandatory records explicitly required by a command contract.

### 2.3 Privileged reads and exports

**Approved initial scope (product decision, 2026-11-19):**
- Audit successful bulk export/download of patient-identifying or medical-card data.
- Audit generation/download of a manifest or report when the operation exports data outside the normal object-detail UI.
- Do **not** audit bulk lookup/search, including large result sets and cross-scope searches, even when the operation covers many cards or extends beyond ordinary viewing.
- Do not audit ordinary single-card views, normal search pages, dashboard reads or object-history views.
- Failed export attempts follow the bounded denial policy; do not make routine reads depend on a synchronous audit write.

Event types in scope:
- `data_export.generated`
- `data_export.downloaded`

`data_bulk_lookup.executed` is explicitly out of scope and must not be introduced as an AdminSecurityAudit event without a new product decision.

Required context: actor, operation code, data category, export format, outcome, correlation ID and timestamp. Do not store exported rows, patient names, card numbers, raw filters containing identifiers, file contents or download tokens in the audit event. If a generated artifact needs its own lifecycle record, reference an opaque artifact ID and apply separate access controls/retention.

## 3. Common event envelope

Proposed required fields:

| Field | Rule |
|---|---|
| `event_id` | Server-generated immutable UUID; unique |
| `schema_version` | Positive integer for the envelope contract |
| `event_type` | Closed, namespaced event type from the catalog |
| `occurred_at` | Database-generated UTC timestamp at the authoritative write boundary |
| `actor_user_id` | Authenticated actor UUID; nullable only for unauthenticated/unknown-principal telemetry |
| `actor_role_code` | Role snapshot at action time; nullable for unknown actor |
| `actor_scope_ref` | Minimal stable scope reference where relevant; avoid duplicating full ACL |
| `outcome` | Closed enum such as `succeeded`, `failed`, `denied`, `throttled` |
| `reason_code` | Closed event-family enum; required for failures/denials and where business correction policy requires it |
| `target_type`, `target_id` | Typed target reference where known; nullable for unknown login principal |
| `command_id` | Idempotent command UUID for command-backed actions; nullable for non-command telemetry |
| `correlation_id` | Request/workflow correlation UUID; required where available |
| `source_channel` | Closed enum such as `web_ui`, `api`, `operator_tool`, `scheduled_job` |
| `payload` | Strict per-event allowlisted object, validated against event type and schema version |

**Approved data-minimization decision (2026-11-21):** the initial product does not collect or retain raw source IP, IP digests, attempted usernames or identity digests in AdminSecurityAudit or bounded security telemetry, and does not correlate security events by source IP or attempted identity. Login-throttle counters remain a separate, narrowly scoped mechanism under their own contract; their internal bucket keys must not be copied into audit events or exposed as event-correlation identifiers. Reconsideration requires a concrete abuse-detection use case, privacy/legal assessment and a new explicit product decision.

## 4. Per-event payload and prohibited data

Payload schemas are allowlists, not generic model dumps. Prefer stable codes and references over labels or copied business data.

**Allowed examples**
- changed field names and stable role/status codes;
- target UUID and target aggregate type;
- correction reason code and a short bounded operator reason only where required by P-07;
- source/result DomainEvent IDs;
- authentication method and generic outcome;
- data category, export format and result-count band;
- stable reason codes and bounded counters.

**Prohibited**
- passwords, password hashes, reset secrets, MFA secrets or recovery codes;
- session cookies, bearer/access/refresh tokens, signing keys or raw credentials;
- full HTTP request/response bodies, unrestricted ORM objects or arbitrary JSON patches;
- SQL text, stack traces and unbounded exception messages;
- patient names, card numbers, dates of birth, phone numbers or exported row contents unless a separate approved, strictly necessary field is documented (none is proposed here);
- raw attempted usernames, raw source IP, IP digests or attempted-identity digests in security-event payloads;
- file contents, download URLs containing secrets, and unbounded search filters.

All payloads require maximum size/depth and schema validation. Unknown keys and unsupported schema versions must be rejected rather than silently persisted/rendered.

## 5. Read permissions and presentation

- **Approved read-access decision (2026-11-21):** only an active `admin` may read the global `AdminSecurityAudit` feed and event details. `branch`, `central_office` and `archive` receive neither the global feed nor any audit-payload endpoint. The same server-side restriction applies to direct URLs/API requests; UI hiding is not an authorization boundary.
- The event list renders event-specific labels and allowlisted fields; no generic raw-JSON viewer.
- Audit detail access should be narrower than general admin object access where payload contains security context. Admin's global object/history access does not justify exposing secrets or unnecessary PII in audit.
- Audit read operations remain admin-only. Opening the feed and viewing event details are not logged; no audit-feed export or export-specific access log exists in the target product. Separate business-data export operations remain audited under their own event types.
- Business history remains in the object's DomainEvent timeline with its existing object-level authorization.

## 6. Retention, integrity and operational gates — unresolved

No statutory or universal retention period is inferred. Before schema/DDL, the owner and operational/legal stakeholders must define:
1. retention by event family (mandatory admin/authentication, bounded denial telemetry, and business-data export events);
2. legal hold, preservation, deletion/archival and backup/restore behavior;
3. confirm implementation conformance with the approved exclusion of source/identity identifiers and event correlation;
4. HMAC key custody, separation, rotation, version overlap and destruction;
5. business-data export permissions, access logging and artifact lifecycle;
6. tamper-evidence requirements, privileged DBA threat model, backup immutability and independent monitoring;
7. event-volume limits, partitioning/index strategy and alerting for failed mandatory writes versus telemetry drops.

Append-only protections and least-privilege INSERT/SELECT grants remain required. Append-only at the application layer alone is insufficient; the eventual DB contract must prevent runtime UPDATE/DELETE and restrict who can read/insert.

## 7. Acceptance evidence required before implementation

- Catalog enum and payload schema validation tests for every event type/version.
- Atomic rollback tests for privileged mutation + DomainEvent + AdminSecurityAudit.
- Login tests proving no session/cookie issuance before successful audit commit.
- Negative tests proving ordinary roles cannot read global audit and runtime cannot update/delete audit rows.
- Privacy tests scanning serialized payloads and rendered UI for prohibited fields.
- Rate/volume tests for denial telemetry and proof that telemetry failure does not convert a denial into success or block ordinary reads.
- Export tests proving event emission contains metadata only, not exported patient rows or secrets.
- Migration reconciliation proving legacy `admin.*` evidence is preserved and old records are not rewritten.
- Operational evidence for retention, key rotation, backup/restore, tamper detection and audit-read access.

All are acceptance criteria only; none were run in this documentation step.

## 8. Product decision recorded

**PR-READ-01 — resolved (2026-11-19):** the product owner rejected auditing mass card lookup/search. This exclusion applies even to large result sets and cross-scope searches. The initial privileged-read audit scope is limited to successful exports/downloads of patient-identifying or medical-card data and report/manifest generation or download that exports such data outside the normal object-detail UI. Ordinary views, routine search, bulk lookup, dashboards and object timelines are not AdminSecurityAudit events by default. Reconsideration requires a new explicit product decision.

Retention periods, HMAC/IP policy and tamper-evidence controls are not product defaults to guess; they remain technical/operational gates for a later evidence-based decision.

## 9. Non-goals

- No implementation, DDL, migration, API/UI change or new session registry.
- No second business event stream or RequestEvent.
- No new incident entity.
- No durable row for every 401/403.
- No claim that current runtime behavior already satisfies this target catalog.


## 10. Retention and integrity proposal

See `UX-06-AUDIT-RETENTION-INTEGRITY-PROPOSAL.md` for proposed event retention classes (without guessed durations), legal-hold/purge lifecycle, approved exclusion of raw IP/identity digests, per-event HMAC and externally signed checkpoint design. This is a technical/operational proposal only; retention schedules, key custody, checkpoint infrastructure and business-data export audit controls remain unapproved.


## 11. Audit access and export policy proposal

See `UX-06-AUDIT-ACCESS-AND-EXPORT-POLICY-PROPOSAL.md`. It retains admin-only feed/detail read access, prohibits export of the AdminSecurityAudit feed entirely, and confirms that ordinary feed/detail reads are not logged. Separate business-data export events remain in scope under section 2.3.


## 12. Product decision — admin audit-feed export approved (2026-11-19)

Any user with the existing `admin` role may export the AdminSecurityAudit feed with a mandatory non-empty reason. No separate export permission and no second-admin approval are required. Export must remain bounded, field-allowlisted, attributable and protected; the export outcome is recorded. This does not grant export access to branch/archive/CO roles and does not change the audit catalog for business-data exports.


## Owner decision clarification — audit-feed export excluded (2026-11-19)

The product owner rejected export of AdminSecurityAudit itself. Any references in this catalog to successful exports/downloads or report/manifest operations concern **business data leaving ordinary object viewing** (for example, patient-identifying card data or qualifying reports/manifests), not export of the AdminSecurityAudit feed. Do not add audit-feed export events, commands, artifacts or access-log records. The existing business-data export event scope remains unchanged.

## 2026-11-21 — утверждена governance-политика сроков хранения AdminSecurityAudit

Владелец продукта утвердил следующий подход к срокам хранения `AdminSecurityAudit`: конкретные периоды сейчас не устанавливаются, поскольку у организации пока нет утверждённого графика хранения. До ввода системы в эксплуатацию организация должна назначить ответственного за юридические требования, персональные данные и архивное хранение. Этот ответственный организует оценку и утверждает отдельную версионированную политику для каждого класса событий R1–R4. Политика должна определять сроки и начальные точки их исчисления, порядок контролируемого удаления, приостановку удаления при legal hold, периодический пересмотр/снятие hold, а также согласование удаления с резервными копиями и восстановлением. До утверждения такой политики автоматическое удаление записей аудита запрещено; произвольное бессрочное хранение и ad hoc удаление также не допускаются. Конкретные сроки и юридические основания остаются открытым организационным вопросом. Решение не разрешает реализацию или изменение БД.


## 2026-11-21 — logout telemetry approved

Logout event decision (owner approval, 2026-11-21): record an explicit user-initiated logout as a best-effort security event. Failure or unavailability of telemetry must not delay or prevent local/session credential clearing and must not change the logout outcome. This is not a mandatory transactional audit write and must not become an availability dependency. The event is not guaranteed when a user closes a browser, loses connectivity, or otherwise leaves without invoking logout. It does not imply a new session registry. This decision closes the product question; implementation details remain subject to the bounded telemetry contract.


## 2026-11-21 — approved denial-telemetry aggregation dimensions

Owner decision (2026-11-21): for selected high-risk authorization denials and threshold crossings, bounded security telemetry may aggregate repeated events using only the operation type/code and denial-reason code. The aggregation must not use or retain actor identity, attempted identity, source IP, IP digest, or other per-user/network correlation key. Operation and denial reason are controlled categories, not free-form request data. This defines the approved grouping dimensions; aggregation windows, thresholds, maximum rate/volume, retention and unavailable-store behavior remain technical implementation parameters to be specified within the existing best-effort policy.


## 2026-11-21 — denial telemetry aggregation window approved

Owner decision (2026-11-21): use fixed 15-minute aggregation windows for selected high-risk authorization-denial telemetry. Within each window, aggregate by the already approved controlled operation type/code and denial-reason code. Store an aggregate count rather than one row per repeated denial. Do not add actor, attempted-identity, source-IP, IP-digest or other per-user/network correlation dimensions. Window boundaries and implementation must avoid unbounded storage or request-path blocking. Other limits (thresholds, maximum rate/volume and retention) remain to be specified as technical parameters under the best-effort telemetry policy.


## 2026-11-21 — denial telemetry threshold approved

Owner decision (2026-11-21): the threshold for selected high-risk authorization-denial telemetry is five matching denials within one fixed 15-minute aggregation window. A matching group is defined by the approved controlled operation type/code and denial-reason code. The threshold is reached at the fifth matching denial; record one aggregate event for that group/window rather than one event per attempt. Do not add actor, attempted-identity, source-IP, IP-digest or other per-user/network correlation dimensions. Exact counter update/finalization mechanics and maximum rate/volume remain implementation parameters; telemetry remains best-effort and must not block or change the authorization result.


## 2026-11-21 — denial telemetry hourly cap approved

Owner decision (2026-11-21): cap selected high-risk authorization-denial aggregate telemetry at 100 aggregate events per application-wide hour. Once the cap is reached, additional telemetry aggregates are not persisted for the remainder of that hour. This cap applies only to best-effort denial telemetry; it must not block, delay or change business operations or authorization outcomes. The cap does not relax the approved 15-minute aggregation window, five-matching-denial threshold, operation/reason grouping dimensions, or prohibition on per-user/network correlation. Exact concurrency-safe enforcement and hour-boundary mechanics remain implementation details.


## 2026-11-21 — denial telemetry retention approved

Owner decision (2026-11-21): retain bounded aggregate authorization-denial telemetry for 30 calendar days, then automatically delete it. This retention applies only to the separate best-effort denial telemetry, not to AdminSecurityAudit, DomainEvent or authoritative business records. Cleanup must be bounded and must not block or alter request processing. The previously approved 15-minute aggregation window, threshold of five matching denials and application-wide cap of 100 aggregate events per hour remain in force.


## 2026-11-21 — denial telemetry unavailable-store behavior approved

Owner decision (2026-11-21): if the separate best-effort denial-telemetry store is unavailable or a telemetry write fails, do not retry and do not enqueue the event for later delivery; the telemetry event is dropped. The failure must not block or delay the request, change the authorization/business-operation result, or expose internal technical details to the user. This rule applies only to optional denial telemetry and does not weaken atomic fail-closed requirements for mandatory DomainEvent/AdminSecurityAudit writes.


## 2026-11-21 — telemetry-loss monitoring approved

Owner decision (2026-11-21): do not create a separate user-interface notification for dropped or unavailable optional denial telemetry. Operational monitoring must track telemetry-store availability and telemetry write failures using technical health signals only, without collecting or exposing personal data. This monitoring is separate from AdminSecurityAudit and must not become a request-path dependency or block authorization/business operations. The decision does not authorize a new user-facing alert, per-event retry, or deferred delivery queue.

## 2026-11-21 — operational telemetry monitoring access approved

Owner decision (2026-11-21): technical monitoring metrics and telemetry-store/write-failure diagnostics are available only to infrastructure administrators and personnel explicitly responsible for system operations. Ordinary MedArchive users, including application-role `admin`, must not see internal infrastructure metrics or telemetry storage diagnostics through the application. This is an infrastructure/operations access boundary, separate from application authorization and AdminSecurityAudit. Exact infrastructure identities and grants depend on deployment topology; implementation remains unauthorized.

## 2026-11-21 — technical monitoring access-view audit boundary approved

Owner decision (2026-11-21): viewing technical monitoring metrics is not recorded in MedArchive `AdminSecurityAudit`. Access to monitoring is governed by infrastructure controls. If the infrastructure requires an access log for monitoring views, it is a separate infrastructure/operations log, outside MedArchive business/security audit. This does not expand application audit events or grant application users access to monitoring. Implementation remains unauthorized.

## 2026-11-21 — no telemetry-outage notifications approved

Owner decision (2026-11-21): do not send separate notifications or alerts to infrastructure/operations personnel for denial-telemetry store unavailability or prolonged telemetry loss. Technical monitoring may retain passive health metrics/diagnostics under the approved access boundary, but it must not generate an alerting/notification workflow for this condition. This does not change drop-on-failure behavior, the absence of retries/queues, or the non-blocking nature of optional telemetry.

## 2026-11-21 — telemetry health is not exposed in the application UI

Owner decision (2026-11-21): do not expose denial-telemetry storage availability, health, write failures, or monitoring diagnostics in the MedArchive application interface, including to users with the application `admin` role. Technical health information remains outside the application UI and is available only through the separately controlled infrastructure/operations monitoring boundary. The application `admin` role does not grant access to that monitoring.

## 2026-11-21 — no separate AdminSecurityAudit integrity-verification feature

Owner decision (2026-11-21): a separate feature for cryptographic integrity verification of `AdminSecurityAudit` is not required for the target product. Do not introduce audit hash chains, MACs, signatures, checkpoints, verification jobs, integrity dashboards, or related UI/API solely for this purpose. Existing append-only/write-permission controls, mandatory atomic audit writes, retention governance, and backup/restore procedures remain separate requirements and are not waived. Key custody/rotation remains deferred and must not be treated as an approved implementation task.

## 2026-11-21 — failed login attempts are not persisted as audit or telemetry events

Owner decision (2026-11-21): do not persist individual failed-login attempts or login-throttle denials as `AdminSecurityAudit`, `DomainEvent`, or bounded security-telemetry events. The candidate telemetry event types `authn.login_failed` and `authn.login_throttled` are excluded from the target event catalog and must not be implemented. The login-throttle mechanism remains in place and may maintain only the narrowly required internal counters/state under its separate contract; those counters/bucket keys are not audit events and must not be copied into audit or telemetry. This decision does not alter successful-login auditing or the previously approved bounded telemetry for selected high-risk authorization denials. It also does not approve recording raw IPs, attempted usernames, or identity digests.

## 2026-11-21 — ordinary session expiration is not persisted as an event

Owner decision (2026-11-21): do not create `AdminSecurityAudit`, `DomainEvent`, or bounded security-telemetry records when a user session expires normally due to timeout or ordinary expiry. Do not introduce a session-expiration event or session registry solely for this purpose. Successful-login auditing and the approved best-effort event for explicit user-initiated logout remain unchanged. Rejected requests using expired/invalid sessions continue to follow the separately approved rule: no per-request session-rejection telemetry is introduced unless a new explicit product decision is made.