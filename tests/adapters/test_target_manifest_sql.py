"""Execute candidate E4 SQL on synthetic rollback-only PostgreSQL fixtures."""
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from medarchive.adapters.postgresql.models import ArchiveRequest, Card
from medarchive.adapters.postgresql.target_manifest_sql import TARGET_MANIFEST_SQL
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow
from tests.adapters.test_postgresql_shipment_workflow import pg_session, seed_locations


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["ordinary", "empty", "excluded", "p09",
                                      "subset", "pending", "wrong_card", "missing_item"])
async def test_target_manifest_sql(pg_session, scenario):
    branch, branch_loc, archive, central, branch_user, archivist, central_user, courier = (
        await seed_locations(pg_session)
    )
    connection = await pg_session.connection()
    # Transactional candidate DDL only; existing guard functions are untouched.
    await connection.execute(text(TARGET_MANIFEST_SQL))
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    if scenario in {"ordinary", "empty", "excluded"}:
        shipment = await workflow.create_draft(
            from_location_id=branch_loc.id,
            to_location_id=central.id if scenario == "excluded" else archive.id,
            courier_id=courier.id, issued_by=branch_user.id,
        )
        if scenario == "ordinary":
            card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                        date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                        current_location_id=branch_loc.id, status="active")
            pg_session.add(card)
            await pg_session.flush()
            box = await workflow.create_box(location_id=branch_loc.id, actor_id=branch_user.id)
            await workflow.add_card_to_box(box_id=box.id, card_id=card.id, actor_id=branch_user.id)
            await workflow.add_box(shipment_id=shipment.id, box_id=box.id, actor_id=branch_user.id)
    else:
        cards = [Card(card_number=uuid4().hex, full_name="Synthetic patient",
                      date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                      current_location_id=archive.id, status="active") for _ in range(2)]
        pg_session.add_all(cards)
        await pg_session.flush()
        request = ArchiveRequest(card_id=cards[0].id, requested_by=central_user.id, status="new")
        pg_session.add(request)
        await pg_session.flush()
        shipment = await workflow.create_draft(
            from_location_id=archive.id, to_location_id=central.id,
            courier_id=courier.id, issued_by=archivist.id,
        )
        await connection.execute(text("""
            UPDATE public.shipments SET request_id = :parent WHERE id = :shipment
        """), {"parent": request.id, "shipment": shipment.id})
        for index, card in enumerate(cards):
            item_id = uuid4()
            await connection.execute(text("""
                INSERT INTO public.archive_request_items
                    (id, request_id, card_id, status, reservation_active, added_at, added_by)
                VALUES (:id, :parent, :card, :status, true, now(), :actor)
            """), {"id": item_id, "parent": request.id, "card": card.id,
                   "status": "pending" if scenario == "pending" and index == 1 else "extracted",
                   "actor": central_user.id})
            if scenario in {"subset", "pending"} and index == 1:
                continue
            await connection.execute(text("""
                INSERT INTO public.shipment_cards (shipment_id, card_id, request_item_id)
                VALUES (:shipment, :card, :item)
            """), {"shipment": shipment.id,
                   "card": cards[1-index].id if scenario == "wrong_card" else card.id,
                   "item": None if scenario == "missing_item" and index == 0 else item_id})
    await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    statement = text("SELECT public.medarchive_validate_target_manifest(:shipment)")
    if scenario in {"ordinary", "p09"}:
        await connection.execute(statement, {"shipment": shipment.id})
    else:
        with pytest.raises(DBAPIError) as error:
            async with connection.begin_nested():
                await connection.execute(statement, {"shipment": shipment.id})
        assert error.value.orig.sqlstate == "23514"
