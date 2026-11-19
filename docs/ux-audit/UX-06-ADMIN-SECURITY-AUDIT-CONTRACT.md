# UX-06 — Administrative and security audit: coverage and target contract

**Status:** target design proposal; event catalog and retention require owner/technical review. Not an implementation authorization.  
**Inspection baseline:** `docs/ux-audit-project`, HEAD `50b06aa358a6be49ad29fde27726fb20fcd45b63`.  
**Scope:** PostgreSQL authentication, authorization, throttling and administrative mutations.

## 1. Accepted boundaries

- D-16: global audit contains administrative and security records only. Card, box, shipment and archive-request business facts remain in DomainEvent and are read in object context.
- DB-SEC-03: retain one append-only typed/versioned DomainEvent stream for business events; do not add RequestEvent.
- Administrative/security audit is a separate authoritative record store/read path. It may use a distinct table in a dedicated schema or another explicitly isolated store; exact schema placement remains open.
- D-07: admin can globally inspect objects and object histories. This does not imply that every ordinary object read must generate a security audit event.
- DB-SEC-01: actor identity baseline remains application-authenticated actor, with DB validation of actor existence/activity/role. Audit actor attribution is not independent cryptographic proof of identity.

## 2. Current implementation evidence

### 2.1 Administrative mutation events

`PostgreSQLAdminService._audit_admin_change` appends administrative facts to DomainEvent. The 13 identified event types cover:
- branch create/update/activate/deactivate;
- courier create/activate/deactivate;
- shelf create/update/activate/deactivate;
- user create/update.

User password replacement is folded into `admin.user_updated` with `password_changed: true`; there is no separate credential event. User activation/deactivation and role changes are also represented by the generic `admin.user_updated` with before/after fields. No courier edit operation was found in the inspected PostgreSQL admin pages/service; only create and active-state change are exposed.

The current audit payload may include usernames, full names, branch IDs, courier names/phone and before/after administrative fields. The admin audit template renders the complete JSON payload. Password hashes are excluded by the current service payload, but rendering arbitrary payload keys is not a safe target contract.

### 2.2 Authentication and security paths

Static inspection of `routers/auth.py`, `dependencies.py`, `postgresql_auth_service.py`, `postgresql_login_throttle.py` and `auth_service.py` found:

| Path | Current behavior | Audit evidence |
|---|---|---|
| Successful PostgreSQL login | Validates active user/password, clears identity throttle bucket, issues signed 12-hour cookie | No success event found |
| Failed login | Increments HMAC-keyed IP and username/IP throttle buckets | No failed-login event found |
| Throttled login | Returns HTTP 429 and Retry-After | No throttle/security event found |
| Logout | Deletes session cookie in response | No logout event found |
| Missing/invalid/expired session | Authentication dependency returns HTTP 401 | No security event found |
| Inactive user / credential-version mismatch | Authentication dependency returns HTTP 401 | No security event found |
| Role denial | `require_pg_role` returns HTTP 403 | No authorization-denial event found |
| Password reset by admin | Included in `admin.user_updated` payload as boolean | No distinct credential lifecycle event |
| Session lifecycle | Signed stateless cookie, 12-hour max age; password-hash fingerprint can invalidate older cookies after password change | No session registry or individual session-revocation event found |

The throttle stores keyed digests rather than raw usernames/IP addresses and prunes stale unblocked buckets after one day. This is rate-limit state, not a durable security audit trail. The current logout removes the browser cookie but there is no server-side session record to revoke.

These are findings from inspected source paths, not proof about reverse proxies, infrastructure logs, deployed versions or external identity systems.

## 3. Proposed event catalog

The following is a candidate minimum catalog. Exact names/versioning and which denial classes merit durable events require approval.

| Family | Candidate event | Trigger / purpose |
|---|---|---|
| Authentication | `auth.login_succeeded` | Successful interactive authentication |
| Authentication | `auth.login_failed` | Invalid credentials or inactive/missing account; use a generic outcome to avoid account enumeration |
| Authentication | `auth.login_throttled` | Login attempt blocked by rate limit |
| Authentication | `auth.logout` | Explicit logout request |
| Authentication | `auth.session_rejected` | Invalid, expired, revoked or inactive-principal session presented |
| Authorization | `authz.access_denied` | Security-relevant 403; consider sampling/aggregation for noisy routine denials |
| Credential lifecycle | `admin.credential_reset` | Admin creates/resets a user's credential; never store credential material |
| Account lifecycle | `admin.user_role_changed` | Privilege/role transition, distinct from generic profile edit |
| Account lifecycle | `admin.user_deactivated` / `admin.user_activated` | Account enabled/disabled |
| Administration | Existing `admin.*` events | Branch, courier, shelf and user administration; migrate to the new store without rewriting old DomainEvent rows |

Do not create events for raw password values, password hashes, cookie/session tokens, signing keys, full request bodies, or unrestricted exception text. Do not record a successful password value or a credential fingerprint as an audit payload field.

### 3.1 Denial event policy

Not every 401/403 is necessarily a security incident. The policy should distinguish:
- failed authentication and invalid/expired sessions, which are useful security signals;
- repeated or anomalous authorization denials, which may warrant durable records;
- routine expected role restrictions, which may be counted/aggregated to avoid flooding the audit store.

The event must carry a stable reason code, not SQL text, stack trace or user-facing error body. Whether every denial is recorded or only selected/high-risk classes remains open.

## 4. Proposed record envelope and payload policy

Minimum common envelope proposal:

| Field | Purpose |
|---|---|
| `id` | Immutable audit record ID |
| `event_type`, `event_version` | Typed, versioned event contract |
| `occurred_at` | Server/database timestamp |
| `actor_user_id` | Authenticated actor; nullable for failed/unknown identity |
| `target_type`, `target_id` | Affected principal/object where known |
| `outcome`, `reason_code` | Stable success/failure classification |
| `request_id`, `correlation_id` | Transport and cross-operation correlation |
| `source_ip_digest` | Optional keyed digest for abuse correlation, not raw IP by default |
| `identity_digest` | Optional keyed digest of normalized login identifier; do not store raw attempted username |
| `payload` | Strict per-event allowlisted JSON, never arbitrary request/model dump |

Use database-enforced append-only protections and least-privilege INSERT/SELECT grants. Separate audit readers from ordinary operational roles. Admin is the target global reader, with any break-glass/support role requiring a separately approved policy.

For login events, do not store the attempted password, raw username, raw IP, or distinguish unknown username from wrong password in the user-facing outcome. If keyed digests are retained, define key rotation and retention so that digest correlation remains possible only for the approved period. The HMAC key must not be the general application signing key by default; key separation is a technical decision to close.

## 5. Atomicity and ownership

**Authoritative-source rule (aligned with DB-SEC-03 and P-07):**

| Record class | Authoritative record | Must not be duplicated as |
|---|---|---|
| Card/box/custody/placement/shipment/request business fact, including an admin correction to that fact | Typed/versioned append-only `DomainEvent` for the aggregate | A second copy of the business payload in the global admin/security feed |
| Privileged admin correction action (actor, reason, authorization outcome, target, command/correlation references) | Separate append-only `AdminSecurityAudit` record | A generic `admin.*` business DomainEvent |
| Pure admin CRUD (branch/user/shelf/courier) | `AdminSecurityAudit` | DomainEvent, unless a separate domain aggregate event is explicitly required by a future approved design |
| Authentication/authorization/security event without a business aggregate | `AdminSecurityAudit` | DomainEvent |

For an admin correction that changes a business aggregate, both authoritative records are committed atomically: DomainEvent records what business fact changed; AdminSecurityAudit records that a privileged correction was invoked, by whom, why, with what outcome and references to the business event. The audit entry should not copy the full before/after business payload; it may carry allowlisted summary fields and the `domain_event_id`/aggregate reference. The two records have distinct semantics and are not competing sources of truth.

- A single admin command that mutates a business aggregate and requires a security/admin audit record must commit both authoritative records atomically, with shared command/correlation references. Do not duplicate the business fact into the global audit feed.
- Pure authentication failures have no business aggregate; their security record and throttle mutation should have a clearly defined transaction outcome. A failed login should not be silently lost because the HTTP response is 200/429.
- Successful login audit should be committed with the authentication decision before the session cookie is issued. Avoid issuing a cookie if the required audit write fails, unless an explicitly approved availability policy says otherwise.
- Logout and invalid-session events need careful treatment because the current session is stateless and may not resolve to an active actor. Store nullable actor plus safe principal reference/digest as appropriate; do not invent a server-side session registry without a separate decision.
- Admin CRUD audit migration must preserve old `admin.*` evidence. Copy, verify counts/checksums/actor/timestamps/aggregate references, switch reads, and retain the old append-only rows as legacy evidence. No destructive rewrite/delete.

## 6. Read permissions and UI

- `/admin/audit` must read only the new admin/security audit store after migration.
- Global audit must not include `card.*`, `box.*`, `shipment.*`, `archive_request.*` operational facts.
- Replace raw JSON rendering with event-specific labels and allowlisted fields.
- Sensitive payload detail should be minimized even for admin; admin's D-07 object access is handled by object detail pages, not by exposing unrestricted event payloads.
- No global audit page or endpoint should be available to branch, archive or Central Office roles.
- Ordinary object timeline pages continue to read business DomainEvents under object-specific authorization; they must not be backed by the global security audit.

## 7. Retention, privacy and operational controls — open

Before DDL, decide:
1. retention periods by family (authentication, authorization, admin changes) and any legal/operational hold;
2. whether source IP is never stored, stored only as keyed digest, or retained in a restricted separate source;
3. HMAC key management/rotation for source and identity digests;
4. whether selected privileged reads (bulk export, sensitive-data export, mass lookup) must be audited, while avoiding noisy logging of every normal object detail view;
5. audit export/download permissions, access logging and tamper-evidence/backup requirements;
6. **Approved outage behavior (2026-11-18): fail closed** for privileged mutations and successful login when the mandatory audit record cannot be committed atomically. Ordinary reads are not automatically blocked. A blanket synchronous audit requirement for every 401/403 is not approved; denial-event policy remains separately bounded and open.

Do not infer statutory retention from the application code. This project audit did not review jurisdiction-specific legal requirements or production policy.

## 8. Required tests

- Every admin mutation writes exactly one expected audit record with actor and allowlisted payload.
- Credential reset event records target and actor but no password/hash/token.
- Successful, failed and throttled login events are distinguished; failed attempts do not disclose whether the account exists.
- Invalid/expired/revoked sessions and selected authorization denials follow the approved event policy.
- No raw password, password hash, cookie, session token, signing key, raw request body or unbounded exception text appears in audit payloads.
- Global audit contains only admin/security events; business DomainEvents are absent.
- Operational roles cannot read global audit.
- Append-only/privilege tests prove runtime cannot update/delete audit rows or forge actor attribution beyond the agreed DB-SEC-01 trust model.
- Admin mutation plus required business DomainEvent plus security audit is atomic under rollback and concurrent retry.
- Migration preserves all old `admin.*` evidence and read cutover is verified before legacy retention policy is applied.

Tests are proposed only; none were executed.

## 9. Non-goals

- No new incident entity.
- No second business event stream or RequestEvent.
- No unrestricted audit payload viewer.
- No assumption that ordinary admin object reads all require durable audit events.
- No changes to authentication/session behavior, schema, code, tests, workflows or deployment in this documentation stage.


## 10. P-07 audit ownership clarification (2026-11-18)

For privileged business corrections, `DomainEvent` is authoritative for the corrected business fact; `AdminSecurityAudit` is authoritative for the fact that an administrator invoked the privileged correction, with actor, mandatory reason, outcome, target and references. Both are committed atomically under the same command/correlation context, but the audit record does not duplicate the full business payload. Pure admin CRUD and auth/security events use AdminSecurityAudit only. This is aligned with DB-SEC-03: one business DomainEvent stream, no RequestEvent and no global operational feed.


## 11. Approved audit-store outage policy (2026-11-18)

The product owner approved the following baseline:

1. **Privileged mutations — fail closed.** A privileged/admin mutation must not take effect unless its mandatory `AdminSecurityAudit` record can be committed in the same database transaction. If the audit insert or transaction commit fails, the entire mutation rolls back. For a privileged business correction, the corresponding authoritative `DomainEvent` and the `AdminSecurityAudit` record are committed atomically; neither may survive alone.
2. **Successful login — fail closed.** After credentials and account state are successfully validated, the application must not establish/issue a new authenticated session unless the required successful-authentication audit event is durably committed. Session issuance must occur only after the audit transaction commits. If audit persistence fails, no authenticated session is issued.
3. **Ordinary reads — not automatically blocked.** Audit-store unavailability alone does not deny normal authorized reads. This does not bypass ordinary authentication, authorization, or database availability requirements.
4. **401/403 and other denials — separate bounded policy.** This approval does not require a synchronous durable audit write for every failed authentication, invalid session, throttled attempt, or authorization denial. Event selection, aggregation/rate limiting, and failure behavior for denial telemetry remain a separate technical policy.
5. **No silent degraded success.** The application must not report a privileged mutation or successful login as completed when its required audit record was not committed. Operational alerts/diagnostics must avoid credentials, tokens and unnecessary patient data.

This is an approved target behavior, not an implementation claim. The current application behavior remains as described in the static findings until separately implemented and verified. Tests, SQL/DDL, migrations and runtime checks have not been performed as part of this decision.


## 12. Event catalog proposal (2026-11-19)

A detailed candidate catalog, envelope, payload allowlist, bounded denial telemetry policy and privileged-read boundary is maintained in `UX-06-ADMIN-SECURITY-EVENT-CATALOG.md`. It is a proposal, not an approved final event schema or implementation authorization.

Key distinctions:
- mandatory transactional records: successful login, privileged admin/reference-data mutations, credential resets, and approved P-07 corrections;
- bounded telemetry: failed/throttled login, rejected sessions, selected authorization denials and logout; no mandatory durable write for every 401/403;
- privileged reads: audit of successful exports/downloads and report/manifest operations that export patient-identifying data; mass lookup/search is explicitly excluded, as are ordinary object views, routine search, dashboards and object timelines.

The envelope proposal includes immutable event ID, schema version, closed event type, DB timestamp, actor/role/scope snapshot, outcome/reason, target, command/correlation IDs, source channel and strict event-specific payload. Raw credentials, tokens, hashes, patient identifiers and unrestricted request/error data are prohibited by default.

PR-READ-01 is resolved: mass card lookup/search is excluded from AdminSecurityAudit, including large-result and cross-scope searches. Successful exports/downloads and report/manifest operations that export patient-identifying data remain in the proposed audit scope. Still unresolved: retention/legal hold; digest/IP necessity and HMAC key lifecycle; export/access logging; tamper evidence and operational controls.


## 13. Retention and integrity design gate (2026-11-19)

`UX-06-AUDIT-RETENTION-INTEGRITY-PROPOSAL.md` proposes separate retention classes for privileged action evidence, sensitive export evidence, bounded security telemetry and integrity checkpoints, without assigning statutory durations. It recommends no raw IP/identity digest by default, per-event HMAC with external key custody, and independently stored signed checkpoint manifests rather than a contended global hash chain. Legal hold, purge/backup reconciliation, privileged DBA threat model, audit export/access logging and deployment-specific key/checkpoint controls remain open. No implementation is claimed.
