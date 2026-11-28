"""E4 receipt SQL exercises without replacing any legacy guards."""
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from medarchive.adapters.postgresql.models import Card
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow
from tests.adapters.test_postgresql_shipment_workflow import pg_session, seed_locations


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["draft", "early_receipt", "legacy_manifest", "excluded"])
async def test_target_receipt_sql_ordinary(pg_session, scenario):
    branch, branch_loc, archive, central, branch_user, archivist, _, courier = (
        await seed_locations(pg_session)
    )
    connection = await pg_session.connection()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    shipment = await workflow.create_draft(
        from_location_id=branch_loc.id,
        to_location_id=central.id if scenario == "excluded" else archive.id,
        courier_id=courier.id, issued_by=branch_user.id,
    )
    if scenario != "excluded":
        card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                    date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                    current_location_id=branch_loc.id, status="active")
        pg_session.add(card)
        await pg_session.flush()
        box = await workflow.create_box(location_id=branch_loc.id, actor_id=branch_user.id)
        await workflow.add_card_to_box(box_id=box.id, card_id=card.id, actor_id=branch_user.id)
        await workflow.add_box(shipment_id=shipment.id, box_id=box.id, actor_id=branch_user.id)
        if scenario == "legacy_manifest":
            await connection.execute(text("""
                INSERT INTO public.shipment_cards (shipment_id, card_id, box_id)
                VALUES (:shipment, :card, :box)
            """), {"shipment": shipment.id, "card": card.id, "box": box.id})
        if scenario == "early_receipt":
            await connection.execute(text("""
                UPDATE public.shipment_boxes SET received_at = now(), received_by = :actor
                WHERE shipment_id = :shipment
            """), {"shipment": shipment.id, "actor": archivist.id})
    await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    statement = text("SELECT public.medarchive_validate_target_receipts(:shipment)")
    if scenario == "draft":
        await connection.execute(statement, {"shipment": shipment.id})
    else:
        with pytest.raises(DBAPIError) as error:
            async with connection.begin_nested():
                await connection.execute(statement, {"shipment": shipment.id})
        assert error.value.orig.sqlstate == "23514"
