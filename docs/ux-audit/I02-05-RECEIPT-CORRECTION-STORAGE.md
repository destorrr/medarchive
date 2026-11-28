# I02.5 — receipt correction storage boundary

2026-11-28. Engineering proposal for isolated I02 implementation; not a new business decision or acceptance.

## Sources
- UX-09-04D-ARCHIVE-RECEIPT-MAPPING: X04, original receipt retained, closed reason catalog, compensation matrix open.
- UX-09-05C-TARGET-RELATIONAL-MODEL: optional correction relation not selected.
- UX-09-05D-CONSTRAINTS-AND-COMMANDS: append-only history, protected command boundary.
- UX-09-05E-ALEMBIC-MIGRATION-SEQUENCE: E2 requires final contracts for affected fields.
- UX-06-IMPLEMENTATION-READINESS-PLAN: owner decisions dated 2026-11-21 on controlled correction, archive scope, mandatory reason.

## Engineering recommendation
Use the existing DomainEvent as the authoritative historical fact, with a versioned correction payload referencing the original receipt event and the shipment/box manifest identity. Do not create a second authoritative event log. A separate relational projection is justified only if referential integrity or query requirements cannot be satisfied by the event contract; select that design before allocating revision 0016. Original receipt actor/time and manifest remain intact. A correction records its own actor/time and mandatory catalog code. No free-text reason required.

## Unresolved contract dependencies
Exact reason codes, original receipt event type/version and linkage, permissible compensation outcomes and their impact on current receipt state, effective permission code, and concurrency with subsequent placement. Do not invent values or change shipment state based solely on existence of a correction event. No automatic custody, placement or location reversal. Ordinary branch/CO-to-archive box receipts only; P09 card receipt is not included.

## Acceptance
Verify original event and manifest identity, retained original receipt metadata, rejection of invalid reason or foreign receipt reference, no implicit state changes, atomic event/command behavior and scoped authorization in the eventual I05 command. Historical facts remain append-only. No fabricated legacy history. E2 storage acceptance is distinct from I05 workflow acceptance.

I02.5 remains open; no DDL or tests introduced by this proposal. I02.3 and I02.4 populated downgrade evidence remains independently outstanding. No production changes.
