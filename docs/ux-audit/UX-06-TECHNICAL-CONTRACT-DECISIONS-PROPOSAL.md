# UX-06 — Technical decisions proposal: DB result, schemas, actor scope and supporting principals

**Status:** DB result envelope and PostgreSQL application-schema architecture approved by product owner on 2026-11-20; remaining sections are technical recommendations for review, not an implementation authorization.  
**Inspection baseline:** `docs/ux-audit-project`, HEAD `80f9b87bf7d97eddbc2c304c4bbad3694c0aae65`.

## 1. DB command result contract

### Approved target: versioned JSONB envelope, validated by each command

**Owner decision (2026-11-20): approved as the mandatory public result contract for all DB commands.**

Prefer a stable envelope over one PostgreSQL composite type per command:

```json
{
  "result_code": "success",
  "result_version": 1,
  "result_payload": {},
  "command_id": "uuid",
  "correlation_id": "uuid",
  "completed_at": "database timestamp"
}
```

Rules:
- `result_code` comes from a closed allowlist for the command family.
- `result_version` versions the payload contract, not the command input.
- Each command has a documented JSON schema/validator and a minimal payload; do not return arbitrary ORM objects or SQL diagnostics.
- Stable IDs and state values are returned; PII is included only where the API use case requires it and current read authorization permits it.
- HTTP status/body mapping remains in the API adapter. Database errors, SQLSTATE, constraint names and exception text are not public result fields.
- Ledger stores the exact committed envelope. Replay returns the original semantic result; it does not recalculate current state.
- For high-frequency internal functions, a fixed SQL signature and small typed scalar return may be used internally, but public command result contract remains the versioned envelope.

Reason: PostgreSQL composite types create DDL coupling when result shapes evolve. JSONB is appropriate only with strict per-command schema validation and explicit versioning; unvalidated free-form JSON is not acceptable.

## 2. Application schemas

### Approved target: private `medarchive` schema, with no application objects in `public`

**Owner decision (2026-11-20): approved as the target PostgreSQL schema architecture.**

Target separation:
- `medarchive`: owner-controlled tables, internal functions and private implementation objects.
- Optional `medarchive_api`: only if a stable set of read views/functions needs a separately grantable API boundary. Do not create it merely for naming symmetry.
- `public`: no MedArchive application objects; revoke unnecessary CREATE/USAGE from PUBLIC and runtime.
- Migration metadata may remain in a dedicated migration schema or the explicitly chosen Alembic schema; ownership and access must be documented.

Every SECURITY DEFINER function must:
- be owned by `medarchive_owner` (NOLOGIN);
- use a fixed safe `search_path` (prefer `pg_catalog` plus explicitly schema-qualified application objects);
- have EXECUTE revoked from PUBLIC and granted only to intended principals;
- validate actor, role, scope, arguments and state internally;
- avoid dynamic SQL unless identifiers are fixed/allowlisted and safely quoted.

The target architecture is approved, but its implementation remains gated: inspect all SQL, ORM metadata, Alembic, extensions, backup/restore and operational scripts before moving or creating objects. This approval does not imply that current code already supports the target schemas.

## 3. RLS and actor/scope context

### Approved target: RLS is not a required first-cut security boundary

**Owner decision (2026-11-20): approved.**

DB-SEC-01 explicitly accepts application-authenticated actor as the baseline. Runtime can set a GUC and, under the same database role, can generally change it; therefore a GUC carrying actor UUID or location is not independent proof of identity.

For initial protected-command cutover:
- each SECURITY DEFINER command receives the authenticated actor UUID as an explicit parameter;
- the command checks that the user exists, is active, has the required role and is authorized for the target location/object using authoritative rows and locked state;
- caller-supplied location/role claims are not trusted; target scope is derived from DB records;
- no direct runtime DML is granted to bypass command checks;
- read endpoints continue enforcing role/object scope in the application until a separately designed read API or stronger DB identity boundary exists.

RLS may be added later as defense in depth only after a threat model proves the policy cannot be bypassed by runtime and the session context is bound to a trustworthy identity mechanism. Do not turn on RLS with permissive fallback policies or trust a mutable GUC as an anti-forgery control. The approval closes the first-cut architecture choice; it does not authorize implementation and does not remove the need for read-side authorization tests.

## 4. Login throttle

### Approved target: narrow SECURITY DEFINER command surface

**Owner decision (2026-11-20): approved.**

Use a small set of fixed functions for throttle operations, for example:
- evaluate/increment the relevant keyed buckets atomically;
- clear the successful-login identity bucket;
- prune only expired, unblocked rows under a bounded policy.

Runtime receives EXECUTE only on those functions, not table-level SELECT/INSERT/UPDATE/DELETE. Functions use fixed search_path, bounded input sizes, safe keyed digests and concurrency-safe updates. Do not let the caller choose arbitrary bucket keys, timestamps or retention windows.

Before implementation, verify current HMAC key source, rotation plan, lock contention, cleanup semantics and whether IP/identity bucket writes can be combined in one transaction. Throttle state remains separate from durable security audit. The architecture choice is closed; this approval does not authorize implementation.

## 5. First-admin and system-location bootstrap

### Approved target: one-shot operator/infra runner, outside web startup

**Owner decision (2026-11-20): approved.**

- Remove first-admin creation and system-location provisioning from web-worker startup/runtime path.
- Run a documented operator command/job using a short-lived, explicitly provisioned bootstrap credential; it is not the runtime URL and not the migrator credential.
- Bootstrap operation checks the database is in the expected uninitialized state, takes an advisory/row lock, creates required system locations and the first admin atomically, and writes a bootstrap audit record without secrets.
- Disable or revoke the bootstrap credential/path immediately after success; subsequent admin lifecycle uses normal protected admin commands.
- No permanent generic bootstrap password or HTTP endpoint.
- Recovery of a lost first admin must be a separate break-glass operator runbook, not a hidden reusable bootstrap endpoint.

The exact principal may be a temporary operator role or controlled owner-context runner, selected with deployment design. Do not give the web process owner or migrator credentials. The architecture choice is closed; implementation details, deployment identity and recovery runbook remain pre-implementation work. This approval does not authorize implementation.

## 6. Infrastructure principals

### Approved target: separate logical identities and least-privilege access

**Owner decision (2026-11-20): approved.**

The repo audit confirms the current deployment reuses one database URL for app, Alembic, bootstrap and backup. The target split is not implemented. Exact infra grants cannot be finalized from repository source alone because the actual PostgreSQL hosting, backup mode, monitoring stack and restore topology are not established here.

Required logical separation:
- `medarchive_owner`: NOLOGIN object owner.
- `medarchive_migrator`: LOGIN used only by a dedicated migration runner; no web-worker environment exposure.
- `medarchive_runtime`: LOGIN used by web/API runtime; no DDL, owner membership, superuser, BYPASSRLS or unrestricted workflow DML.
- backup identity: separate from runtime/migrator; rights derived from pg_dump/physical backup strategy and tested restore procedure.
- monitoring identity: health/statistics only, no patient PII or workflow mutation.
- maintenance identity: only if an actual maintenance job requires it; command-scoped and separately credentialed.
- bootstrap identity: short-lived operator path, not a permanent runtime principal.

Do not create backup/monitoring/maintenance roles speculatively before selecting deployment topology and commands they must execute. The logical separation decision is closed; exact grants, credentials, and operational runbooks remain pre-implementation work. This approval does not authorize implementation.

## 7. DomainEvent consumer inventory and compatibility

Current static inventory identifies in-repository readers:
- dashboard recent-movement summary;
- global operational `/history` route (target retirement as a global feed);
- shipment aggregate detail timeline;
- legacy `/admin/audit` reader of `admin.%` (target cutover to separate admin/security audit);
- shipment-specific resolution/detail logic that reads shipment events.

Writers include card intake/custody, archive workflow, shipment workflow and admin service. The admin writer events must migrate to the separate admin/security audit while preserving historical evidence; business events remain in DomainEvent.

Owner clarification (2026-11-20): external DomainEvent consumers are not planned at the current stage. Do not design external delivery, subscriptions, outbox/consumer offsets, webhook contracts or downstream compatibility now. This product-scope decision does not prove that no deployment-level reader/job already exists. Before changing event schema, payload semantics or retention, complete the internal reader inventory and verify actual deployment jobs/readers and effective DB grants. No outbox/consumer-offset deployment contract has been found in the inspected source slice.

Compatibility requirements:
- do not rename/reinterpret existing event_type payloads in place;
- introduce event_version for new/changed payload contracts;
- retain old readers or add an adapter during a bounded dual-read period;
- migrate admin.* records non-destructively, reconcile counts and references, then switch admin audit reads;
- preserve old DomainEvent rows as append-only historical evidence; no destructive rewrite;
- do not introduce a second business event stream or RequestEvent.

## 8. Readiness classification

| Topic | Recommendation | Remaining gate |
|---|---|---|
| DB result | Approved: versioned JSONB envelope with strict command schemas | Define and verify API error mapping before implementation |
| Schema | Approved: private `medarchive`; optional `medarchive_api` only if needed; no application objects in `public` | Full SQL/ORM/deployment compatibility review before implementation |
| RLS | Approved: not required for first cut; explicit actor parameter + DB-side role/scope checks for protected commands; application read-side scope; no direct runtime DML bypass | Threat model and trustworthy identity binding before any later RLS adoption |
| Login throttle | Approved: narrow SECURITY DEFINER functions; runtime EXECUTE only, no direct table SELECT/INSERT/UPDATE/DELETE | HMAC key/rotation, lock contention, atomicity and bounded cleanup design before implementation |
| Bootstrap | Approved: one-shot operator runner outside web startup; atomic initialization; short-lived credential revoked after success | Deployment principal, exact system-location inventory and break-glass recovery runbook before implementation |
| Infra principals | Approved: separate owner, migrator, runtime, backup, monitoring and short-lived bootstrap identities; maintenance identity only when required | Hosting/backup/monitoring topology, exact grants and operational runbooks before implementation |
| Event consumers | Approved current scope: internal MedArchive readers only; no external consumers planned | Complete internal reader inventory and verify no existing deployment-level readers/jobs before event-contract changes |

No source, SQL, DDL, ORM, migration, tests, workflow or deployment files were changed. PostgreSQL, Alembic, pytest and CI were not run.
