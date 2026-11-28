"""E4.4 deferred reservation integration with installed PostgreSQL guards."""
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from medarchive.adapters.postgresql.models import ArchiveRequest, Card
from tests.adapters.test_postgresql_shipment_workflow import pg_session, seed_locations


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["release", "retain", "wrong_status", "late_insert"])
async def test_request_deferred_guards(pg_session, scenario):
    branch, _, archive, _, _, _, central, _ = await seed_locations(pg_session)
    card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                current_location_id=archive.id, status="active")
    pg_session.add(card)
    await pg_session.flush()
    parent = ArchiveRequest(card_id=card.id, requested_by=central.id, status="new")
    pg_session.add(parent)
    await pg_session.flush()
    c = await pg_session.connection()
    params = {"parent": parent.id, "card": card.id, "actor": central.id}
    insert = text("""
        INSERT INTO public.archive_request_items
            (request_id, card_id, status, reservation_active, added_at, added_by)
        VALUES (:parent, :card, 'pending', true, now(), :actor)
    """)
    if scenario != "late_insert":
        await c.execute(insert, params)
    async def change():
        await c.execute(text("UPDATE public.archive_requests SET status='cancelled' WHERE id=:parent"), params)
        if scenario == "late_insert":
            await c.execute(insert, params)
        elif scenario != "wrong_status":
            await c.execute(text("""
                UPDATE public.archive_request_items SET status='cancelled',
                    reservation_active=:active WHERE request_id=:parent
            """), {**params, "active": scenario == "retain"})
        await c.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    if scenario == "release":
        await change()
        assert await c.scalar(text("""
            SELECT reservation_active FROM public.archive_request_items WHERE request_id=:parent
        """), params) is False
    else:
        with pytest.raises(DBAPIError) as error:
            async with c.begin_nested():
                await change()
        assert error.value.orig.sqlstate == "23514"
        assert await c.scalar(text("SELECT status FROM public.archive_requests WHERE id=:parent"), params) == "new"
