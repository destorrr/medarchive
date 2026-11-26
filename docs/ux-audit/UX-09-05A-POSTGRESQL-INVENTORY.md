# UX-09.5A — PostgreSQL inventory
2026-11-26. Design only. Actual models.py blob b7eab361b90c63951dc99ec90881672ca122a1d2 and all 11 migration files inspected (revision links and DDL/function declarations). No live database connection or migration execution.
## ORM inventory (16 tables)
| Existing | Target gap |
|---|---|
| branches, locations | preserve IDs; verify deactivation and singleton scope |
| users | viewer CHECK, branch binding; permissions/session revocation absent in ORM |
| couriers | phone legacy; normalized unique FIO absent |
| cards | active/inactive/under_review CHECK lacks written_off; location XOR shipment and deferred FK to ShipmentCard conflict with box-only manifest; separate accountable custodian needed; FIO GIN not proof of target prefix/e-yo semantics |
| boxes | location XOR shipment; separate custody/reservation and snapshots needed |
| box_cycles, box_card_memberships | existing unique open cycle/active membership; preserve history, verify last-member rules |
| shipments | draft/in_transit/received/exception/cancelled; no partially_received; header receipt only; author/courier snapshots and route guards need review |
| shipment_boxes | composite PK only; no item receipt metadata |
| shipment_cards | composite PK, optional box_id FK; target ordinary manifest box-only, P09 card-only; no item receipt metadata |
| archive_requests | mandatory card_id, single-card status and fulfillment FK; parent/items/reservation target |
| archive_shelves | section_name text, shelf_name, optional QR; section identity/deactivation target review |
| box_placements | unique active placement; retain |
| card_placements | legacy direct shelf placement; preserve history, reconcile active rows before retirement |
| domain_events | append-only guard via migration; typed payload/snapshots/correlation and separate security audit/ledger needed |
## Migration chain
0001_initial → 0002_workflow_guards → 0003_card_placements → 0004_receipt_manifest_guard → 0005_card_external_custody → 0006_external_custody_at_other → 0007_transition_meta → 0008_shipment_integrity → 0009_external_org_custody → 0010_archive_fulfill → 0011_login_throttles.
Single head by static revision links; 0007_external_organization_custody.py actually revision 0009, filename not ordering authority. 0011 creates auth_login_throttles outside ORM: at least 17 application tables by static inventory, plus Alembic metadata. Live schema drift unknown.
0002 guards shipment route/manifest, membership/cycle, consistency, archive request/placement, append-only events and historical deletes. 0003 direct card placement guards. 0004 received manifest assertion. 0005 external custody and archive→CO manifest guards. 0006/0009 custody vocabulary changes. 0007 immutable transition metadata. 0008 object movement/current shipment guards. 0010 transition metadata function replacement. 0011 login throttles.
## Migration implications
Do not rewrite applied revisions. Inventory effective function bodies and trigger dependencies before replacing constraints; additive expand/backfill/validate/switch/contract. Critical coupled changes: card shipment pointer FK vs ordinary box-only manifest; receipt metadata vs header completion; single-card request guard vs group parent; direct card placement guards vs box-only storage; immutable transition metadata vs compensating correction. No fabricated receipt timestamps or grouping of historical requests. Legacy statuses/active placements/viewer/courier phone require read-only preflight and explicit reconciliation. Missing target aggregates: request items, writeoff requests, permissions, security audit, command ledger, discrepancy/receipt facts (physical table layout TBD). 
Next 5B SQLite dependency inventory; 5C target mapping; 5D effective DDL/guard contracts. No production code, migrations or HTML changed; tests not run.
