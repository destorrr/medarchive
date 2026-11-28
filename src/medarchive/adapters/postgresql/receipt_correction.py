"""Build an unpersisted correction event for the future protected command."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from medarchive.adapters.postgresql.models import DomainEvent, Location, Shipment, ShipmentBox
from medarchive.domain.receipt_correction import ReceiptCorrectionPayload


def build_receipt_correction_event(
    fact: ReceiptCorrectionPayload,
    original: DomainEvent,
    *,
    actor_id: UUID,
    receipt_event_type: str,
    correction_event_type: str,
    receipt_payload_version: int,
    allowed_reason_codes: frozenset[str],
) -> DomainEvent:
    """Map a validated fact without adding it to a session or committing.

    The future command must obtain the original from the database, authorize
    the actor, verify the manifest, and supply trusted event and reason
    catalogs. This helper alone is not a business command.
    """
    if not isinstance(actor_id, UUID):
        raise TypeError("Correction actor identifier must be a UUID")
    if not correction_event_type.strip() or correction_event_type == receipt_event_type:
        raise ValueError("A distinct correction event type is required")
    if original.correction_of_event_id is not None:
        raise ValueError("A correction is not an original receipt")
    if original.actor_user_id is None or original.occurred_at is None:
        raise ValueError("Original receipt must retain actor and time")
    fact.validate_original_receipt(
        event_id=original.id,
        event_type=original.event_type,
        expected_receipt_type=receipt_event_type,
        aggregate_type=original.aggregate_type,
        aggregate_id=original.aggregate_id,
        payload=original.payload,
        expected_payload_version=receipt_payload_version,
    )
    return DomainEvent(
        event_type=correction_event_type,
        aggregate_type="shipment",
        aggregate_id=fact.shipment_id,
        actor_user_id=actor_id,
        correction_of_event_id=original.id,
        payload=fact.to_event_payload(allowed_reason_codes=allowed_reason_codes),
    )


async def resolve_receipt_correction_event(
    session: AsyncSession,
    fact: ReceiptCorrectionPayload,
    *,
    actor_id: UUID,
    receipt_event_type: str,
    correction_event_type: str,
    receipt_payload_version: int,
    allowed_reason_codes: frozenset[str],
) -> DomainEvent:
    """Resolve real manifest membership and route before building a fact.

    Read-only helper: no flush, insert, commit or state transition. The protected
    command must authorize and lock/recheck the relevant rows in its transaction.
    Event vocabulary and reason catalog must come from trusted server policy.
    """
    source = aliased(Location)
    destination = aliased(Location)
    statement = (
        select(ShipmentBox.box_id)
        .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
        .join(source, source.id == Shipment.from_location_id)
        .join(destination, destination.id == Shipment.to_location_id)
        .where(
            ShipmentBox.shipment_id == fact.shipment_id,
            ShipmentBox.box_id == fact.box_id,
            Shipment.handed_over_at.is_not(None),
            source.kind.in_(("branch", "central_office")),
            destination.kind == "archive",
        )
    )
    with session.no_autoflush:
        if await session.scalar(statement) is None:
            raise ValueError("No handed-over archive shipment manifest entry for this box")
        original = await session.get(DomainEvent, fact.original_event_id, populate_existing=True)
        if original is None:
            raise ValueError("Original receipt event not found")
        return build_receipt_correction_event(
            fact,
            original,
            actor_id=actor_id,
            receipt_event_type=receipt_event_type,
            correction_event_type=correction_event_type,
            receipt_payload_version=receipt_payload_version,
            allowed_reason_codes=allowed_reason_codes,
        )
