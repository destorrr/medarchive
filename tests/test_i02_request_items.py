"""E2 storage constraints, using only the guarded synthetic PostgreSQL fixture."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.environ.get("MEDARCHIVE_TEST_PG_ENABLED") != "1",
                       reason="Dedicated PostgreSQL fixture required"),
]

INSERT = text("""
    INSERT INTO public.archive_request_items
        (request_id, card_id, status, reservation_active, added_at, added_by)
    SELECT r.id, CAST(:card AS uuid), :status, :reserved, now(), r.requested_by
    FROM public.archive_requests r
    JOIN public.cards c ON c.id=r.card_id
    WHERE c.card_number='I01-SYNTH-002'
    RETURNING id
""")
CARD = "10000000-0000-4000-8000-000000000014"


async def insert(connection, status="pending", reserved=True, card=CARD):
    return (await connection.execute(INSERT, dict(
        status=status, reserved=reserved, card=card))).scalar_one()


async def test_valid_and_historical_items(pg_runtime_transaction):
    c = pg_runtime_transaction
    assert await insert(c)
    assert await insert(c, "removed_by_central_office", False)
    assert await insert(c, "removed_by_central_office", False)
    assert await c.scalar(text("SELECT count(*) FROM public.archive_request_items")) == 3


@pytest.mark.parametrize("status,reserved,card,sqlstate", [
    ("invalid", True, CARD, "23514"),
    ("removed_by_central_office", True, CARD, "23514"),
    ("pending", True, "ffffffff-ffff-4fff-8fff-ffffffffffff", "23503"),
])
async def test_invalid_item(pg_runtime_transaction, status, reserved, card, sqlstate):
    with pytest.raises(DBAPIError) as error:
        async with pg_runtime_transaction.begin_nested():
            await insert(pg_runtime_transaction, status, reserved, card)
    assert error.value.orig.sqlstate == sqlstate


async def test_duplicate_reservation(pg_runtime_transaction):
    c = pg_runtime_transaction
    await insert(c)
    with pytest.raises(DBAPIError) as error:
        async with c.begin_nested():
            await insert(c)
    assert error.value.orig.sqlstate == "23505"


@pytest.mark.parametrize("column", ["extracted_at", "resolved_at"])
async def test_actor_time_pair(pg_runtime_transaction, column):
    c = pg_runtime_transaction
    item = await insert(c)
    with pytest.raises(DBAPIError) as error:
        async with c.begin_nested():
            await c.execute(text(
                f"UPDATE public.archive_request_items SET {column}=now() WHERE id=:id"
            ), {"id": item})
    assert error.value.orig.sqlstate == "23514"
