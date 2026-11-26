# UX-09.6A — implementation packages and dependency gates
2026-11-26. Planning only. Sources: 5H DB1–DB8, 4 cross-contract R/T register, UX08 approved baseline, UX06 readiness plan. Package IDs are proposed engineering sequencing, not new product decisions or authorization.
| Package | Scope | Dependencies | Acceptance evidence |
|---|---|---|---|
| I00 | Pin baseline, isolated PG16, baseline pytest/CI, read-only catalog/data/DML inventory | authorization | DB1 report, reproducible test environment, no real patient data |
| I01 | Shared auth helpers, PG test fixtures, security principals, command/event/ledger foundation | I00 | DB2/DB5 foundation, password/session regression, role ACL tests |
| I02 | Additive schema, deterministic reconciliation, coupled route-aware guards | I01 | E2–E4, DB3 contracts, clean/upgrade and rollback tests |
| I03 | Admin directories, user lifecycle, grants, sessions, audit | I01/I02 | four roles, last-admin concurrency, no self grant, audit atomicity |
| I04 | Card QR/manual intake, search, box membership/cycles, branch packing | I02/I03 | QR duplicate and atomic create+pack, search scope, cycle invariants; R01 resolved |
| I05 | Ordinary shipments branch/CO→archive, courier, per-box receipt/discrepancy | I04 | immutable manifest, no empty box, one handover, per-box custody, correction contract resolved |
| I06 | Archive placement, regroup, box writeoff | I04/I05 | empty box placement allowed, cycle history, DB69 resolved before affected regroup |
| I07 | P09 group request, processing, full manifest, handover, CO receipt | I04/I05/I06 | all positions processed before single shipment; no subset, per-card receipt, cancellation race, terminal outcomes |
| I08 | CO external custody, card writeoff/restore/correction | I03/I04/I07 | no balance change external, conflicts, reason/grant and restoration matrix resolved |
| I09 | Role home, registries, history, analytics, shared UX integration | I03–I08 | scope, dashboard_view, real counts, filters, approved prototype parity |
| I10 | PG-only startup/config, legacy retirement, revoke runtime DML | I01–I09 | E6/E7, all writers covered, AST/import/route checks, full suite |
| I11 | Deployment, encrypted restore, cross-role/browser/HID acceptance | I10 | DB7/DB8, no mandatory skips, governance before production |
Shared frontend shell/components can be developed after I01 and stable contracts, but not treated as independent business acceptance. I02 must not expose incompatible schema to old writers; isolated integration branch, coordinated release. I03–I09 vertical slices include SQL command, service/API, read model, UI, events and tests; no direct DML workaround. Exact DDL and command signatures resolved before affected package; R/T/DB gates remain explicit. I00–I11 are proposed order, no effort estimate.
## Definition of done per package
Trace approved workflow/screen/command/invariant to tests; positive/negative authorization and scope; transaction rollback and replay where applicable; concurrent conflicting operations on separate PG connections; event/audit/ledger consistency; browser keyboard/error/empty/loading states for UI; no PHI in fixtures/logs; review migrations and effective ACL; document evidence (commit, CI run, test IDs, known gaps). No package passes on mock-only DB tests or skipped required checks. No prototype edits without separate approval.
## Next
6B test matrix by workflow and invariant; 6C CI/test environments and evidence gates; 6D final dependency/acceptance review. No source/migrations/HTML changed; tests not executed.
