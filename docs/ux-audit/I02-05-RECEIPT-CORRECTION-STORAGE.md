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


## Historical read integrity — implementation checkpoint

Add a pure stored-pair reader for version 1 ordinary receipt corrections. Validate the authoritative relational reference against the duplicated JSON reference and supplied original ID, event types, aggregate, canonical UUIDs, actor/time presence, exact payload fields and original receipt identity. Reject self-reference and correction chains; preserve both historical facts. Do not revalidate historical reason against today's writable catalog or require current receipt projection for historical reading. This helper does not query, authorize or execute compensation. No new business outcome or reason catalog. The verified storage component does not require a separate relation; full I02.5 remains open pending compensation storage sufficiency and E2 acceptance. CI at 92f4d0ed32fe22540d9a424404b8b6fbcf485aea: Foundation #1756 (46107983250, job 140864919624) 579 passed, 53 skipped, 36 warnings, backup/restore successful; dedicated #142 (46107983198, job 140864919564) 19+25+35 passed, both manifests verified, all steps successful.


## Storage closure candidate — approved repeated receipt and separate consequences

The owner decisions in UX-06-IMPLEMENTATION-READINESS-PLAN (2026-11-21) explicitly permit repeat receipt in the same manifest after correction and absence of an effective receipt; prohibit automatic reversal of custody, placement or location; and require distinct history events for any separately authorized changes. Therefore the E2 correction fact is NOT a state-reset command and its existence alone does NOT establish eligibility for repeat receipt. No unique constraint on shipment/box event identity: repeat receipts have independent event IDs, each correction refers to its particular receipt, not the shipment's latest arbitrary event. Current receipt metadata is a projection, not a historical source. The existing append-only DomainEvent supports this cardinality without a new correction relation. Separate effects are represented by their own event types and versioned payloads under the later command contract, not by rewriting correction version 1 or original receipt. Historical actor snapshot is conditional under the approved decision; no current-name substitution. Do not infer a snapshot or fabricate history.

E2 affected-field contract: existing shipment_boxes received_at/by pair plus DomainEvent id, type, aggregate type/id, actor, occurred_at, correction_of_event_id and JSON object (version 1, shipment_id, box_id, original_event_id, reason_code). The typed writer and reader enforce this payload and link consistency. Exact reason catalog is a server policy dependency, not a new column. Permission and outcome rules remain I05 gates; the E4 current-effective-receipt projection and atomic write contract must be specified before cutover, including explicit invalidation versus retained receipt and repeat receipt eligibility. If that contract requires additional projection fields, E4 must introduce them in a reviewed migration before enabling writes. This is an engineering storage boundary, not approval of any new outcome. No new revision for the historical correction fact. The current E2 closure candidate requires successful real PostgreSQL roundtrip of two receipt/correction pairs for the same identity; the synthetic test verifies capacity and integrity only, not authorization or real workflow. On green CI accept I02.5 historical additive storage only; E2/E4 overall remain open and do not bypass I02.3/4. 

CI at 41e530e66939306373bd5a51f0b0e2be4be03371: Foundation #1758 (46110071582, job 140871423163), 613 passed, 53 skipped, 36 warnings; dedicated #144 (46110071727, job 140871423253), 19+25+35 passed, both manifests verified; all steps successful.


## I02.5 historical additive storage — accepted in isolated CI

Exact SHA a636e37bad30893de13cbff0c588eb8ef1860b47: Foundation #1759 (46110773318, job 140873582881), 614 passed, 53 skipped, 36 warnings; dedicated #145 (46110773356, job 140873582952), 19 infrastructure +25 I01 +35 I02, both mandatory manifests verified. All steps succeeded including encrypted backup/restore. The repeated receipt/correction history roundtrip passed in PostgreSQL. Accept I02.5 historical additive storage only, using existing DomainEvent and version 1 payload without revision 0016. This does not accept effective receipt invalidation, compensation outcomes, protected write command, permission catalog or E4/I05; no workflow cutover. E2 overall remains open because I02.3/I02.4 populated downgrade rehearsals remain blocked and affected projection contracts must be resolved before E4. No production changes.
