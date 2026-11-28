"""Installed P09 deferred integration: legacy-compatible terminal gate."""
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from medarchive.adapters.postgresql.models import ArchiveRequest, Card
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow
from tests.adapters.test_postgresql_shipment_workflow import pg_session, seed_locations


@pytest.mark.asyncio
async def test_p09_rejects_draft_for_cancelled_parent(pg_session):
    branch, _, archive, central, _, archivist, requester, courier = await seed_locations(pg_session)
    card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                current_location_id=archive.id, status="active")
    pg_session.add(card)
    await pg_session.flush()
    parent = ArchiveRequest(card_id=card.id, requested_by=requester.id, status="new")
    pg_session.add(parent)
    await pg_session.flush()
    c = await pg_session.connection()
    params = {"parent": parent.id, "card": card.id, "actor": requester.id}
    await c.execute(text("""
        INSERT INTO public.archive_request_items
            (request_id, card_id, status, reservation_active, added_at, added_by)
        VALUES (:parent, :card, 'pending', true, now(), :actor)
    """), params)
    shipment = await PostgreSQLShipmentWorkflow(pg_session).create_draft(
        from_location_id=archive.id, to_location_id=central.id,
        courier_id=courier.id, issued_by=archivist.id,
    )
    await c.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await c.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    with pytest.raises(DBAPIError) as error:
        async with c.begin_nested():
            await c.execute(text("UPDATE public.archive_requests SET status='cancelled' WHERE id=:parent"), params)
            await c.execute(text("""
                UPDATE public.archive_request_items SET status='cancelled',
                    reservation_active=false WHERE request_id=:parent
            """), params)
            await c.execute(text("UPDATE public.shipments SET request_id=:parent WHERE id=:shipment"),
                            {**params, "shipment": shipment.id})
            await c.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert error.value.orig.sqlstate == "23514"
    assert "P09 draft requires processing parent" in str(error.value.orig)
    assert await c.scalar(text("SELECT status FROM public.archive_requests WHERE id=:parent"), params) == "new"
