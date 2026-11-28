from uuid import UUID

import pytest

from medarchive.domain.receipt_correction import ReceiptCorrectionPayload


SHIPMENT = UUID("10000000-0000-4000-8000-000000000013")
BOX = UUID("10000000-0000-4000-8000-000000000011")
# Synthetic catalog for unit testing only; not a product reason catalog.
CATALOG = frozenset({"test_reason"})


def test_receipt_correction_payload_serializes_only_fact_fields():
    fact = ReceiptCorrectionPayload(SHIPMENT, BOX, 42, "test_reason")
    assert fact.to_event_payload(allowed_reason_codes=CATALOG) == {
        "version": 1,
        "shipment_id": str(SHIPMENT),
        "box_id": str(BOX),
        "original_event_id": 42,
        "reason_code": "test_reason",
    }


@pytest.mark.parametrize("event_id", [0, -1, True, "42", None])
def test_receipt_correction_rejects_invalid_event_id(event_id):
    with pytest.raises(ValueError):
        ReceiptCorrectionPayload(SHIPMENT, BOX, event_id, "test_reason")


@pytest.mark.parametrize("reason", ["", " ", " test_reason", "test_reason ", None])
def test_receipt_correction_rejects_missing_or_malformed_reason(reason):
    with pytest.raises(ValueError):
        ReceiptCorrectionPayload(SHIPMENT, BOX, 42, reason)


@pytest.mark.parametrize("shipment,box", [(str(SHIPMENT), BOX), (SHIPMENT, None)])
def test_receipt_correction_requires_uuid_identifiers(shipment, box):
    with pytest.raises(TypeError):
        ReceiptCorrectionPayload(shipment, box, 42, "test_reason")


@pytest.mark.parametrize("catalog", [frozenset(), frozenset({"other_test_reason"})])
def test_receipt_correction_rejects_reason_outside_catalog(catalog):
    fact = ReceiptCorrectionPayload(SHIPMENT, BOX, 42, "test_reason")
    with pytest.raises(ValueError):
        fact.to_event_payload(allowed_reason_codes=catalog)
