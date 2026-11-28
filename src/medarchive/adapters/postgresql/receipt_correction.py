"""Build an unpersisted correction event for the future protected command."""
from uuid import UUID

from medarchive.adapters.postgresql.models import DomainEvent
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
