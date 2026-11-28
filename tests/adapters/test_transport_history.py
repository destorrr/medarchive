"""Installed E4.4 fulfillment link immutability on synthetic rows."""
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from medarchive.adapters.postgresql.models import ArchiveRequest, Card
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow
from tests.adapters.test_postgresql_shipment_workflow import pg_session, seed_locations


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["clear", "replace"])
async def test_fulfillment_link_history(pg_session, change):
    branch, _, archive, central, _, archivist, requester, courier = await seed_locations(pg_session)
    cards = [Card(card_number=uuid4().hex, full_name="Synthetic patient",
                  date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                  current_location_id=archive.id, status="active") for _ in range(2)]
    pg_session.add_all(cards)
    await pg_session.flush()
    parents = [ArchiveRequest(card_id=card.id, requested_by=requester.id, status="new")
               for card in cards]
    pg_session.add_all(parents)
    await pg_session.flush()
    shipment = await PostgreSQLShipmentWorkflow(pg_session).create_draft(
        from_location_id=archive.id, to_location_id=central.id,
        courier_id=courier.id, issued_by=archivist.id,
    )
    c = await pg_session.connection()
    statement = text("UPDATE public.shipments SET request_id=:parent WHERE id=:shipment")
    await c.execute(statement, {"parent": parents[0].id, "shipment": shipment.id})
    await c.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    with pytest.raises(DBAPIError) as error:
        async with c.begin_nested():
            await c.execute(statement, {"parent": None if change == "clear" else parents[1].id,
                                        "shipment": shipment.id})
    assert error.value.orig.sqlstate == "23514"
    assert await c.scalar(text("SELECT request_id FROM public.shipments WHERE id=:shipment"),
                          {"shipment": shipment.id}) == parents[0].id
