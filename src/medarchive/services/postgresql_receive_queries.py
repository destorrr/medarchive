"""Shared read model for recipient-side shipment incident queues."""
from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from medarchive.adapters.postgresql.models import Card, Courier, Location, Shipment, ShipmentBox, ShipmentCard, Box

async def exception_shipments(session: AsyncSession, destination_id: uuid.UUID) -> list[dict]:
    """Return unresolved incidents addressed to exactly one receiving location."""
    rows = (await session.execute(
        select(Shipment, Location, Courier)
        .join(Location, Location.id == Shipment.from_location_id)
        .join(Courier, Courier.id == Shipment.courier_id)
        .where(Shipment.to_location_id == destination_id, Shipment.status == "exception")
        .order_by(Shipment.exception_at, Shipment.shipment_number)
    )).all()
    incidents = []
    for shipment, source, courier in rows:
        manifest_rows = (await session.execute(
            select(ShipmentCard, Card).join(Card, Card.id == ShipmentCard.card_id)
            .where(ShipmentCard.shipment_id == shipment.id).order_by(Card.card_number)
        )).all()
        boxes = list((await session.scalars(
            select(Box).join(ShipmentBox, ShipmentBox.box_id == Box.id)
            .where(ShipmentBox.shipment_id == shipment.id).order_by(Box.box_number)
        )).all())
        incidents.append({
            "id": shipment.id, "number": shipment.shipment_number,
            "source": source.name, "courier": courier.full_name,
            "exception_at": shipment.exception_at, "exception_note": shipment.exception_note,
            "box_count": len(boxes), "card_count": len(manifest_rows),
            "boxes": [{"box_number": box.box_number, "cards": [
                {"card_number": card.card_number, "full_name": card.full_name}
                for row, card in manifest_rows if row.box_id == box.id
            ]} for box in boxes],
            "standalone_cards": [
                {"card_number": card.card_number, "full_name": card.full_name}
                for row, card in manifest_rows if row.box_id is None
            ],
        })
    return incidents
