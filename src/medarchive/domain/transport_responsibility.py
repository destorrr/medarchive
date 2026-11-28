"""Target E4 transport responsibility projection, independent of legacy ORM.

UX-08-06 owner confirmation 2026-11-27 applies to all three routes.
External storage and authorization are deliberately not inferred here.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


Route = Literal["ordinary_box", "p09_card"]


@dataclass(frozen=True, slots=True)
class TransportFacts:
    source_kind: str
    destination_kind: str
    source_id: UUID
    destination_id: UUID
    courier_id: UUID
    handed_over_at: datetime | None
    received_at: datetime | None


@dataclass(frozen=True, slots=True)
class TransportResponsibility:
    route: Route
    subject_kind: Literal["location", "courier"]
    subject_id: UUID


def classify_transport_route(source_kind: str, destination_kind: str) -> Route:
    if source_kind in {"branch", "central_office"} and destination_kind == "archive":
        return "ordinary_box"
    if (source_kind, destination_kind) == ("archive", "central_office"):
        return "p09_card"
    raise ValueError("Unsupported transport route")


def resolve_transport_responsibility(facts: TransportFacts) -> TransportResponsibility:
    """Resolve one manifest object's effective transport responsibility.

    Ordinary input is a box, P09 input is a card. Receipt must be the
    object's effective receipt, never a shipment-wide timestamp substitute.
    Caller must establish membership, actor/time pair and correction validity.
    This is a pure target rule, not a legacy data reader or access grant.
    """
    route = classify_transport_route(facts.source_kind, facts.destination_kind)
    if facts.source_id == facts.destination_id:
        raise ValueError("Transport endpoints must differ")
    if facts.received_at is not None:
        if facts.handed_over_at is None or facts.received_at < facts.handed_over_at:
            raise ValueError("Receipt requires prior handover")
        return TransportResponsibility(route, "location", facts.destination_id)
    if facts.handed_over_at is not None:
        return TransportResponsibility(route, "courier", facts.courier_id)
    return TransportResponsibility(route, "location", facts.source_id)
