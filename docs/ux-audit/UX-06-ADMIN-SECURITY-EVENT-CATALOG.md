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
- The approved outage behavior remains binding: mandatory privileged mutation and successful-login audit writes fail closed; ordinary reads are not automatically blocked; every 401/403 is not synchronously required to produce a durable event.

## 2. Event classes and proposed catalog

The catalog distinguishes **mandatory transactional records** from **bounded security telemetry**. A telemetry event must never become an implicit availability dependency for an ordinary request.

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
| `authz.access_denied` | Authorization check returns denial | Durable event only for explicitly selected high-risk classes or threshold crossings; aggregate routine expected denials | Request remains denied regardless of telemetry availability |
| `authn.logout` | Explicit logout | Candidate event; can be best-effort telemetry unless a server-side revocable session model is separately adopted | Logout must clear client credential even if telemetry is unavailable |

For each telemetry family, the implementation contract must specify: reason-code enum, aggregation key, window, threshold, maximum event rate, retention, and behavior when the telemetry store is unavailable. Do not log a raw HTTP response body, SQL error, stack trace or unbounded exception string. No blanket synchronous durable write for every 401/403 is proposed.

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

Optional network context:
- Prefer a keyed digest of normalized source IP only if an approved abuse-detection use case requires it.
- Never store raw IP by default in the general audit payload.
- If identity correlation is required for unauthenticated login attempts, use a separate keyed digest of a normalized identifier; never store the attempted raw username in the event payload.
- Source and identity digest keys must be purpose-separated from application signing keys, access-controlled, versioned and rotatable. Include a non-secret key-version identifier, never key material.
- Do not claim that a digest is anonymous; it remains linkable personal data while the key/correlation capability exists.

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
- raw attempted usernames or raw source IP in the general audit payload by default;
- file contents, download URLs containing secrets, and unbounded search filters.

All payloads require maximum size/depth and schema validation. Unknown keys and unsupported schema versions must be rejected rather than silently persisted/rendered.

## 5. Read permissions and presentation

- Global audit remains admin-only under D-07/D-16; branch, archive and Central Office roles receive no global feed or audit payload endpoint.
- The event list renders event-specific labels and allowlisted fields; no generic raw-JSON viewer.
- Audit detail access should be narrower than general admin object access where payload contains security context. Admin's global object/history access does not justify exposing secrets or unnecessary PII in audit.
- Audit read/export operations themselves should be access-controlled and logged under an approved policy; avoid recursive logging loops. A separate access log or security platform may be more appropriate than recursively inserting a row into the same audit feed.
- Business history remains in the object's DomainEvent timeline with its existing object-level authorization.

## 6. Retention, integrity and operational gates — unresolved

No statutory or universal retention period is inferred. Before schema/DDL, the owner and operational/legal stakeholders must define:
1. retention by event family (mandatory admin/authentication, bounded denial telemetry, export/access events);
2. legal hold, preservation, deletion/archival and backup/restore behavior;
3. whether source/identity digests are needed, their purpose and expiry;
4. HMAC key custody, separation, rotation, version overlap and destruction;
5. audit export permission, approval, access logging and artifact expiry;
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

See `UX-06-AUDIT-RETENTION-INTEGRITY-PROPOSAL.md` for proposed event retention classes (without guessed durations), legal-hold/purge lifecycle, default exclusion of raw IP/identity digests, per-event HMAC and externally signed checkpoint design. This is a technical/operational proposal only; retention schedules, key custody, checkpoint infrastructure and audit-export access policy remain unapproved.
