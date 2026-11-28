"""E3 read-only reconciliation inventory for the additive PostgreSQL schema.

Counts are diagnostic, not authorization to migrate or repair rows. Run inside
a consistent snapshot with writers stopped before any actual cutover.
"""
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class ReconciliationInventory:
    legacy_requests: int
    legacy_box_manifest_cards: int
    viewer_accounts: int
    courier_duplicate_groups: int
    excluded_route_shipments: int
    active_individual_placements: int
    conflicting_active_memberships: int
    conflicting_active_box_placements: int
    conflicting_request_links: int
    conflicting_item_links: int
    shared_legacy_fulfillments: int

    @property
    def requires_review(self) -> bool:
        """Nonzero inventory requires reconciliation or explicit disposition."""
        return any(getattr(self, name) for name in self.__dataclass_fields__)


# One statement gives a consistent statement snapshot, even at READ COMMITTED.
# Return counts only; never names, medical card identifiers or free-text notes.
_INVENTORY = text("""
SELECT
    (SELECT count(*) FROM public.archive_requests r
     WHERE NOT EXISTS (SELECT 1 FROM public.archive_request_items i
                       WHERE i.request_id = r.id AND i.card_id = r.card_id))
        AS legacy_requests,
    (SELECT count(*) FROM public.shipment_cards WHERE box_id IS NOT NULL)
        AS legacy_box_manifest_cards,
    (SELECT count(*) FROM public.users WHERE role = 'viewer')
        AS viewer_accounts,
    (SELECT count(*) FROM (
        SELECT lower(btrim(regexp_replace(full_name, '[[:space:]]+', ' ', 'g')))
        FROM public.couriers
        GROUP BY 1 HAVING count(*) > 1
    ) duplicates) AS courier_duplicate_groups,
    (SELECT count(*) FROM public.shipments s
     JOIN public.locations src ON src.id = s.from_location_id
     JOIN public.locations dst ON dst.id = s.to_location_id
     WHERE NOT ((src.kind IN ('branch', 'central_office') AND dst.kind = 'archive')
                OR (src.kind = 'archive' AND dst.kind = 'central_office')))
        AS excluded_route_shipments,
    (SELECT count(*) FROM public.card_placements WHERE removed_at IS NULL)
        AS active_individual_placements,
    (SELECT count(*) FROM public.box_card_memberships m
     JOIN public.box_cycles cy ON cy.id = m.box_cycle_id
     JOIN public.boxes b ON b.id = cy.box_id
     JOIN public.cards c ON c.id = m.card_id
     WHERE m.removed_at IS NULL AND (
         cy.ended_at IS NOT NULL
         OR c.current_location_id IS DISTINCT FROM b.current_location_id
         OR c.current_shipment_id IS DISTINCT FROM b.current_shipment_id))
        AS conflicting_active_memberships,
    (SELECT count(*) FROM public.box_placements p
     JOIN public.boxes b ON b.id = p.box_id
     JOIN public.archive_shelves sh ON sh.id = p.shelf_id
     LEFT JOIN public.locations loc ON loc.id = b.current_location_id
     WHERE p.removed_at IS NULL AND (
         loc.kind IS DISTINCT FROM 'archive'
         OR b.current_shipment_id IS NOT NULL
         OR NOT sh.is_active))
        AS conflicting_active_box_placements,
    (SELECT count(*) FROM public.archive_requests r
     LEFT JOIN public.shipments s ON s.id = r.fulfillment_shipment_id
     WHERE r.fulfillment_shipment_id IS NOT NULL AND (
         (s.request_id IS NOT NULL AND s.request_id <> r.id)
         OR NOT EXISTS (SELECT 1 FROM public.shipment_cards sc
                        WHERE sc.shipment_id = s.id AND sc.card_id = r.card_id
                          AND sc.box_id IS NULL)))
        AS conflicting_request_links,
    (SELECT count(*) FROM public.shipment_cards sc
     JOIN public.archive_request_items i ON i.id = sc.request_item_id
     JOIN public.shipments s ON s.id = sc.shipment_id
     WHERE sc.card_id <> i.card_id OR sc.box_id IS NOT NULL
        OR s.request_id IS DISTINCT FROM i.request_id)
        AS conflicting_item_links,
    (SELECT count(*) FROM (
        SELECT fulfillment_shipment_id FROM public.archive_requests
        WHERE fulfillment_shipment_id IS NOT NULL
        GROUP BY fulfillment_shipment_id HAVING count(*) > 1
    ) shared) AS shared_legacy_fulfillments
""")


async def inspect_reconciliation(connection: AsyncConnection) -> ReconciliationInventory:
    """Inspect only; no transaction ownership, autoflush, commits or writes."""
    row = (await connection.execute(_INVENTORY)).mappings().one()
    return ReconciliationInventory(**row)
