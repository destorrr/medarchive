"""E3.2 conservative legacy request mapping plan; no workflow writes.

Only an unprocessed request for a currently boxed archive card can be
automatically proposed as submitted/pending with known creation provenance.
Other states remain explicit review cases until their historical evidence and
target constraints are reconciled. This is not a migration or command.
"""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class PendingRequestMapping:
    parent_id: UUID
    item_id: UUID
    card_id: UUID
    added_by: UUID
    added_at: datetime


@dataclass(frozen=True, slots=True)
class RequestMappingPlan:
    candidates: tuple[PendingRequestMapping, ...]
    review_count: int
    existing_item_count: int


# Deterministic namespace scoped by the preserved parent UUID. No generated
# historical event, submission timestamp, extraction or receipt fact.
_QUERY = text("""
SELECT r.id AS parent_id, r.card_id, r.requested_by, r.created_at,
       EXISTS (SELECT 1 FROM public.archive_request_items i
               WHERE i.request_id = r.id) AS has_items,
       (
           r.status = 'new'
           AND r.fulfillment_shipment_id IS NULL
           AND r.fulfilled_by IS NULL AND r.completed_at IS NULL
           AND r.result_note IS NULL
           AND u.role = 'central_office'
           AND c.status = 'active' AND c.custody_status = 'available'
           AND loc.kind = 'archive' AND c.current_shipment_id IS NULL
           AND EXISTS (
               SELECT 1 FROM public.box_card_memberships m
               JOIN public.box_cycles cy ON cy.id = m.box_cycle_id
               JOIN public.boxes b ON b.id = cy.box_id
               WHERE m.card_id = c.id AND m.removed_at IS NULL
                 AND cy.ended_at IS NULL AND b.status = 'active'
                 AND b.current_location_id = c.current_location_id
                 AND b.current_shipment_id IS NULL
           )
           AND NOT EXISTS (
               SELECT 1 FROM public.card_placements p
               WHERE p.card_id = c.id AND p.removed_at IS NULL
           )
           AND NOT EXISTS (
               SELECT 1 FROM public.archive_request_items i
               WHERE i.card_id = c.id AND i.reservation_active
           )
           AND NOT EXISTS (
               SELECT 1 FROM public.shipments s WHERE s.request_id = r.id
           )
           AND NOT EXISTS (
               SELECT 1 FROM public.shipment_cards sc
               JOIN public.shipments s ON s.id = sc.shipment_id
               WHERE sc.card_id = c.id AND s.status IN ('draft', 'in_transit', 'exception')
           )
       ) AS eligible
FROM public.archive_requests r
JOIN public.cards c ON c.id = r.card_id
JOIN public.users u ON u.id = r.requested_by
LEFT JOIN public.locations loc ON loc.id = c.current_location_id
ORDER BY r.id
""")


async def plan_pending_request_mappings(connection: AsyncConnection) -> RequestMappingPlan:
    """Internal mapping data; never log or expose candidate identifiers.

    Any existing items are excluded from automatic planning and require separate
    equality/consistency verification before actual migration. No writes.
    """
    rows = (await connection.execute(_QUERY)).mappings()
    candidates = []
    review_count = existing_item_count = 0
    for row in rows:
        if row["has_items"]:
            existing_item_count += 1
        elif row["eligible"]:
            parent_id = row["parent_id"]
            candidates.append(PendingRequestMapping(
                parent_id=parent_id,
                item_id=uuid5(parent_id, "medarchive:legacy-request-item:v1"),
                card_id=row["card_id"],
                added_by=row["requested_by"],
                added_at=row["created_at"],
            ))
        else:
            review_count += 1
    return RequestMappingPlan(tuple(candidates), review_count, existing_item_count)
