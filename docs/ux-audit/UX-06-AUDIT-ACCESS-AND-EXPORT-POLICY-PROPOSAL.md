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

### 2.2 Export — approved for all admins

**Product decision (2026-11-19):** any user with the existing `admin` role may export the AdminSecurityAudit feed, provided a mandatory non-empty reason is supplied. A separate `audit_export` permission and second-admin approval are not required.

Export remains a distinct privileged action, not an automatic consequence of reading the feed. Apply:
- explicit export action and mandatory non-empty reason;
- bounded date range, event-type filters and row/size limits;
- fixed allowlisted export format and columns;
- server-side authorization and scope enforcement;
- export event recorded with actor, reason, filters/scope, outcome, row count, opaque artifact reference, creation/expiry timestamps and correlation ID;
- no event payload duplication, file contents, tokens or public download URL in audit payload.

This permission does not extend to branch, archive or central-office roles. It concerns exporting the audit feed itself and does not alter the separate catalog of successful business-data exports/downloads of patient-identifying card data and reports/manifests.

## 3. Access logging without recursive audit

Do not append a row to the same AdminSecurityAudit feed for every read of that feed. It creates a self-referential stream, complicates verification and can amplify ordinary pagination into high-volume audit writes.

Use a logically separate, append-only `AuditAccessLog` (name provisional) or equivalent security access channel for export request/completion/download facts only, with its own retention and integrity policy. It is not a second business event stream and must not contain copies of AdminSecurityAudit payloads.

Recommended events:
- Opening the global audit feed is not logged (product decision, 2026-11-19).
- Individual event-detail views are not logged (product decision, 2026-11-19).
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

Do not log feed opening, individual event-detail views, page navigation, each row rendered, polling, client-side re-render or each event in a displayed list. Export actions always receive their own explicit records.

Access-log failure policy:
- export generation/download must fail closed if its mandatory access/export record cannot be committed;
- opening or reading the audit feed has no feed-open telemetry dependency because the action is not logged;
- no silent export success without a durable export record.

The access log must be independently protected and monitored. If stored in the same PostgreSQL database, use a distinct table/schema and grants, while recognizing that this is not independent protection from a database superuser. External checkpointing/immutable anchoring should cover both logs or explicitly document the difference.

## 4. Artifact lifecycle

No audit-feed export artifact exists in the target model. The artifact lifecycle requirements in this section do not apply to AdminSecurityAudit. Business-data exports (medical-card data, reports and manifests) follow their own approved workflow and remain auditable as specified in the event catalog.

## 5. Acceptance criteria (not executed)

- Non-admin roles cannot access feed, detail, filters, export endpoints or artifact downloads.
- Admin read access returns only event-specific allowlisted fields.
- No audit-feed export, export endpoint, export artifact or download flow is available.
- Feed pagination and rendering do not create recursive event rows.
- Opening the global audit feed and viewing individual event details do not generate access-log records.
- Access-log payload does not copy audit event payloads or patient data.
- Access-log retention and integrity are explicitly defined.
- Audit read/export access remains unavailable to branch/archive/CO roles.

## 6. Decisions still required

1. **Resolved:** any admin may export the audit feed with a mandatory non-empty reason; no separate export permission or second-admin approval.
2. **Resolved:** individual event-detail views are not logged.
3. Approve retention/integrity treatment for export/access evidence together with R1–R4.

## 7. Non-goals

- No new viewer role.
- No broad access for branch/archive/CO.
- No automatic audit record for each 401/403.
- No audit-feed export by non-admin roles; any admin export requires a mandatory non-empty reason and bounded, protected handling.
- No access-log record for opening the feed or viewing individual event details; no recursive writes for ordinary audit reads.
- No assumption that MFA, step-up authentication, KMS, artifact storage or a separate permission framework already exists.
- No implementation, DDL, migration, API/UI or infrastructure change is authorized by this proposal.


## 8. Owner decision — patient-identifying data in audit-feed exports (2026-11-19)

**Approved:** an export of the AdminSecurityAudit feed may include patient-identifying data, including patient full names and medical-card numbers. The earlier blanket prohibition on patient identifiers in the audit-feed export is superseded by this explicit owner decision. This permission applies only to the exported audit artifact; it does not authorize adding patient identifiers to ordinary audit-event payloads or broadening feed access beyond the admin role.

The export contract must still enforce the mandatory non-empty reason, admin authorization, bounded date range and volume, fixed allowlisted format/columns, protected actor-bound artifact and download-time authorization, and mandatory durable records for export request/result/download. The exact patient-identifying columns must be explicitly allowlisted in the export schema; arbitrary payload dumps remain prohibited. Keep the export purpose-limited and minimize fields to those approved for the selected export. Retention/legal-hold and artifact-expiry policy remain unresolved governance gates.

This decision does not authorize implementation, DDL, migrations, API/UI or infrastructure changes.
