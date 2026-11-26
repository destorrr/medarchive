# UX-09.6D — implementation plan review
2026-11-26. Reread 6A–C and 5H. Planning closure, not implementation acceptance.
## Package acceptance mapping
| Package | Required test groups (incremental scope) | CI / evidence |
|---|---|---|
| I00 | M01 baseline inventory, existing suite baseline | J01–J05 baseline as available; record failures/skips, no target pass claim |
| I01 | S01 foundation, S02, S03 foundation | J01–J06 applicable foundation |
| I02 | M01, B01/L01/L02/P01–P04 schema-level invariants | J01–J06, J08 schema recovery |
| I03 | A01,S01–S03 | J01–J07,J08 security recovery |
| I04 | C01,C02,B01,S03,U02 | J01–J07 |
| I05 | L01–L03,A01 reassignment,S03,U02 | J01–J07 |
| I06 | H01,H02,B01,S03,U02 | J01–J07 |
| I07 | P01–P05,S03,U02 | J01–J07 |
| I08 | X01–X03,S03,U02 | J01–J07 |
| I09 | U01,U02,C02 | J01–J07 |
| I10 | Z01,S01 final, regression of all implemented groups | J01–J09 |
| I11 | Z02,U02 physical HID, E2E1–5, all prior groups | J01–J10; manual evidence separate from automated aggregate |
J08 required for schema/security/backup changes and release regardless of row shorthand. All previously accepted applicable regression cases remain mandatory. Package manifest defines concrete IDs, not merely group labels. No required skip/xfail or missing report accepted.
## Reconciliation
D01 I02 schema guards may depend on future I05/I07 commands: define and test minimal protected SQL boundaries in isolated integration environment before enabling coupled guards; never expose incompatible schema to old writers. No direct runtime DML workaround. Split additive vs coupled revisions if necessary.
D02 S01 final runtime DML denial cannot pass before I10; early tests verify foundation ACL and protected objects, final revoke only after all writers converted including auth/bootstrap/jobs.
D03 U02 browser tests incremental; physical HID is I11 gate, not required for every PR; J10 manual evidence cannot be inferred from CI green.
D04 M01 baseline I00 differs from clean/upgrade acceptance I02; baseline failures recorded and triaged, not waived for final release.
D05 A01 reassignment requires shipment draft, so I03 tests directory/authority prerequisites and I05 tests full workflow.
D06 H02 reservation-related move guard requires P09 model; schema-level test I02/I06, full cross-workflow regression I07. No unsupported multi-source regroup.
D07 6B 25 groups verified by table row count; wording 'not 24 individual tests' editorial residue; groups not individual cases. DB IDs duplicate subjects retained; coverage not proven by group mapping.
D08 CI J09 phased, J08 conditional but mandatory on affected changes, aggregate check must evaluate required job manifest and fail closed. Branch protection and runner access not verified.
## Open gate routing
DB1 I00; DB2 I01/I02 before affected DDL; DB3 I02/I05/I07/I08 before coupled guards; DB4 I04/I06/I08 before affected commands; DB5 I01 foundation and each writer, final I10; DB6 I04/I09 before affected queries; DB7 I02/I10/I11; DB8 I11 before production. R/T register remains authoritative; unresolved contracts block affected package, not unrelated planning. No production authorization.
## Completion evidence
For each package record source commit, implemented command/screen/invariant IDs, concrete tests and CI run, environment versions, migration head/catalog/ACL evidence, defects and unresolved gates. Status planned/in progress/blocked/verified; verified only after applicable evidence. No effort estimate inferred from package count.
## Disposition
6A–D design complete. Next UX09.7 cutover plan: isolated implementation branch and baseline, migration/backup/rollback gates, release and operator checklist. No source/migration/workflow/approved HTML changes; no tests or live DB checks executed. Sequencing and gate mapping are engineering proposals.
