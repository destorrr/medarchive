# UX-09.7B — cutover and rollback plan
2026-11-26. Reviewed 5E, 5F, 5H, 7A. Owner confirmed no real SQLite data and no production use; existing PostgreSQL contents remain unverified. No converter, dual writes, destructive reset or production authorization.
## Release gates
G0 I00: pin final design SHA, identify isolated environment, read-only PG16 revision/catalog/ACL/counts/conflicts and bootstrap SQL parity. Unknown target/data stops deployment, not isolated development.
G1 I01–I09: E1–E5 in isolated integration environment; resolve DB2–DB6 before affected DDL/commands, preserve 0001–0011, no old writer on incompatible schema. Reconcile retained PG rows deterministically, no invented dates or links.
G2 I10: E6/E7 after all writers converted including auth/bootstrap/jobs; validate constraints, revoke runtime DML, retire SQLite after assertion mapping. No deletion of old DB files/backups.
G3 I11: DB7 clean/0011 upgrade parity, full mandatory CI, negative ACL, E2E, encrypted restore rehearsal, physical HID and browser evidence.
G4 production only: DB8 retention/hold/purge, RPO/RTO, topology/TLS, off-host copy, monitoring, authorized operator and explicit release approval. No automatic deploy on push.
## Target classification and runbook
A clean installation: provision separate owner/migrator/runtime/backup principals, create new empty PG16 database, migrate base→head, verify expected head/catalog/ACL, bootstrap first admin via controlled path, no patient seed.
B existing PG with retained data: identify actual supported revision, read-only inventory, resolve conflicts, stop all writers (app, jobs, admin tools), take final consistent encrypted snapshot and verify isolated restore, apply reviewed upgrade, validate counts/IDs/history/constraints/ACL, switch matching application. Unknown or unsupported revision/data blocks switch. Disposable reset is a separate explicitly authorized action, never default.
C existing SQLite: no import; leave files/backups untouched, remove executable support only.
Before switch record release SHA, artifact/dependency versions, old app SHA/schema head, target identity, backup reference and verified recovery evidence, operator and go/no-go. Keep traffic closed during incompatible transition. Validate config (separate migration/runtime/backup URLs), secrets, secure cookies, health and four-role smoke under actual runtime role. Verify ordinary box receipt and P09 full manifest/partial receipt on synthetic staging, not fake patients in production. Enable backup timer after backup/restore proof; open traffic only after authorization.
## Stop and rollback matrix
Before migration: abort on wrong target, unknown data, missing backup/recovery proof where applicable, unresolved gates, insufficient privileges/disk. No change to existing service.
During migration: stop on failure; transactional rollback where supported, inspect actual revision/catalog before restart; do not assume entire multi-revision run rolled back.
After committed incompatible schema, before traffic: keep writers stopped; reviewed forward fix or restore verified pre-upgrade snapshot to isolated replacement and matching old app/config; verify ownership/ACL/functions and data before switch. Never blindly downgrade/stamp or restore over live DB.
After traffic/write acceptance: freeze writes, preserve current snapshot and evidence; assess post-cutover changes and data loss with authorized operator. Do not silently restore older snapshot; reviewed forward repair or explicit recovery/reconciliation plan required. RPO/RTO not yet approved.
Clean install failure: keep inaccessible, diagnose or recreate only explicitly authorized disposable target; no automatic deletion.
## Backup and evidence
Existing hourly/24 local copies are operational defaults, not retention approval. age encrypted archive, separate backup identity, completeness incl audit/ledger/sequences, versioned ownership/ACL reconstruction after no-owner/no-acl restore, isolated recovery and off-host copy before production. Record backup ID/time, schema/app version, restore result, catalog/ACL checks, smoke and operator decision without credentials or patient data in GitHub. Preserve prior release artifacts and recovery instructions. No actual backup, migration, restore or host inspection performed.
## Disposition
Engineering runbook proposal; no new business rules. Exact deployment commands, host, timing, recovery objectives and authorization pending I00/I11. Next 7C final design readiness review.
