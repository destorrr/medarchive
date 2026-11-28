"""Pure data contract for a future ordinary box receipt correction.

This module does not authorize, persist or execute corrections. The eventual
protected command must resolve the original receipt, validate scope and use
the approved reason catalog before writing a DomainEvent.
"""
from dataclasses import dataclass
from uuid import UUID


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
            "version": 1,
            "shipment_id": str(self.shipment_id),
            "box_id": str(self.box_id),
            "original_event_id": self.original_event_id,
            "reason_code": self.reason_code,
        }
