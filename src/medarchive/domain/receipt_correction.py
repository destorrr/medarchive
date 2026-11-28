"""Pure data contract for a future ordinary box receipt correction.

This module does not authorize, persist or execute corrections. The eventual
protected command must resolve the original receipt, validate scope and use
the approved reason catalog before writing a DomainEvent.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID


# Server-owned vocabulary for new ordinary per-box receipt events.
# Legacy shipment.received remains a distinct whole-shipment fact.
BOX_RECEIVED_EVENT = "shipment.box_received"
BOX_RECEIPT_CORRECTED_EVENT = "shipment.box_receipt_corrected"
BOX_RECEIPT_PAYLOAD_VERSION = 1
BOX_RECEIPT_CORRECTION_PAYLOAD_VERSION = 1


@dataclass(frozen=True, slots=True)
class ReceiptCorrectionPayload:
    shipment_id: UUID
    box_id: UUID
    original_event_id: int
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.shipment_id, UUID) or not isinstance(self.box_id, UUID):
            raise TypeError("Shipment and box identifiers must be UUIDs")
        if type(self.original_event_id) is not int or self.original_event_id <= 0:
            raise ValueError("Original event identifier must be a positive integer")
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise ValueError("A reason code is required")
        if self.reason_code != self.reason_code.strip():
            raise ValueError("Reason code must not contain surrounding whitespace")

    def to_event_payload(self, *, allowed_reason_codes: frozenset[str]) -> dict[str, str | int]:
        """Build versioned JSON data only for a supplied closed reason catalog."""
        if self.reason_code not in allowed_reason_codes:
            raise ValueError("Unknown receipt correction reason code")
        return {
            "version": BOX_RECEIPT_CORRECTION_PAYLOAD_VERSION,
            "shipment_id": str(self.shipment_id),
            "box_id": str(self.box_id),
            "original_event_id": self.original_event_id,
            "reason_code": self.reason_code,
        }


    def validate_original_receipt(
        self,
        *,
        event_id: int,
        event_type: str,
        expected_receipt_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        payload: Mapping[str, object],
        expected_payload_version: int,
    ) -> None:
        """Validate a resolved per-box receipt fact, without changing any state.

        The caller supplies the approved receipt type and schema version.
        Legacy whole-shipment receipts are not per-box receipt evidence.
        Authorization, manifest membership and persistence remain command duties.
        """
        if type(event_id) is not int or event_id != self.original_event_id:
            raise ValueError("Original receipt event identifier does not match")
        if not expected_receipt_type or event_type != expected_receipt_type:
            raise ValueError("Original event is not the expected receipt type")
        if aggregate_type != "shipment" or aggregate_id != self.shipment_id:
            raise ValueError("Original receipt belongs to another aggregate")
        if not isinstance(payload, Mapping):
            raise ValueError("Original receipt payload must be a JSON object")
        if (
            type(expected_payload_version) is not int
            or expected_payload_version <= 0
            or type(payload.get("version")) is not int
            or payload["version"] != expected_payload_version
        ):
            raise ValueError("Unsupported original receipt payload version")
        if payload.get("box_id") != str(self.box_id):
            raise ValueError("Original receipt does not identify the requested box")
        if payload.get("shipment_id") != str(self.shipment_id):
            raise ValueError("Original receipt does not identify the requested shipment")
