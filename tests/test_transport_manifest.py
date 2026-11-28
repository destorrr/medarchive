from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from medarchive.domain.transport_manifest import (
    Manifest, ManifestCard, receipt_progress, validate_manifest,
)


@pytest.mark.parametrize("source", ["branch", "central_office"])
def test_ordinary_box_only(source):
    manifest = Manifest(source, "archive", None, (uuid4(), uuid4()), ())
    validate_manifest(manifest)
    for invalid in (
        replace(manifest, box_ids=()),
        replace(manifest, box_ids=(manifest.box_ids[0],) * 2),
        replace(manifest, request_id=uuid4()),
        replace(manifest, cards=(ManifestCard(uuid4(), uuid4(), uuid4()),)),
    ):
        with pytest.raises(ValueError):
            validate_manifest(invalid)


@pytest.mark.parametrize("scenario", ["valid", "subset", "extra", "wrong_parent",
                                      "wrong_card", "duplicate_card", "duplicate_item",
                                      "box", "empty", "no_parent"])
def test_p09_full_manifest(scenario):
    parent = uuid4()
    first = ManifestCard(uuid4(), uuid4(), parent)
    second = ManifestCard(uuid4(), uuid4(), parent)
    ready = (first, second)
    manifest = Manifest("archive", "central_office", parent, (), ready[::-1])
    if scenario == "valid":
        validate_manifest(manifest, ready_items=ready)
        return
    invalid = {
        "subset": replace(manifest, cards=(first,)),
        "extra": replace(manifest, cards=ready + (ManifestCard(uuid4(), uuid4(), parent),)),
        "wrong_parent": replace(manifest, cards=(first, replace(second, request_id=uuid4()))),
        "wrong_card": replace(manifest, cards=(first, replace(second, card_id=uuid4()))),
        "duplicate_card": replace(manifest, cards=(first, replace(second, card_id=first.card_id))),
        "duplicate_item": replace(manifest, cards=(first, replace(second, request_item_id=first.request_item_id))),
        "box": replace(manifest, box_ids=(uuid4(),)),
        "empty": replace(manifest, cards=()),
        "no_parent": replace(manifest, request_id=None),
    }[scenario]
    with pytest.raises(ValueError):
        validate_manifest(invalid, ready_items=ready)


@pytest.mark.parametrize("scenario,expected", [
    ("draft", "draft"), ("transit", "in_transit"),
    ("partial", "partially_received"), ("full", "received"),
    ("early", None), ("no_handover", None), ("empty", None),
])
def test_receipt_progress(scenario, expected):
    now = datetime.now(timezone.utc)
    later = now + timedelta(seconds=1)
    facts = {
        "draft": (None, (None, None)),
        "transit": (now, (None, None)),
        "partial": (now, (later, None)),
        "full": (now, (later, later)),
        "early": (now, (now - timedelta(seconds=1), None)),
        "no_handover": (None, (later, None)),
        "empty": (now, ()),
    }[scenario]
    if expected is None:
        with pytest.raises(ValueError):
            receipt_progress(handed_over_at=facts[0], receipts=facts[1])
    else:
        assert receipt_progress(handed_over_at=facts[0], receipts=facts[1]) == expected
