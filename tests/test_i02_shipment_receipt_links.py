"""Additive receipt metadata constraints on isolated synthetic PostgreSQL."""
import os
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.environ.get("MEDARCHIVE_TEST_PG_ENABLED") != "1",
                       reason="Dedicated PostgreSQL fixture required"),
]


@pytest.mark.parametrize("table", ["shipment_cards", "shipment_boxes"])
async def test_receipt_metadata_pair(pg_runtime_transaction, table):
    c = pg_runtime_transaction
    assert await c.scalar(text(f"SELECT count(*) FROM public.{table} WHERE received_at IS NULL AND received_by IS NULL")) == 1
    for assignment, code in [
        ("received_at=now()", "23514"),
        ("received_by='10000000-0000-4000-8000-000000000008'", "23514"),
        ("received_at=now(), received_by='ffffffff-ffff-4fff-8fff-ffffffffffff'", "23503"),
    ]:
        with pytest.raises(DBAPIError) as error:
            async with c.begin_nested():
                await c.execute(text(f"UPDATE public.{table} SET {assignment}"))
        assert error.value.orig.sqlstate == code, str(error.value.orig)


async def test_nullable_legacy_links(pg_runtime_transaction):
    c = pg_runtime_transaction
    assert await c.scalar(text("SELECT count(*) FROM public.shipments WHERE request_id IS NULL")) == 1
    assert await c.scalar(text("SELECT count(*) FROM public.shipment_cards WHERE request_item_id IS NULL")) == 1


@pytest.mark.parametrize("table,column", [
    ("shipments", "request_id"),
    ("shipment_cards", "request_item_id"),
])
async def test_invalid_fulfillment_reference(pg_runtime_transaction, table, column):
    c = pg_runtime_transaction
    with pytest.raises(DBAPIError) as error:
        async with c.begin_nested():
            await c.execute(text(f"UPDATE public.{table} SET {column}='ffffffff-ffff-4fff-8fff-ffffffffffff'"))
    assert error.value.orig.sqlstate == "23503", str(error.value.orig)
