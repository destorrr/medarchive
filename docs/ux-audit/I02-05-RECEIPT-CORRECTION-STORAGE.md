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

## Verified implementation checkpoint — 2026-11-28

The generic storage component is verified without revision 0016: existing DomainEvent self-FK and append-only trigger, pure payload contract, original receipt identity validator, transient ORM adapter, and rollback-only Foundation tests. 29 domain cases, 11 adapter cases, one structural mapping case and four Foundation storage cases added. All four runs for 6ad5c8650524b017c9c9717039f22bb4983d1b05 and 4417da99abc1a5c91a4226ff6526298d185c7797 completed successfully: Foundation #1741 (46103681322, job 140851559648) and #1742 (46103694078, job 140851598414), each 559 passed, 53 skipped, 36 warnings; dedicated #127 (46103681330, job 140851559990) and #128 (46103694081, job 140851598730), each 19 infrastructure +25 I01 +35 I02, both mandatory manifests verified. All job steps successful.

Decision: reuse existing storage for the verified generic component. This does not constitute final I02.5/E2 acceptance: E2 explicitly requires final contracts for affected fields. Synthetic events do not establish actual receipt or manifest membership, and the adapter is not a protected command. The receipt event type/version, production reason catalog, manifest binding, compensation and authorization contract remain open. No new revision justified by generic storage evidence alone. Do not mark I02.5 accepted or advance E4 based on these tests. I02.3/I02.4 rollback evidence is independently outstanding. Overall accepted packages remain 2/12.


## E2 storage contract and E4/I05 boundaries — 2026-11-28

The current implementation fixes the ordinary receipt/correction vocabulary to shipment.box_received and shipment.box_receipt_corrected, respectively, version 1. The correction JSON object contains only version, shipment_id, box_id, original_event_id and reason_code. The relational correction_of_event_id is the authoritative original-event reference; its value must agree with original_event_id when written by the typed adapter. Aggregate identity is shipment; original receipt actor/time remain on the original event; correction actor/time belong to the new event. No actor/time duplication in payload. JSONB can contain non-object values: reject malformed original payloads explicitly before field validation. Do not infer individual facts from legacy shipment.received.

The E2 resolver reads shipment_boxes.received_at/by and requires both plus matching original actor, after validating route, manifest and original event identity. This is a pre-compensation check, not a historical replay or authorization boundary. Time equality is deliberately not assumed until the E4 producer defines the timestamp contract. The existing draft-only manifest guard prevents actual post-handover receipt writes under E2; preserve it until coupled E4. Positive persisted receipt and mismatched persisted actor scenarios remain mandatory E4 integration tests, along with immutability of manifest identity, no automatic placement/custody reversal and atomic receipt projection/event. Do not synthesize receipt metadata before handover.

The present DomainEvent storage suffices for the verified original/correction reference and reason fact. It does not yet establish a complete compensation outcome schema or common event envelope. Do not add a competing correction table or allocate 0016 merely for these verified fields. Before final E2 acceptance resolve whether any approved compensation outcome requires additional persisted fields. The reason catalog, compensation matrix and exact special permission remain open; no test catalog is product policy. Common command_id/correlation_id/event_version and protected command/ledger/audit/locking are cross-family contracts, not implemented by this adapter. No claim of E4 or I05 acceptance.

CI evidence at 9ae29cf13b3ef50acb52834572af36e740078fc2: Foundation #1755 (46107109205, job 140862206529), 574 passed, 53 skipped, 36 warnings; dedicated #141 (46107109154, job 140862206499), 19 infrastructure +25 I01 +35 I02 passing, both manifests verified. All steps successful, including Foundation backup/restore. Prior eight fixture failures at 05db3c3 were caused by retained manifest guard and are recorded in implementation journal. I02.5 remains open.
