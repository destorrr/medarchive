# UX-09.4 — cross-contract review and technical gate register
2026-11-25. All nine A–I mapping files reread. Design review, not implementation approval. Later explicit product decisions override earlier drafts; unresolved conflicts fail closed, not silently approved.
## Baseline
Four roles, no viewer/courier account, PostgreSQL only target. Ordinary routes branch→archive and CO→archive use ShipmentBox; P09 archive→CO uses one complete card manifest after all positions processed, courier custody after handover, item receipt by CO, parent completes only when all outcomes final. No direct branch↔CO. External custody separate, CO balance unchanged. Receipt separate from placement; Box/Cycle/Placement independent. Audit separate from DomainEvent; dashboard_view not role.
## Reconciliation register
| ID | Conflict | Disposition |
|---|---|---|
| R01 | B vs WF08/branch V6 packing search | direct owner instruction allows FIO/number/phone in packing; general FIO only; synchronize source wording and confirm later decision chronology before coding |
| R02 | F vs WF04 C06 single draft | later DB34 multiple independent drafts; supersede old limit |
| R03 | G vs UX06 cancellation until execution | D13/WF-X02 CO before processing, archive thereafter; synchronize old wording |
| R04 | G vs H number correction grant | WF-D03 inherent active admin; verify latest decision, not assignable checkbox |
| R05 | old terminal writeoff vs restore | direct restoration active without automatic location/custody change; explicit exception, preserve original |
| R06 | DB69 multi-source regroup | unresolved; distinguish bulk extraction from regroup; no multi-source implementation until verified |
| R07 | DB62/63 duplicate numbering | editorial defect; cite heading as well, renumber with cross-reference audit |
| R08 | analytics V4 vs V5 | later dashboard access revision CO/archive assignable, admin inherent, branch never |
| R09 | earlier archive custody vs P09 final | latest approved baseline courier custody after handover; per-card CO receipt |
## Technical gates (not new product decisions)
T01 canonical command IDs, typed envelopes, HTTP error/replay, event ownership and schema (A–I).
T02 canonical lock order across principal/grant/request/shipment/box/card/shelf, unique constraints, atomic event+ledger, concurrency (A–H).
T03 QR identity normalization, field limits, old QR after number correction, real HID/browser tests (B,G).
T04 ordinary draft batch create atomicity vs independent results, partial receipt projection, receipt/discrepancy resolution and correction compensation matrix/reasons (C,D).
T05 placement correction grant, DB69, cycle and placement invariants (E).
T06 external custody conflict predicates and active shipment states (F).
T07 writeoff active request uniqueness, execution grant, rejection/cancel reasons, restoration compatibility, number correction authority and phone editing policy (G,H).
T08 session invalidation, role transition grants, last-admin guard, section/location deactivation, audit migration and integrity (H).
T09 analytics scope, timezone/calendar buckets, gross/effective correction semantics, event source, pagination and query performance (I).
T10 production governance: approved retention/hold/purge/backup policy absent; no invented duration or audit export.
T11 UX08 technical gates: real HID, responsive, keyboard, browser, backend, inter-role and transaction tests.
## Next phase
UX09.5 inventory actual models/migrations, SQLite dependencies and deployment, target mapping, preflight/backfill/reconciliation, rollback and cutover plan. No destructive migration or source edits authorized. Resolve gates before implementing affected commands; governance before production. UX09.4 nine drafts and review complete, implementation readiness not established. R register is explicit overlay, not claim that earlier source files were edited. Code, migrations, HTML unchanged; tests not run.
