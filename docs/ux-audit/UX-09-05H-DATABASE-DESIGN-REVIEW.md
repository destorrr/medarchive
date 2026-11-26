# UX-09.5H — database design review
2026-11-26. Reviewed 5A–G, G follow-up and 4 cross-contract register. Design phase closure only; not executable schema approval.
## Baseline
PG16 only; no SQLite conversion per owner; preserve 0001–0011. Existing 16 ORM relations plus migration-managed throttle, target 23 logical relations (not final DDL). Four user roles; technical DB principals separate. Ordinary branch→archive and CO→archive box manifests; P09 archive→CO one complete ready-card manifest after all positions processed, per-card receipt, courier custody after handover. No direct branch↔CO. External custody separate, CO balance unchanged. Box/cycle/membership/placement independent. DomainEvent, security audit and ledger distinct.
## Cross-check and corrections
H01 5B data uncertainty and offline converter conditional superseded by owner decision in 5C; no converter. PG contents still unknown.
H02 5G initial scan limitation superseded by follow-up static inventory; AST/runtime proof still pending. Negative SQLite URL test explicitly allowed; gate forbids imports/runtime support, not all text mentions.
H03 5C custody derived model remains proposal, not proven: ordinary card transit pointer cannot reference absent ShipmentCard; resolve route-aware pointer and exception/restore cases before E4.
H04 5C request_item_id NOT NULL applies to new P09 rows only; legacy ordinary ShipmentCard rows require reconciliation/history preservation before global constraint. No fabricated item link.
H05 ordinary partial box receipt and P09 partial card receipt distinct semantics; resolve status projection and discrepancy closure before DDL.
H06 5D runtime DML revoke after all writers converted, including auth throttle/bootstrap; E5/E6 must be coordinated, not early revoke.
H07 5F no-owner/no-acl restore requires versioned ownership/ACL reconstruction; backup read completeness tested under actual backup identity.
H08 5G removal after replacement and assertion mapping; do not delete shared AuthService or templates prematurely.
H09 5C logical count excludes optional correction/session relations and retained historical tables; do not use 23 as exact catalog acceptance.
## Implementation gates
| Gate | Required evidence | Before |
|---|---|---|
| DB1 | read-only PG preflight, effective catalog/trigger bodies, bootstrap SQL vs Alembic reconciliation | E0/E1 |
| DB2 | typed columns, statuses, reason catalogs, session/permission schema, event and ledger signatures | affected E1/E2 |
| DB3 | route-aware custody and reservation model, ordinary/P09 manifest and receipt constraints, discrepancy/correction matrix, restore compatibility | E4 |
| DB4 | DB69 regroup scope, placement grant, writeoff cancellation and packing search reconciliation from R01/R03/R06; no invented rules | affected commands |
| DB5 | cross-family lock graph, replay authorization, atomic event/audit/ledger, concurrency tests | E5/E6 |
| DB6 | scope/read models, timezone, pagination, correction metrics, search normalization/index plans | affected queries |
| DB7 | clean and 0011 upgrade synthetic tests, ACL negatives, rollback/restore, full PG-only suite and AST/import/template checks | release |
| DB8 | retention/hold/purge, RPO/RTO, TLS/topology, off-host backup, real HID/browser and role workflows | production |
## Sequence
E0 preflight → E1 security foundation → E2 additive schema → E3 deterministic reconciliation if retained PG rows → E4 coupled guards → E5 commands and app → E6 validate/revoke → E7 contract/SQLite removal. No old writer against incompatible schema; no destructive reset or stamp bypass. Exact revision IDs not allocated.
## Disposition
5A–H design inventory complete; open gates explicitly carried to UX09.6 test and implementation sequence, UX09.7 cutover and UX10/11 execution. No source, migrations or approved HTML changed. No live PG access or tests. No new business decision; engineering proposals remain conditional. Next UX09.6.
