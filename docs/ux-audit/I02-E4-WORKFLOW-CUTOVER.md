# I02/E4 — coupled PostgreSQL workflow cutover

## E3 handoff

Exact SHA d0f22fa49796f9311f6efbf4766523de42a2a2d4: Foundation #1769 (46119972411, job 140902119748), 646 passed, 53 skipped, 36 warnings; Dedicated #155 (46119972448, job 140902119749), 19+25+35 and both manifests, all 37 steps successful. E3 software components verified; actual retained-data disposition and invocation under maintenance still open.

## E4.1 — transport responsibility rule

Source UX-08-06-CUSTODY-REPRESENTATION-PROPOSAL.md, explicit owner confirmation 2026-11-27: all three routes transfer responsibility to courier at actual handover, then to recipient for each received object. This supersedes earlier archive-retains-responsibility wording. No new courier role. Ordinary manifests consist of boxes; P09 manifests consist of cards, one complete fulfillment shipment. Draft/cancel-before-handover retains source responsibility. Effective receipt is per box/card, not shipment-wide. External custody status is separate.

Added pure target route classifier and per-object responsibility function, with 12 unit cases. No schema changes or runtime wiring. Input must be verified effective receipt facts, not inferred legacy timestamps; no access grant. Correction/discrepancy semantics and actual route-aware DB pointer constraints remain DB3 requirements. Existing guards remain active. Next implement target manifest validation and persistence constraints as a coupled cutover; avoid independent weakening of existing FK or triggers. No E4 acceptance or production change claimed.


## E4 — target manifest and receipt progress

Verified 79b0fd1ef5968253caff05f8633d28097a6650d2: Foundation #1770 (46120382821, job 140903367453) 658 passed, 53 skipped, 36 warnings; dedicated #156 (46120382825, job 140903367369) 19+25+35, both manifests, all 37 steps successful. Added target manifest validation: ordinary final handover requires unique nonempty box-only set; P09 draft requires one parent and exact complete extracted card/item set, no duplicates or boxes. Per-object effective receipt progress separates none/partial/all without assuming ordinary discrepancy closure. 19 unit scenarios pending CI. Pure domain component, not DB enforcement; caller must verify fully processed parent and effective receipt facts under locks. No live schema change. I02 scope is E2–E4 only per UX-09-06A; E5–E7 are outside this package. Next coupled persistence constraints; DB3 and retained data gate still open.
