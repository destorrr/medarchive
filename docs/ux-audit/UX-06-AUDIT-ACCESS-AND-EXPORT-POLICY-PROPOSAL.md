# UX-06 — AdminSecurityAudit access and export policy proposal

**Status:** product decision recorded: exporting the AdminSecurityAudit feed is not supported or planned. Remaining access-logging details require owner approval.  
**Prepared:** 2026-11-19  
**Baseline:** `docs/ux-audit-project`, HEAD `e8d5e26b02b61e560e0a3a7685c212e26642932f`.

## 1. Existing approved boundary

- The global administrative/security audit feed is admin-only.
- Branch, archive and central-office roles do not receive global audit access.
- This does not remove the existing admin global access to cards and object history.
- The global feed contains administrative/security audit facts, not operational DomainEvent history.
- Business object timelines remain in their object-specific views and scope rules.
- Audit access must not expose passwords, tokens, keys, patient payloads, raw request/response bodies or unrestricted JSON.
- Sensitive data export events remain in scope; mass card lookup/search remains excluded from audit.

## 2. Approved permission model

### 2.1 Read

Retain the existing `admin` role as the only role allowed to open the global AdminSecurityAudit feed and inspect event details. Do not create a new general-purpose viewer role or grant access to other operational roles.

Read access should be implemented through curated, event-specific views:
- feed list: timestamp, event type, actor, outcome, target reference and concise safe summary;
- event detail: only fields allowed by that event type's schema;
- no generic raw JSON viewer;
- server-side authorization on every list, filter, detail and pagination request;
- bounded date ranges/page sizes and safe filter allowlists;
- no direct runtime table SELECT if the protected read boundary can provide the required scope.

Ordinary feed viewing does not require a free-text reason. The system already attributes the authenticated admin actor and time.

### 2.2 Audit-feed export — not supported

**Product decision (2026-11-19): exporting the AdminSecurityAudit feed is not предусмотрено.** There is no audit-feed export capability, no audit-export permission, no export artifact, no download flow and no second-person approval workflow to design. Do not add CSV/PDF/JSON export, bulk download or equivalent extraction endpoint for this feed.

This decision concerns export of the audit feed itself. It does **not** remove the separately approved audit events for successful exports/downloads of patient-identifying medical-card data and reports/manifests that export such data. Those are business-data export actions and remain in the AdminSecurityAudit event catalog.


## 3. Access logging without recursive audit

Do not append a row to the same AdminSecurityAudit feed for every read of that feed. It creates a self-referential stream, complicates verification and can amplify ordinary pagination into high-volume audit writes.

Use a logically separate, append-only `AuditAccessLog` (name provisional) or equivalent security access channel, with its own retention and integrity policy. It is not a second business event stream and must not contain copies of AdminSecurityAudit payloads.

Recommended events:
- `audit.feed_opened`: once when the admin opens the global feed, not on every page render;
- `audit.event_detail_viewed`: only if the owner decides individual detail access is necessary; otherwise omit to avoid excessive tracking;
- `audit.export_requested`;
- `audit.export_completed` / `audit.export_failed`;
- `audit.export_downloaded`;
- `audit.access_denied`: bounded telemetry, not necessarily a durable row for every denial.

Minimum access-log envelope:
- immutable event ID and schema version;
- DB timestamp;
- authenticated actor and role snapshot;
- action/outcome;
- bounded query scope metadata (date range, event-type filter, page count) without copying arbitrary search text;
- target event ID for detail view, if enabled;
- export reason, safe filter scope, row count and opaque artifact reference for export;
- command/correlation/request IDs.

Do not log each page navigation, every row rendered, polling, client-side re-render or each event in a displayed list. A single feed-open event may include the initial bounded scope; export actions always receive their own explicit records.

Access-log failure policy:
- export generation/download must fail closed if its mandatory access/export record cannot be committed;
- opening or reading the audit feed should not be blocked solely because optional feed-open telemetry is unavailable, unless the owner explicitly elevates it to mandatory;
- no silent export success without a durable export record.

The access log must be independently protected and monitored. If stored in the same PostgreSQL database, use a distinct table/schema and grants, while recognizing that this is not independent protection from a database superuser. External checkpointing/immutable anchoring should cover both logs or explicitly document the difference.

## 4. Artifact lifecycle

No audit-feed export artifact exists in the target model. The artifact lifecycle requirements in this section do not apply to AdminSecurityAudit. Business-data exports (medical-card data, reports and manifests) follow their own approved workflow and remain auditable as specified in the event catalog.

## 5. Acceptance criteria (not executed)

- Non-admin roles cannot access feed, detail, filters, export endpoints or artifact downloads.
- Admin read access returns only event-specific allowlisted fields.
- No audit-feed export, export endpoint, export artifact or download flow is available.
- Feed pagination and rendering do not create recursive event rows.
- Feed-open telemetry is emitted at most once per logical feed-open action, not per page/render.
- Access-log payload does not copy audit event payloads or patient data.
- Access-log retention and integrity are explicitly defined.
- Audit read/export access remains unavailable to branch/archive/CO roles.

## 6. Decisions still required

1. Decide whether to log only feed-open actions or also individual event-detail views.
2. Decide whether feed-open telemetry is mandatory or best-effort.
3. Approve retention/integrity treatment for the separate access log together with R1–R4.

## 7. Non-goals

- No new viewer role.
- No broad access for branch/archive/CO.
- No automatic audit record for each 401/403.
- No audit-feed export or download capability.
- No recursive write into AdminSecurityAudit on every feed read.
- No assumption that MFA, step-up authentication, KMS, artifact storage or a separate permission framework already exists.
- No implementation, DDL, migration, API/UI or infrastructure change is authorized by this proposal.
