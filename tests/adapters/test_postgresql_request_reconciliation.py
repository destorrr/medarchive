"""E3.2 mapping plan on synthetic PostgreSQL rows, rolled back after tests."""
from datetime import date
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import text

from medarchive.adapters.postgresql.models import ArchiveRequest, Card
from medarchive.adapters.postgresql.request_reconciliation import plan_pending_request_mappings
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow
from tests.adapters.test_postgresql_shipment_workflow import pg_session, seed_locations


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["boxed", "unboxed", "existing_item", "cancelled", "matching_item",
                                      "wrong_actor", "wrong_status", "extra_item", "wrong_time"])
async def test_pending_request_mapping_is_conservative(pg_session, scenario):
    branch, _, archive, _, _, archivist, central_user, _ = await seed_locations(pg_session)
    connection = await pg_session.connection()
    before = await plan_pending_request_mappings(connection)
    card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                current_location_id=archive.id, status="active")
    pg_session.add(card)
    await pg_session.flush()
    if scenario != "unboxed":
        workflow = PostgreSQLShipmentWorkflow(pg_session)
        box = await workflow.create_box(location_id=archive.id, actor_id=archivist.id)
        await workflow.add_card_to_box(box_id=box.id, card_id=card.id, actor_id=archivist.id)
    request = ArchiveRequest(card_id=card.id, requested_by=central_user.id, status="new")
    pg_session.add(request)
    await pg_session.flush()
    await pg_session.refresh(request)
    if scenario == "cancelled":
        request.status = "cancelled"
        await pg_session.flush()
    if scenario in {"existing_item", "matching_item", "wrong_actor", "wrong_status", "extra_item", "wrong_time"}:
        await connection.execute(text("""
            INSERT INTO public.archive_request_items
                (id, request_id, card_id, status, reservation_active, added_at, added_by)
            VALUES (:id, :request, :card, :status, true, :added_at, :actor)
        """), {"id": uuid4() if scenario == "existing_item" else uuid5(request.id, "medarchive:legacy-request-item:v1"),
               "request": request.id, "card": card.id,
               "status": "extracted" if scenario == "wrong_status" else "pending",
               "added_at": request.created_at if scenario != "wrong_time" else request.created_at.replace(year=2000),
               "actor": archivist.id if scenario == "wrong_actor" else central_user.id})
        if scenario == "extra_item":
            await connection.execute(text("""
                INSERT INTO public.archive_request_items
                    (request_id, card_id, status, reservation_active, added_at, added_by)
                VALUES (:request, :card, 'removed_by_central_office', false, :added_at, :actor)
            """), {"request": request.id, "card": card.id,
                   "added_at": request.created_at, "actor": central_user.id})
    await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    after = await plan_pending_request_mappings(connection)
    assert after == await plan_pending_request_mappings(connection)
    conflict = scenario in {"existing_item", "wrong_actor", "wrong_status", "extra_item", "wrong_time"}
    has_item = conflict or scenario == "matching_item"
    assert after.review_count == before.review_count + int(conflict or scenario in {"unboxed", "cancelled"})
    assert after.existing_item_count == before.existing_item_count + int(has_item)
    assert after.matching_item_count == before.matching_item_count + int(scenario == "matching_item")
    assert after.conflicting_item_count == before.conflicting_item_count + int(conflict)
    assert len(after.candidates) == len(before.candidates) + int(scenario == "boxed")
    candidates = [item for item in after.candidates if item.parent_id == request.id]
    if scenario == "boxed":
        item, = candidates
        assert item.card_id == card.id
        assert item.item_id == uuid5(request.id, "medarchive:legacy-request-item:v1")
        assert item.added_by == central_user.id
        assert item.added_at == request.created_at
    else:
        assert not candidates
    assert (await connection.execute(text("""
        SELECT count(*) FROM public.archive_request_items WHERE request_id = :request
    """), {"request": request.id})).scalar_one() == int(has_item) + int(scenario == "extra_item")
