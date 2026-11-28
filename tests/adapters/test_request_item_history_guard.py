"""E4.4 actual installed history guard on rollback-only PostgreSQL rows."""
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from medarchive.adapters.postgresql.models import ArchiveRequest, Card
from tests.adapters.test_postgresql_shipment_workflow import pg_session, seed_locations


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["transition", "delete", "identity", "actor", "time", "tombstone"])
async def test_request_item_history(pg_session, scenario):
    branch, _, archive, _, _, archivist, central, _ = await seed_locations(pg_session)
    card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                current_location_id=archive.id, status="active")
    pg_session.add(card)
    await pg_session.flush()
    parent = ArchiveRequest(card_id=card.id, requested_by=central.id, status="new")
    pg_session.add(parent)
    await pg_session.flush()
    connection = await pg_session.connection()
    item = uuid4()
    await connection.execute(text("""
        INSERT INTO public.archive_request_items
            (id, request_id, card_id, status, reservation_active, added_at, added_by)
        VALUES (:id, :parent, :card, :status, :active, now(), :actor)
    """), {"id": item, "parent": parent.id, "card": card.id, "actor": central.id,
           "status": "removed_by_central_office" if scenario == "tombstone" else "pending",
           "active": scenario != "tombstone"})
    commands = {
        "transition": "UPDATE public.archive_request_items SET status = 'extracted' WHERE id = :id",
        "delete": "DELETE FROM public.archive_request_items WHERE id = :id",
        "identity": "UPDATE public.archive_request_items SET id = :replacement WHERE id = :id",
        "actor": "UPDATE public.archive_request_items SET added_by = :replacement WHERE id = :id",
        "time": "UPDATE public.archive_request_items SET added_at = added_at - interval '1 second' WHERE id = :id",
        "tombstone": "UPDATE public.archive_request_items SET status = 'pending', reservation_active = true WHERE id = :id",
    }
    params = {"id": item, "replacement": archivist.id if scenario == "actor" else uuid4()}
    if scenario == "transition":
        await connection.execute(text(commands[scenario]), params)
    else:
        with pytest.raises(DBAPIError) as error:
            async with connection.begin_nested():
                await connection.execute(text(commands[scenario]), params)
        assert error.value.orig.sqlstate == "23514"
    row = (await connection.execute(text("""
        SELECT id, added_by, status FROM public.archive_request_items WHERE id = :id
    """), {"id": item})).one()
    assert row.id == item and row.added_by == central.id
    assert row.status == ("extracted" if scenario == "transition" else
                          "removed_by_central_office" if scenario == "tombstone" else "pending")
