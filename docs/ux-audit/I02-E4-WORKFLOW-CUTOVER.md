# I02/E4 — coupled PostgreSQL workflow cutover

## E3 handoff

Exact SHA d0f22fa49796f9311f6efbf4766523de42a2a2d4: Foundation #1769 (46119972411, job 140902119748), 646 passed, 53 skipped, 36 warnings; Dedicated #155 (46119972448, job 140902119749), 19+25+35 and both manifests, all 37 steps successful. E3 software components verified; actual retained-data disposition and invocation under maintenance still open.

## E4.1 — transport responsibility rule

Source UX-08-06-CUSTODY-REPRESENTATION-PROPOSAL.md, explicit owner confirmation 2026-11-27: all three routes transfer responsibility to courier at actual handover, then to recipient for each received object. This supersedes earlier archive-retains-responsibility wording. No new courier role. Ordinary manifests consist of boxes; P09 manifests consist of cards, one complete fulfillment shipment. Draft/cancel-before-handover retains source responsibility. Effective receipt is per box/card, not shipment-wide. External custody status is separate.

Added pure target route classifier and per-object responsibility function, with 12 unit cases. No schema changes or runtime wiring. Input must be verified effective receipt facts, not inferred legacy timestamps; no access grant. Correction/discrepancy semantics and actual route-aware DB pointer constraints remain DB3 requirements. Existing guards remain active. Next implement target manifest validation and persistence constraints as a coupled cutover; avoid independent weakening of existing FK or triggers. No E4 acceptance or production change claimed.


## E4 — target manifest and receipt progress

Verified 79b0fd1ef5968253caff05f8633d28097a6650d2: Foundation #1770 (46120382821, job 140903367453) 658 passed, 53 skipped, 36 warnings; dedicated #156 (46120382825, job 140903367369) 19+25+35, both manifests, all 37 steps successful. Added target manifest validation: ordinary final handover requires unique nonempty box-only set; P09 draft requires one parent and exact complete extracted card/item set, no duplicates or boxes. Per-object effective receipt progress separates none/partial/all without assuming ordinary discrepancy closure. 19 unit scenarios pending CI. Pure domain component, not DB enforcement; caller must verify fully processed parent and effective receipt facts under locks. No live schema change. I02 scope is E2–E4 only per UX-09-06A; E5–E7 are outside this package. Next coupled persistence constraints; DB3 and retained data gate still open.


## E4 — executable SQL manifest boundary

Verified 4c235ce78d312db6b34791e57533733db692a6c4: Foundation #1771 (46120881020, job 140904895939) 677 passed, 53 skipped, 36 warnings; dedicated #157 (46120881051, job 140904896000) 19+25+35, both manifests, all 37 steps successful. Added candidate PostgreSQL function validating route, manifest type, exact extracted item set, parent/card links and reservations; eight integration cases execute actual SQL in rollback-only Foundation transactions. Function not yet installed in Alembic or wired to production guards. Legacy guards unchanged. Parent processing status, effective physical pointers, receipt transitions, correction/discrepancy closure and coupled DDL remain required. I02 only E2–E4. CI pending; no real data access. I02.3/4 blocker unchanged.


## E4 — candidate SQL receipt and transport pointer checks

Verified 21c1a41b349c6d8d3fe641b56d9fb90095126824: Foundation #1772 (46121328915, job 140906300424) 685 passed, 53 skipped, 36 warnings; Dedicated #158 (46121328881, job 140906300304) 19+25+35, both manifests, all 37 steps successful. Added candidate per-object receipt validator for both routes: actor/time pairing, handover chronology, outstanding box/card transit pointer, P09 item identity/status/reservation and shipment receipt progress. Historical received objects may move later; do not bind current location permanently to an old receipt. Four ordinary draft/negative integration cases pending CI. Positive post-handover target cases require the coupled replacement of legacy manifest and receipt guards; not simulated by disabling them. Ordinary discrepancy closure, effective correction projection, target parent lifecycle, route-aware card membership and activation remain open. No Alembic activation, no real data access. I02.3/4 blocker unchanged.

## E4 — additive installation of PostgreSQL validators

Verified 2a900d73d20cd79ed09d7ed25cca1e61a6131ed1: Foundation #1773 (46121705204, job 140907442587), 689 passed, 53 skipped, 36 warnings; dedicated #159 (46121705203, job 140907442469), 19+25+35, both manifests, all 37 steps successful. Revision 0016_target_validators installs both previously tested functions with frozen SQL and explicit PUBLIC execute revoke. No trigger replacement, no constraint removal, no data mutation. Integration tests now exercise installed functions rather than creating test copies. Manifest predicate excludes non-reserving draft tombstones per approved parent composition rule. Actual target activation, lifecycle, pointer and correction guards remain open. CI pending. I02.3/4 blocker unchanged. Scope E2–E4 only.


## Fixed E4 checklist (owner confirmed; no further subdivision)

| ID | Task | Component status |
|---|---|---|
| E4.1 | Транспортная ответственность | CI verified |
| E4.2 | Доменная модель манифестов и приёмки | CI verified |
| E4.3 | Серверные ограничения манифестов | Installed validators verified at 0016; activation belongs to E4.4/E4.5 |
| E4.4 | Связи, резервирование и приёмка на уровне БД | In progress |
| E4.5 | Согласованная миграция и итоговые тесты | Pending |

Three of five components verified; not an effort estimate or E4 acceptance. Migrations and commits are evidence, not new checklist items. E4.5 must integrate E3 data gate and prove coupled activation, including rollback/restore. No additional E4 tasks.

Verified fc9db87c51e9bbf794b69c0f648bce86b7bb34d4: Foundation #1774 (46122092471, job 140908605567) 689 passed, 53 skipped, 36 warnings; Dedicated #160 (46122092475, job 140908605546) 19+25+35, both manifests, all 37 steps successful. E4.3 installed component verified. E4.4: revision 0017 adds actual history guard for request item identity, original addition facts, deletion and draft tombstones; six integration cases pending CI. Does not yet enforce target lifecycle or complete reservation/receipt constraints. No legacy guards weakened. E4.5 coupled activation remains required. I02.3/4 prior blocker unchanged. No actual retained-data access.


Verified dff04e5abbb81f5e6e28e9e80dd578fd3870c4df: Foundation #1775 (46122484463, job 140909794150), 695 passed, 53 skipped, 36 warnings; Dedicated #161 (46122484475, job 140909794152), 19+25+35, both manifests, all 37 steps successful; backup/restore successful. E4.4 revision 0018 installs the target parent/item reservation consistency validator. It checks target status compatibility, reservation retention through partial receipt and exception outcomes, and nonempty submitted/processing/completed composition. The existing unique active-card index remains in force. Missing-parent boundary test added; positive target lifecycle and deferred activation on both parent and item changes must be verified in E4.5 against the coupled schema. No legacy constraint weakened. I02.3/4 blocker unchanged. CI pending.


Verified e456ac92662a2cdcd0a17ebd316cc1a386c1e302: Foundation #1776 (46122872918, job 140910975407), 696 passed, 53 skipped, 36 warnings; Dedicated #162 (46122872943, job 140910975423), 19+25+35, both manifests, all 37 steps successful. E4.4 revision 0019 connects the installed reservation validator to deferred parent/item triggers, and freezes item composition outside draft for target statuses. Transitional legacy gate retained until E4.5; four installed-trigger integration cases exercise cancellation, atomic reservation release and rejection/rollback. Target-only active statuses still require coupled parent DDL, and manifest/receipt integration is outstanding. No legacy constraints removed. I02.3/4 blocker unchanged. CI pending.
