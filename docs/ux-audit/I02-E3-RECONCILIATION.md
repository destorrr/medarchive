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

## E3.1 — route and additive-link coverage

Verified initial inventory CI at 7a92ee0be9ac74616006427390415bfb76688c65: Foundation #1761 617 passed, 53 skipped, 36 warnings; dedicated #147 19+25+35 passed, mandatory manifests verified, all steps successful. Expanded actual PostgreSQL fixture coverage to both permitted incoming routes and both excluded legacy routes, boxed manifest counts, and inconsistent additive item links. Normalize whitespace before trim to detect tabs/newlines at edges. All test rows synthetic and rollback-only. Existing legacy constraints remain in force. No automatic data correction. Positive coverage of other conflict predicates remains outstanding; do not mark E3.1 complete until evidence or a documented invariant explains why a positive fixture is inadmissible.

## E3.1 — regression fixture correction

Foundation #1762 at 70f04cd62a687d816f156be2c4f5c58de601e730: 1 failed, 621 passed, 53 skipped, 36 warnings. Four route cases and additive-link mismatch passed; courier duplicate fixture incorrectly supplied literal backslash-t/backslash-n strings instead of tab/newline characters. Correct Python escapes; retain the normalization SQL and exact duplicate-count assertion. Dedicated #148 succeeded, 19+25+35 cases, both mandatory manifests verified. New CI pending.

## E3.1 — remaining inventory categories

At ae9ef3993b119a8d44bb3ef882f6d010dac0a6c5 all CI succeeded: Foundation #1763, 622 passed, 53 skipped, 36 warnings; Dedicated #149, 19+25+35 and mandatory manifests. Four additional real PostgreSQL cases cover shared legacy fulfillment, conflicting new parent link, inactive shelf with an existing placement, and two active membership conflicts (closed cycle and location mismatch). Membership conflicts are inspected only inside rollback-only savepoints before the existing deferred constraints reject them with SQLSTATE 23514; no committed inconsistent fixture and no disabled guard. Shared fulfillment is supported by the old workflow and cannot be automatically split or assigned to one parent without preserving all original request identities. New tests pending CI. All eleven inventory categories now have proposed positive coverage; acceptance requires green CI. E3.2 will separately classify deterministic mappings versus cases needing explicit disposition; inventory itself is not a migration.

## E3.1 — accepted diagnostic inventory

Exact SHA 2f108c8e503ae9992d94a02b2942f72d14421e5e: Foundation #1764 (46113584767, job 140882304089) 626 passed, 53 skipped, 36 warnings; dedicated #150 (46113584780, job 140882304203) 19+25+35, both manifests verified; all 37 steps successful. All 12 integration cases and 11 inventory categories verified. Accept E3.1 diagnostic module only, not full E3 or live data classification.

## E3.2 — deterministic pending request planning

Preserve legacy parent UUID; derive stable UUIDv5 child from parent ID and fixed versioned name. For initial safe subset, an old new request with no fulfillment/result metadata, active available card in an active archive box, no individual placement, active reservation conflict or competing shipment is a candidate for submitted/pending, with original requested_by/created_at as item addition provenance. No new submission timestamp or history event inferred. Existing child rows are excluded and counted separately pending exact equivalence checks; not treated as accepted. Other statuses and ambiguous cases are explicitly counted for review. Internal candidate identifiers are not public/log output. Implemented read-only planner and four rollback-only real PostgreSQL cases; no automatic writes or migration yet. Actual apply must be atomic under stopped writers, verify exact existing rows and target parent state, refuse unresolved data, preserve old status until E4. Engineering choice: conservative safe subset and deterministic UUIDv5, not new product rules. CI pending.
