"""Discrepancy storage tests using the isolated PostgreSQL fixture."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.environ.get("MEDARCHIVE_TEST_PG_ENABLED") != "1",
                       reason="Dedicated PostgreSQL fixture required"),
]

SHIPMENT = "10000000-0000-4000-8000-000000000013"
BOX = "10000000-0000-4000-8000-000000000011"
ACTOR = "10000000-0000-4000-8000-000000000008"
MISSING = "ffffffff-ffff-4fff-8fff-ffffffffffff"


async def insert(c, kind="missing_box", shipment=SHIPMENT, actor=ACTOR,
                 expected=None, observed=None):
    return await c.scalar(text("""
        INSERT INTO public.shipment_discrepancies
            (shipment_id, kind, recorded_by, expected_box_id, observed_box_id)
        VALUES (:shipment, :kind, :actor, :expected, :observed)
        RETURNING id
    """), dict(shipment=shipment, kind=kind, actor=actor,
               expected=expected, observed=observed))


@pytest.mark.parametrize("kind,expected,observed", [
    ("missing_box", BOX, None),
    ("extra_box", None, BOX),
    ("extra_box", None, None),
])
async def test_discrepancy_preserves_shipment(pg_runtime_transaction, kind, expected, observed):
    c = pg_runtime_transaction
    params = {"id": SHIPMENT}
    queries = [
        "SELECT * FROM public.shipments WHERE id=:id",
        "SELECT * FROM public.shipment_boxes WHERE shipment_id=:id ORDER BY box_id",
        "SELECT * FROM public.shipment_cards WHERE shipment_id=:id ORDER BY card_id",
    ]
    before = [(await c.execute(text(q), params)).all() for q in queries]
    item = await insert(c, kind=kind, expected=expected, observed=observed)
    assert await c.scalar(text("""
        SELECT count(*) FROM public.shipment_discrepancies
        WHERE id=:id AND recorded_at IS NOT NULL AND kind=:kind
    """), {"id": item, "kind": kind}) == 1
    assert before == [(await c.execute(text(q), params)).all() for q in queries]


@pytest.mark.parametrize("kwargs,code", [
    ({"kind": "invalid"}, "23514"),
    ({"shipment": MISSING}, "23503"),
    ({"actor": MISSING}, "23503"),
    ({"expected": MISSING}, "23503"),
    ({"observed": MISSING}, "23503"),
])
async def test_discrepancy_invalid_input(pg_runtime_transaction, kwargs, code):
    c = pg_runtime_transaction
    with pytest.raises(DBAPIError) as error:
        async with c.begin_nested():
            await insert(c, **kwargs)
    assert error.value.orig.sqlstate == code, str(error.value.orig)
