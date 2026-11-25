# UX-09.4H — administration and security mapping
2026-11-25. Design only. Sources WF-05 D01–D06, UX-06-ADMIN-SECURITY-AUDIT-CONTRACT, EVENT-CATALOG, AUDIT-ACCESS-AND-EXPORT-POLICY-PROPOSAL, AUDIT-RETENTION-INTEGRITY-PROPOSAL, UX-08-APPROVED-BASELINE, actual postgresql_admin_pages.py.
| Screen/action | Proposed protected use case | Existing route / target |
|---|---|---|
| D01 admin home | AdminHome read | GET /admin; management, no operational queue |
| D02 users | UserRegistry read | GET /admin/users; FIO/login, role/location/activity filters |
| D03 create | user_create | POST /admin/users |
| D03 profile | user_update_profile | POST /admin/users/{id}/edit split into typed commands |
| D03 role/scope | user_change_role_scope | active compatible location; admin assignment requires own password reauth |
| D03 activate/deactivate | user_set_active | preserve records and unfinished operations |
| D03 grants | permission_grant/revoke | other active user, approved role eligibility, audit |
| D03 reset password | user_reset_password | separate command; own password reauth; invalidate all target sessions atomically |
| D04 organization | location_create/rename/set_active | existing /admin/branches and edit/status; preserve ID/history |
| D05 couriers | courier_create/rename/set_active | existing /admin/couriers and status; rename target contract |
| D06 storage structure | section/shelf_create/rename/set_active | existing /admin/sections and shelf edit/status; typed aggregate distinction |
| Object search | AdminObjectSearch read | global authorized read, no routine mutation rights |
| Audit | AdminAuditRegistry/Detail read | GET /admin/audit; AdminSecurityAudit only, typed allowlist, no export |
| P07 correction | typed correction commands | separate domain and security records, see UX-09.4G |
| Draft reassignment | shipment_reassign_draft_author | deactivated author, active compatible replacement, no handover by admin |

Four roles only; no viewer/courier accounts. User fields FIO, immutable login, role, organization, activity; no contact extras. No self profile edit/role change/deactivation/admin reset. Last active admin guard must serialize concurrent mutations. Reset notification once at next login, no resetter name. Never expose passwords/hash/tokens. Session invalidation fail closed; verify actual session mechanism before migration. Deactivation preserves unfinished operations. Location deactivation blocked by active staff or unfinished operations. Courier FIO globally unique including inactive, normalized case/repeated whitespace, no deletion/contact fields; prior assignments/snapshots preserved. Shelf deactivation blocked by active placement; section constraints verify separately.
## Permission reconciliation
card_correct explicitly granted/revoked by another active admin, no self grant; scoped. WF-D03 explicitly states card_number_correct inherent to every active admin, not assignable checkbox: resolves earlier UX-09.4G open role question; verify against latest product decision before implementation and sync G. dashboard_view inherent admin, assignable active CO/archive only, branch excluded. Writeoff execution separate archive grant, placement correction grant and their management contract remain to verify; no invented rights. 
## Audit and authentication
AdminSecurityAudit authoritative admin/auth/security, DomainEvent business facts, command ledger replay only. Pure admin CRUD no duplicate business event. P07 correction writes both linked records atomically, no duplicate full business payload. Required events/ledger/mutation fail closed; successful login audit commits before cookie. Ordinary reads not blocked; logout best effort; routine denials not mandatory individual rows; bounded selected security telemetry. No raw IP, IP digest, attempted username/identity digest, credentials, patient identifiers, arbitrary JSON, raw SQL or tokens. No audit-feed export or event detail read logging; successful business-data exports remain audited. Retention durations not approved: preproduction governance gate, no invented durations/automatic purge/ad hoc deletion. Integrity checkpoints/deployment details open. Legacy admin.* DomainEvents preserved, verified migration and switch reads; no destructive rewrite. Existing /admin/incidents legacy retirement requires migration plan, not new admin workflow.
## Tests and gates
Concurrent last-admin deactivation/demotion; self mutations; reauth failure; role/scope change vs operational command; grant/revoke race; inactive target; reset invalidates all sessions and one notification; audit failure rollback/no cookie; courier uniqueness inactive/rename snapshot; location/shelf deactivation guards; audit direct URL role denial; no raw payload/PII/export; legacy migration reconciliation. Exact command/event IDs, role transition/session policy, section constraints and grants must be verified before implementation. Names proposed, not existing SQL. Code/migrations/HTML unchanged; tests not run.
