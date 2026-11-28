from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import inspect

from medarchive.adapters.postgresql.models import DomainEvent
from medarchive.adapters.postgresql.receipt_correction import build_receipt_correction_event
from medarchive.domain.receipt_correction import ReceiptCorrectionPayload


SHIPMENT = UUID(int=1)
BOX = UUID(int=2)
ACTOR = UUID(int=3)
CORRECTOR = UUID(int=4)


def original_receipt():
    return DomainEvent(
        id=42,
        event_type="test.box_received",
        aggregate_type="shipment",
        aggregate_id=SHIPMENT,
        actor_user_id=ACTOR,
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"version": 1, "shipment_id": str(SHIPMENT), "box_id": str(BOX)},
    )


def build(original, **overrides):
    options = {
        "actor_id": CORRECTOR,
        "receipt_event_type": "test.box_received",
        "correction_event_type": "test.box_receipt_corrected",
        "receipt_payload_version": 1,
        "allowed_reason_codes": frozenset({"test_reason"}),
    }
    options.update(overrides)
    return build_receipt_correction_event(
        ReceiptCorrectionPayload(SHIPMENT, BOX, 42, "test_reason"),
        original,
        **options,
    )


def test_event_mapping_preserves_original_and_creates_transient_event():
    original = original_receipt()
    before = original.payload.copy()
    result = build(original)
    assert inspect(result).transient
    assert result.id is None
    assert result.correction_of_event_id == 42
    assert result.aggregate_type == "shipment"
    assert result.aggregate_id == SHIPMENT
    assert result.actor_user_id == CORRECTOR
    assert result.event_type == "test.box_receipt_corrected"
    assert result.payload == {
        "version": 1, "shipment_id": str(SHIPMENT), "box_id": str(BOX),
        "original_event_id": 42, "reason_code": "test_reason",
    }
    assert original.payload == before
    assert original.actor_user_id == ACTOR
    assert original.correction_of_event_id is None


@pytest.mark.parametrize("field,value", [
    ("correction_of_event_id", 41),
    ("actor_user_id", None),
    ("occurred_at", None),
    ("event_type", "shipment.received"),
    ("aggregate_id", BOX),
    ("id", 43),
])
def test_event_mapping_rejects_invalid_original(field, value):
    original = original_receipt()
    setattr(original, field, value)
    with pytest.raises(ValueError):
        build(original)


@pytest.mark.parametrize("options", [
    {"correction_event_type": ""},
    {"correction_event_type": "test.box_received"},
    {"allowed_reason_codes": frozenset()},
])
def test_event_mapping_rejects_invalid_contract(options):
    with pytest.raises(ValueError):
        build(original_receipt(), **options)


def test_event_mapping_requires_uuid_actor():
    with pytest.raises(TypeError):
        build(original_receipt(), actor_id=None)


@pytest.mark.asyncio
async def test_ordinary_resolver_uses_server_owned_event_vocabulary(monkeypatch):
    from unittest.mock import AsyncMock
    from medarchive.adapters.postgresql import receipt_correction as adapter

    resolver = AsyncMock(return_value=original_receipt())
    monkeypatch.setattr(adapter, "resolve_receipt_correction_event", resolver)
    session = object()
    fact = ReceiptCorrectionPayload(SHIPMENT, BOX, 42, "test_reason")
    catalog = frozenset({"test_reason"})
    await adapter.resolve_ordinary_box_receipt_correction(
        session, fact, actor_id=CORRECTOR, allowed_reason_codes=catalog,
    )
    resolver.assert_awaited_once_with(
        session, fact,
        actor_id=CORRECTOR,
        receipt_event_type="shipment.box_received",
        correction_event_type="shipment.box_receipt_corrected",
        receipt_payload_version=1,
        allowed_reason_codes=catalog,
    )


@pytest.mark.parametrize("scenario", ["valid", "missing_time", "missing_actor", "wrong_actor"])
def test_box_receipt_metadata_contract(scenario):
    from medarchive.adapters.postgresql.receipt_correction import validate_box_receipt_metadata

    original = original_receipt()
    received_at = None if scenario == "missing_time" else original.occurred_at
    received_by = None if scenario == "missing_actor" else ACTOR
    if scenario == "wrong_actor":
        received_by = CORRECTOR
    if scenario == "valid":
        validate_box_receipt_metadata(received_at, received_by, original)
    else:
        expected = "does not match" if scenario == "wrong_actor" else "metadata is absent"
        with pytest.raises(ValueError, match=expected):
            validate_box_receipt_metadata(received_at, received_by, original)
    assert original.actor_user_id == ACTOR
    assert original.correction_of_event_id is None
