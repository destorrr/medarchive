# UX-09.4E — archive storage and regroup mapping
2026-11-25. Проектирование. Источники UX-06-INVARIANTS-DB-TEST-MATRIX DB-12,50–59,61–64,67–69; UX-07-WF-02; UX-07-DETAILED-ARCHIVE-RECEIPT-REGROUP-P09-STATES G1/G2. Номера DB-62/63 повторяются в матрице: ссылаться также на заголовок инварианта, исправить нумерацию отдельно.

| UI | Use case | Preconditions | Atomic result |
|---|---|---|---|
| Хранение: схема/реестр | StorageExplorer read model | archive scope, independent object grants | одна выборка, фильтры сохраняются; коробки без размещения/на полках/все |
| Создать коробку | box_create | active archive, scope | Box без BoxCycle |
| Разместить | box_place | active shelf same location, box without active placement/shipment conflict | placement + event, no custody/cycle change |
| Разместить выбранные | bulk box_place | all eligible, one shelf | all or none DB-67 |
| Переместить | box_move | active source placement, active target shelf, scope, no conflicting request/shipment | close old placement, create new, event; no reason |
| Переместить выбранные | bulk box_move | one/multiple source shelves, one target | all or none DB-68 |
| Снять с полки | box_unplace | empty, no open cycle or active shipment | close placement only |
| Перегруппировать | box_repack | source box, selected cards, eligible existing/new target box, archive scope | close/open memberships, cycles, event atomically; preserve placements |
| Повторно наполнить | box_add_card / box_repack | eligible empty box incl. on shelf | new cycle, first membership; preserve placement |
| Исправить размещение | placement_correct (proposed ID) | special permission where unfinished request, closed reason, actual location verified | one box, new placement/event, preserve prior history, no request/custody/membership mutation |
| Карточка коробки/циклы | BoxDetail/BoxCycleDetail read | independent scope | current composition, historical cycle list, separate historical detail |

Filled and empty boxes may be placed/moved. Ordinary move no reason; correction mandatory closed reason, no free comment required, no bulk correction. Ordinary move prohibited while box involved in unfinished CO request; corrective exception only specially authorized active archive actor, not admin by role. No direct shelf placement of cards. No automatic placement on receipt. DB-56: last membership closes cycle; regroup preserves placement, P09 extraction closes placement. Empty placed box can be refilled without unplace. Writeoff separate next block, only empty/unplaced/no shipment. No arbitrary capacity limit. 
DB-69 prohibits bulk extraction from multiple boxes; multi-source regroup distinction unresolved in prior G review. Do not implement multi-source card regroup until reconciled; single-source selected cards to one target is documented. Bulk physical box moves are separate and permitted. 
## Read model and security
Current composition only current cycle, card number/FIO; historical cycle list start/end/count/status only, detail independently authorized. Shipment access not inherited as box/card detail access. Storage diagram and table same scoped data, no fictitious shelf capacity. Active shelves selectable; inactive historical shelves visible only under historical scope. 
## Transactions/tests
Actor from session, lock/recheck principal/membership then relevant requests/shipments/boxes/placements/shelves/cards per canonical order to be finalized; unique active placement/membership/cycle; events and ledger atomic. Test empty/full placement/move, bulk rollback, concurrent placement, last membership regroup vs P09, refill same placement/new cycle, unplace empty only, request move guard and correction exception, stale target, cross-scope, cycle history, direct URL, event failure, no PII leakage. Exact API/event IDs, special permission identifier, DB-69 reconciliation and lock order open. No code/migration/HTML changes; tests not run.
