"""Build an unpersisted correction event for the future protected command."""
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from medarchive.adapters.postgresql.models import DomainEvent, Location, Shipment, ShipmentBox
from medarchive.domain.receipt_correction import (
    BOX_RECEIVED_EVENT,
    BOX_RECEIPT_CORRECTED_EVENT,
    BOX_RECEIPT_PAYLOAD_VERSION,
    BOX_RECEIPT_CORRECTION_PAYLOAD_VERSION,
    ReceiptCorrectionPayload,
)


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


def validate_box_receipt_metadata(
    received_at: datetime | None, received_by: UUID | None, original: DomainEvent,
) -> None:
    """Validate current receipt projection before any compensation."""
    if received_at is None or received_by is None:
        raise ValueError("Individual box receipt metadata is absent")
    if received_by != original.actor_user_id:
        raise ValueError("Individual box receipt actor does not match original event")
    # Timestamp equality awaits the individual receipt producer contract.


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
        select(ShipmentBox.received_at, ShipmentBox.received_by)
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
        receipt = (await session.execute(statement)).one_or_none()
        if receipt is None:
            raise ValueError("No handed-over archive shipment manifest entry for this box")
        original = await session.get(DomainEvent, fact.original_event_id, populate_existing=True)
        if original is None:
            raise ValueError("Original receipt event not found")
        event = build_receipt_correction_event(
            fact,
            original,
            actor_id=actor_id,
            receipt_event_type=receipt_event_type,
            correction_event_type=correction_event_type,
            receipt_payload_version=receipt_payload_version,
            allowed_reason_codes=allowed_reason_codes,
        )
        validate_box_receipt_metadata(receipt.received_at, receipt.received_by, original)
        # This pre-compensation check is not a general historical replay validator.
        return event


async def resolve_ordinary_box_receipt_correction(
    session: AsyncSession,
    fact: ReceiptCorrectionPayload,
    *,
    actor_id: UUID,
    allowed_reason_codes: frozenset[str],
) -> DomainEvent:
    """Server-owned event vocabulary; not a mutation or authorization boundary."""
    return await resolve_receipt_correction_event(
        session,
        fact,
        actor_id=actor_id,
        receipt_event_type=BOX_RECEIVED_EVENT,
        correction_event_type=BOX_RECEIPT_CORRECTED_EVENT,
        receipt_payload_version=BOX_RECEIPT_PAYLOAD_VERSION,
        allowed_reason_codes=allowed_reason_codes,
    )


def read_ordinary_box_receipt_correction(
    correction: DomainEvent, original: DomainEvent,
) -> ReceiptCorrectionPayload:
    """Validate a stored historical pair, independently of current receipt state.

    This is a data-integrity helper, not a query, authorization or replay API.
    A historical reason need not remain in today's writable reason catalog.
    """
    if correction.event_type != BOX_RECEIPT_CORRECTED_EVENT:
        raise ValueError("Unexpected correction event type")
    if type(correction.id) is not int or correction.id <= 0:
        raise ValueError("Correction must have a persisted event identifier")
    if correction.id == original.id:
        raise ValueError("Correction cannot reference itself")
    if correction.actor_user_id is None or correction.occurred_at is None:
        raise ValueError("Correction must retain actor and time")
    if not isinstance(correction.payload, Mapping):
        raise ValueError("Correction payload must be a JSON object")
    payload = correction.payload
    if (
        type(payload.get("version")) is not int
        or payload["version"] != BOX_RECEIPT_CORRECTION_PAYLOAD_VERSION
    ):
        raise ValueError("Unsupported correction payload version")
    if set(payload) != {"version", "shipment_id", "box_id", "original_event_id", "reason_code"}:
        raise ValueError("Unexpected correction payload fields")
    if (
        type(correction.correction_of_event_id) is not int
        or type(payload["original_event_id"]) is not int
        or correction.correction_of_event_id != original.id
        or payload["original_event_id"] != original.id
    ):
        raise ValueError("Correction references do not match original event")
    try:
        fact = ReceiptCorrectionPayload(
            UUID(payload["shipment_id"]), UUID(payload["box_id"]),
            payload["original_event_id"], payload["reason_code"],
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid correction payload values") from exc
    if (
        payload["shipment_id"] != str(fact.shipment_id)
        or payload["box_id"] != str(fact.box_id)
        or correction.aggregate_type != "shipment"
        or correction.aggregate_id != fact.shipment_id
    ):
        raise ValueError("Correction aggregate or identifiers do not match")
    if original.correction_of_event_id is not None:
        raise ValueError("A correction is not an original receipt")
    if original.actor_user_id is None or original.occurred_at is None:
        raise ValueError("Original receipt must retain actor and time")
    fact.validate_original_receipt(
        event_id=original.id, event_type=original.event_type,
        expected_receipt_type=BOX_RECEIVED_EVENT,
        aggregate_type=original.aggregate_type, aggregate_id=original.aggregate_id,
        payload=original.payload, expected_payload_version=BOX_RECEIPT_PAYLOAD_VERSION,
    )
    return fact
