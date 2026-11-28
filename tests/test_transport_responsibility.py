"""Target route and per-object responsibility tests, without schema activation."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from medarchive.domain.transport_responsibility import (
    TransportFacts, classify_transport_route, resolve_transport_responsibility,
)


@pytest.mark.parametrize("source,destination,route", [
    ("branch", "archive", "ordinary_box"),
    ("central_office", "archive", "ordinary_box"),
    ("archive", "central_office", "p09_card"),
])
def test_transport_responsibility_per_object(source, destination, route):
    source_id, destination_id, courier_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    draft = TransportFacts(source, destination, source_id, destination_id,
                           courier_id, None, None)
    assert classify_transport_route(source, destination) == route
    result = resolve_transport_responsibility(draft)
    assert (result.route, result.subject_kind, result.subject_id) == (
        route, "location", source_id)
    handed = replace(draft, handed_over_at=now)
    result = resolve_transport_responsibility(handed)
    assert (result.subject_kind, result.subject_id) == ("courier", courier_id)
    received = replace(handed, received_at=now + timedelta(seconds=1))
    result = resolve_transport_responsibility(received)
    assert (result.subject_kind, result.subject_id) == ("location", destination_id)
    # Another object in the same shipment has not been received.
    assert resolve_transport_responsibility(handed).subject_id == courier_id


@pytest.mark.parametrize("source,destination", [
    ("branch", "central_office"), ("central_office", "branch"),
    ("archive", "branch"), ("branch", "branch"),
    ("archive", "archive"), ("central_office", "central_office"),
])
def test_excluded_routes(source, destination):
    with pytest.raises(ValueError, match="Unsupported"):
        classify_transport_route(source, destination)


@pytest.mark.parametrize("scenario", ["before_handover", "earlier_receipt", "same_location"])
def test_invalid_transport_facts(scenario):
    now = datetime.now(timezone.utc)
    facts = TransportFacts("archive", "central_office", uuid4(), uuid4(),
                           uuid4(), now, now)
    if scenario == "before_handover":
        facts = replace(facts, handed_over_at=None)
    elif scenario == "earlier_receipt":
        facts = replace(facts, received_at=now - timedelta(seconds=1))
    else:
        facts = replace(facts, destination_id=facts.source_id)
    with pytest.raises(ValueError):
        resolve_transport_responsibility(facts)
