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


@pytest.mark.parametrize("table,column", [
    ("shipments", "request_id"),
    ("shipment_cards", "request_item_id"),
])
async def test_fulfillment_unique_constraint(pg_runtime_transaction, table, column):
    c = pg_runtime_transaction
    assert await c.scalar(text("""
        SELECT count(*) FROM pg_constraint con
        JOIN pg_attribute a ON a.attrelid=con.conrelid
        WHERE con.conrelid=to_regclass(:relation)
          AND con.contype='u' AND con.convalidated
          AND con.conkey=ARRAY[a.attnum] AND a.attname=:column
    """), {"relation": "public." + table, "column": column}) == 1


async def test_duplicate_request_link(pg_runtime_transaction):
    c = pg_runtime_transaction
    await c.execute(text("""
        UPDATE public.shipments SET request_id=(SELECT id FROM public.archive_requests)
        WHERE id='10000000-0000-4000-8000-000000000013'
    """))
    with pytest.raises(DBAPIError) as error:
        async with c.begin_nested():
            await c.execute(text("""
                INSERT INTO public.shipments(from_location_id,to_location_id,courier_id,issued_by,request_id)
                SELECT from_location_id,to_location_id,courier_id,issued_by,request_id
                FROM public.shipments WHERE id='10000000-0000-4000-8000-000000000013'
            """))
    assert error.value.orig.sqlstate == "23505", str(error.value.orig)
    assert "uq_shipments_request_id" in str(error.value.orig)


async def test_duplicate_request_item_link(pg_runtime_transaction):
    c = pg_runtime_transaction
    item = await c.scalar(text("""
        INSERT INTO public.archive_request_items(request_id,card_id,status,reservation_active,added_at,added_by)
        SELECT id,card_id,'pending',true,now(),requested_by FROM public.archive_requests
        RETURNING id
    """))
    await c.execute(text("UPDATE public.shipment_cards SET request_item_id=:item"), {"item":item})
    shipment = await c.scalar(text("""
        INSERT INTO public.shipments(from_location_id,to_location_id,courier_id,issued_by)
        SELECT from_location_id,to_location_id,courier_id,issued_by
        FROM public.shipments WHERE id='10000000-0000-4000-8000-000000000013'
        RETURNING id
    """))
    await c.execute(text("""
        INSERT INTO public.shipment_boxes(shipment_id,box_id)
        SELECT :shipment,box_id FROM public.shipment_boxes
        WHERE shipment_id='10000000-0000-4000-8000-000000000013'
    """), {"shipment":shipment})
    with pytest.raises(DBAPIError) as error:
        async with c.begin_nested():
            await c.execute(text("""
                INSERT INTO public.shipment_cards(shipment_id,card_id,box_id,request_item_id)
                SELECT :shipment,card_id,box_id,request_item_id FROM public.shipment_cards
                WHERE shipment_id='10000000-0000-4000-8000-000000000013'
            """), {"shipment":shipment})
    assert error.value.orig.sqlstate == "23505", str(error.value.orig)
    assert "uq_shipment_cards_request_item" in str(error.value.orig)
