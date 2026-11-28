"""Test route-aware pointer evidence using installed SQL and synthetic rows."""
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text

from medarchive.adapters.postgresql.models import Card
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow
from tests.adapters.test_postgresql_shipment_workflow import pg_session, seed_locations


@pytest.mark.asyncio
async def test_box_membership_supplies_transport_evidence(pg_session):
    branch, origin, archive, _, sender, _, _, courier = await seed_locations(pg_session)
    cards = [Card(card_number=uuid4().hex, full_name="Synthetic patient",
                  date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                  current_location_id=origin.id, status="active") for _ in range(2)]
    pg_session.add_all(cards)
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(location_id=origin.id, actor_id=sender.id)
    await workflow.add_card_to_box(box_id=box.id, card_id=cards[0].id, actor_id=sender.id)
    shipment = await workflow.create_draft(
        from_location_id=origin.id, to_location_id=archive.id,
        courier_id=courier.id, issued_by=sender.id,
    )
    await workflow.add_box(shipment_id=shipment.id, box_id=box.id, actor_id=sender.id)
    c = await pg_session.connection()
    async def evidence(kind, obj, received=False):
        return await c.scalar(text("""
            SELECT public.medarchive_target_transport_object(:kind,:obj,:shipment,:received)
        """), {"kind": kind, "obj": obj, "shipment": shipment.id, "received": received})
    assert not await evidence("boxes", box.id)
    await workflow.hand_over(shipment_id=shipment.id, actor_id=sender.id)
    await c.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await evidence("boxes", box.id)
    assert await evidence("cards", cards[0].id)
    assert not await evidence("cards", cards[1].id)
    assert not await evidence("cards", cards[0].id, True)
    assert not await evidence("boxes", box.id, True)
