"""Writeoff storage constraints in the isolated PostgreSQL fixture."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.environ.get("MEDARCHIVE_TEST_PG_ENABLED") != "1",
                       reason="Dedicated PostgreSQL fixture required"),
]

CARD = "10000000-0000-4000-8000-000000000014"
ACTOR = "10000000-0000-4000-8000-000000000007"
MISSING = "ffffffff-ffff-4fff-8fff-ffffffffffff"


async def insert(c, reason="lost", status="submitted", card=CARD, actor=ACTOR):
    return await c.scalar(text("""
        INSERT INTO public.card_writeoff_requests
            (card_id, requested_by, reason_code, status)
        VALUES (:card, :actor, :reason, :status)
        RETURNING id
    """), dict(card=card, actor=actor, reason=reason, status=status))


@pytest.mark.parametrize("reason", ["lost", "damaged", "other"])
async def test_writeoff_valid_reason(pg_runtime_transaction, reason):
    c = pg_runtime_transaction
    before = (await c.execute(text("SELECT * FROM public.cards WHERE id=:id"), {"id": CARD})).one()
    item = await insert(c, reason=reason)
    assert await c.scalar(text("""
        SELECT count(*) FROM public.card_writeoff_requests
        WHERE id=:id AND status='submitted' AND comment IS NULL
          AND document_reference IS NULL AND requested_at IS NOT NULL
    """), {"id": item}) == 1
    after = (await c.execute(text("SELECT * FROM public.cards WHERE id=:id"), {"id": CARD})).one()
    assert before == after


@pytest.mark.parametrize("kwargs,code", [
    ({"reason": "invalid"}, "23514"),
    ({"status": "invalid"}, "23514"),
    ({"card": MISSING}, "23503"),
    ({"actor": MISSING}, "23503"),
])
async def test_writeoff_invalid_input(pg_runtime_transaction, kwargs, code):
    c = pg_runtime_transaction
    with pytest.raises(DBAPIError) as error:
        async with c.begin_nested():
            await insert(c, **kwargs)
    assert error.value.orig.sqlstate == code, str(error.value.orig)


@pytest.mark.parametrize("assignment", [
    "processing_at=now()",
    "processing_by='10000000-0000-4000-8000-000000000008'",
    "resolved_at=now()",
    "resolved_by='10000000-0000-4000-8000-000000000008'",
])
async def test_writeoff_actor_time_pair(pg_runtime_transaction, assignment):
    c = pg_runtime_transaction
    item = await insert(c)
    with pytest.raises(DBAPIError) as error:
        async with c.begin_nested():
            await c.execute(text(
                f"UPDATE public.card_writeoff_requests SET {assignment} WHERE id=:id"
            ), {"id": item})
    assert error.value.orig.sqlstate == "23514", str(error.value.orig)
