"""Target E4 manifest invariants, shared by future commands and DB tests."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from medarchive.domain.transport_responsibility import classify_transport_route


@dataclass(frozen=True, slots=True)
class ManifestCard:
    card_id: UUID
    request_item_id: UUID
    request_id: UUID


@dataclass(frozen=True, slots=True)
class Manifest:
    source_kind: str
    destination_kind: str
    request_id: UUID | None
    box_ids: tuple[UUID, ...]
    cards: tuple[ManifestCard, ...]


def validate_manifest(
    manifest: Manifest, *, ready_items: tuple[ManifestCard, ...] = (),
) -> None:
    """Validate the complete target manifest at handover/draft creation.

    Ready items are the authoritative set of extracted positions of the
    fully processed parent, established by the caller under locks.
    Ordinary draft editing may temporarily have an empty manifest.
    This check is for final ordinary handover or P09 draft creation.
    """
    route = classify_transport_route(manifest.source_kind, manifest.destination_kind)
    if route == "ordinary_box":
        if (manifest.request_id is not None or manifest.cards or ready_items
                or not manifest.box_ids
                or len(set(manifest.box_ids)) != len(manifest.box_ids)):
            raise ValueError("Ordinary shipment requires a nonempty box-only manifest")
        return
    if manifest.request_id is None or manifest.box_ids or not manifest.cards:
        raise ValueError("P09 requires one parent and a nonempty card-only manifest")
    for rows in (manifest.cards, ready_items):
        if (any(row.request_id != manifest.request_id for row in rows)
                or len({row.card_id for row in rows}) != len(rows)
                or len({row.request_item_id for row in rows}) != len(rows)):
            raise ValueError("P09 manifest contains conflicting positions")
    if set(manifest.cards) != set(ready_items):
        raise ValueError("P09 manifest must equal the complete ready set")


def receipt_progress(
    *, handed_over_at: datetime | None,
    receipts: tuple[datetime | None, ...],
) -> str:
    """Derive transport receipt progress, not final ordinary closure.

    Effective per-object receipts must already be validated. Ordinary
    discrepancy closure is separate; do not use this result alone to close
    an ordinary shipment. Cancellation is also a separate transition.
    """
    if not receipts:
        raise ValueError("Receipt progress requires a nonempty manifest")
    if handed_over_at is None:
        if any(value is not None for value in receipts):
            raise ValueError("Receipt before handover")
        return "draft"
    if any(value is not None and value < handed_over_at for value in receipts):
        raise ValueError("Receipt before handover")
    received = sum(value is not None for value in receipts)
    if received == 0:
        return "in_transit"
    if received == len(receipts):
        return "received"
    return "partially_received"
