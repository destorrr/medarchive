"""HTTP integration coverage for Central Office external card custody."""

import os
import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    Box, BoxCardMembership, Branch, Card, Courier, DomainEvent, Location, Shipment, User,
)
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.parametrize(
    "custody_status",
    ["at_court", "at_insurance", "at_external_organization"],
)
@pytest.mark.asyncio
async def test_central_external_custody_round_trip_preserves_balance_and_history(custody_status):
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(
        Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="postgres-central-custody-integration-secret",
        )
    )
    database = await get_pg_database()
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    async def session_override():
        yield session

    app.dependency_overrides[get_pg_session] = session_override

    branch = Branch(
        id=uuid.uuid4(),
        code=f"CU-{uuid.uuid4().hex[:10]}",
        name="Custody Origin Branch",
        is_active=True,
    )
    central = Location(
        id=uuid.uuid4(),
        kind="central_office",
        branch_id=None,
        name="Custody Central Office",
        is_active=True,
    )
    actor = User(
        id=uuid.uuid4(),
        username=f"central-custody-{uuid.uuid4().hex[:10]}",
        password_hash="test-only-hash",
        full_name="Central Custody Operator",
        role="central_office",
        branch_id=None,
        is_active=True,
    )
    archive_actor_id = uuid.uuid4()
    archive_actor = User(
        id=archive_actor_id,
        username=f"archive-custody-{uuid.uuid4().hex[:10]}",
        password_hash="test-only-hash",
        full_name="Archive Custody Operator",
        role="archive",
        branch_id=None,
        is_active=True,
    )
    archive = Location(
        id=uuid.uuid4(), kind="archive", branch_id=None,
        name="Custody Archive", is_active=True,
    )
    courier = Courier(
        id=uuid.uuid4(), full_name="Custody Workflow Courier", is_active=True,
    )
    central_id = central.id
    archive_id = archive.id
    courier_id = courier.id
    empty_box_id = uuid.uuid4()
    empty_box = Box(
        id=empty_box_id, box_number=f"BX-CU-{uuid.uuid4().hex[:10].upper()}",
        current_location_id=central_id, status="active", created_by=actor.id,
    )
    card_id = uuid.uuid4()
    card = Card(
        id=card_id,
        card_number=f"CU-{uuid.uuid4().hex[:10]}",
        full_name="Custody Test Patient",
        date_of_birth=date(1982, 3, 4),
        origin_branch_id=branch.id,
        current_location_id=central.id,
        current_shipment_id=None,
        status="active",
        custody_status="available",
    )

    try:
        async with session.begin():
            # Persist locations before the box that references the Central
            # Office location; SQLAlchemy has no ORM relationship to order these.
            session.add_all([branch, central, archive, actor, archive_actor, courier])
            await session.flush()
            session.add_all([empty_box, card])

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("session", token)
            initial_page = await client.get("/central/custody")
            assert initial_page.status_code == 200
            assert card.card_number in initial_page.text
            assert "Custody Test Patient" in initial_page.text
            assert "Отметить передачу" in initial_page.text

            marked = await client.post(
                "/central/custody/mark",
                data={
                    "card_id": str(card_id),
                    "custody_status": custody_status,
                    "organization": "Test External Organization",
                    "comment": "Передано по акту № 17",
                },
            )
            assert marked.status_code == 303

            await session.refresh(card)
            assert card.current_location_id == central_id
            assert card.current_shipment_id is None
            assert card.custody_status == custody_status
            assert card.external_organization == "Test External Organization"
            assert card.external_comment == "Передано по акту № 17"
            assert card.external_since is not None

            client.cookies.set("session", token)
            external_page = await client.get("/central/custody")
            assert external_page.status_code == 200
            assert card.card_number in external_page.text
            assert "Test External Organization" in external_page.text
            assert "Вернуть на баланс ЦО" in external_page.text
            assert "Отметить передачу" not in external_page.text

            # External custody remains on the CO balance but must be excluded
            # from packing and internal dispatch until the physical return.
            client.cookies.set("session", token)
            packing_page = await client.get("/central/packing")
            transfer_page = await client.get("/central/transfer")
            assert packing_page.status_code == 200
            assert transfer_page.status_code == 200
            assert card.card_number not in packing_page.text
            assert card.card_number not in transfer_page.text

            client.cookies.set("session", token)
            add_to_box = await client.post(
                "/central/packing/add-card",
                data={"box_id": str(empty_box_id), "card_id": str(card_id)},
            )
            assert add_to_box.status_code == 200
            assert "Карта не найдена в месте хранения коробки или недоступна" in add_to_box.text

            client.cookies.set("session", token)
            create_box_with_external_card = await client.post(
                "/central/packing/new-box",
                data={"card_ids": str(card_id)},
            )
            assert create_box_with_external_card.status_code == 200
            assert "Карта передана во внешнюю организацию и недоступна для внутреннего перемещения" in create_box_with_external_card.text

            client.cookies.set("session", token)
            dispatch_external_card = await client.post(
                "/central/transfer/confirm",
                data={
                    "card_ids": str(card_id),
                    "to_location_id": str(archive_id),
                    "courier_id": str(courier_id),
                },
            )
            assert dispatch_external_card.status_code == 200
            assert "Карта передана во внешнюю организацию и недоступна для внутреннего перемещения" in dispatch_external_card.text

            await session.refresh(card)
            assert card.current_location_id == central_id
            assert card.current_shipment_id is None
            assert card.custody_status == custody_status
            assert await session.scalar(
                select(BoxCardMembership.id).where(
                    BoxCardMembership.card_id == card_id,
                    BoxCardMembership.removed_at.is_(None),
                )
            ) is None
            assert await session.scalar(select(Shipment.id)) is None

            # Archive staff must not be able to manage Central Office custody.
            archive_token = get_auth().create_session_token(type("TokenUser", (), {"id": archive_actor_id})())
            client.cookies.set("session", archive_token)
            forbidden_page = await client.get("/central/custody")
            forbidden_mark = await client.post(
                "/central/custody/mark",
                data={
                    "card_id": str(card_id),
                    "custody_status": "at_court",
                    "organization": "Unauthorized",
                },
            )
            forbidden_return = await client.post(
                "/central/custody/return", data={"card_id": str(card_id)}
            )
            assert forbidden_page.status_code == 403
            assert forbidden_mark.status_code == 403
            assert forbidden_return.status_code == 403

            client.cookies.set("session", token)
            returned = await client.post(
                "/central/custody/return",
                data={
                    "card_id": str(card_id),
                    "comment": "Возвращена по акту № 18",
                },
            )
            assert returned.status_code == 303

        await session.refresh(card)
        assert card.current_location_id == central_id
        assert card.current_shipment_id is None
        assert card.custody_status == "available"
        assert card.external_organization is None
        assert card.external_since is None
        assert card.external_comment == "Возвращена по акту № 18"

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("session", token)
            returned_page = await client.get("/central/custody")
            assert returned_page.status_code == 200
            assert card.card_number in returned_page.text
            assert "Отметить передачу" in returned_page.text
            assert "Вернуть на баланс ЦО" not in returned_page.text
            assert "Test External Organization" not in returned_page.text

        events = list((
            await session.scalars(
                select(DomainEvent)
                .where(
                    DomainEvent.aggregate_type == "card",
                    DomainEvent.aggregate_id == card_id,
                )
                .order_by(DomainEvent.id)
            )
        ).all())
        assert [event.event_type for event in events] == [
            "card.external_custody_started",
            "card.external_custody_returned",
        ]
        assert events[0].payload["custody_status"] == custody_status
        assert events[0].payload["organization"] == "Test External Organization"
        assert events[1].payload["previous_external_custody"]["custody_status"] == custody_status
        assert events[1].payload["previous_external_custody"]["organization"] == "Test External Organization"
        assert events[1].payload["return_comment"] == "Возвращена по акту № 18"
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
