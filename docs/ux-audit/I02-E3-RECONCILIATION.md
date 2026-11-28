# I02/E3 — reconciliation implementation

## E3.1 read-only inventory (initial)

Source: UX-09-05E E3, UX-09-05C/H and migrations 0001–0015. The new adapter
reconciliation.inspect_reconciliation accepts an existing async PostgreSQL
connection. It executes one fixed SELECT of counts only. No writes, connection
fallback, transaction ownership, identifiers or patient names in output.
No production connection or actual retained-data inspection is authorized here.

Inventory distinguishes unmapped legacy requests, legacy boxed card manifests,
viewer accounts (including inactive), normalized courier duplicate groups
(including inactive), excluded historical routes, active individual placements,
inconsistent active membership/box placement, conflicting request/item links,
and shared legacy fulfillment shipments. Courier normalization is a diagnostic
candidate (lowercase, trim, collapse whitespace), not a product uniqueness
or automatic merge rule. Nonzero counts require review/disposition, not
necessarily corruption: historical excluded routes and ordinary card manifests
may legitimately exist. A zero inventory is not full E3 acceptance.

Tests use the existing rollback-only Foundation PostgreSQL fixture, synthetic
rows and count deltas. Remaining E3 work: prove every query against positive
and negative fixtures, determine deterministic request mapping from approved
facts, resolve shared fulfillment and historical manifest cases, reconcile
active placements/cycles, prove safe transactional conversion/empty no-op.
No schema revision, mutation or E4 cutover yet. I02.3/4 downgrade blocker
retained; this is independent read-only preparation.
