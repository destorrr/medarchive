"""PostgreSQL archive UI route smoke tests."""

import os
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import Location, User
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_postgresql_archive_pages_render_with_archive_role():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(
        Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="postgres-archive-pages-integration-secret",
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
    archive_location = Location(
        id=uuid.uuid4(),
        kind="archive",
        branch_id=None,
        name="Archive UI Integration",
        is_active=True,
    )
    actor = User(
        id=uuid.uuid4(),
        username=f"archive-ui-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash",
        full_name="Archive UI Integration",
        role="archive",
        branch_id=None,
        is_active=True,
    )
    try:
        async with session.begin():
            session.add_all([archive_location, actor])

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("session", token)
            receive = await client.get("/receive")
            client.cookies.set("session", token)
            placement = await client.get("/placement")
            client.cookies.set("session", token)
            requests = await client.get("/requests")

        assert receive.status_code == 200
        assert "Приёмка отправлений" in receive.text
        assert placement.status_code == 200
        assert "Размещение в архиве" in placement.text
        assert requests.status_code == 200
        assert "Заявки центрального офиса" in requests.text
        assert "Archive UI Integration" in receive.text
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()


@pytest.mark.asyncio
async def test_postgresql_archive_individual_card_shelf_placement_is_audited_and_reusable():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from datetime import date

    from medarchive.adapters.postgresql.models import (
        ArchiveShelf, Branch, Card, CardPlacement, DomainEvent,
    )

    app = create_app(
        Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="postgres-archive-card-placement-integration-secret",
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

    branch_id = uuid.uuid4()
    archive_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    shelf_id = uuid.uuid4()
    card_id = uuid.uuid4()
    branch = Branch(
        id=branch_id,
        code=f"AP-{uuid.uuid4().hex[:10]}",
        name="Archive Placement Origin",
        is_active=True,
    )
    archive = Location(
        id=archive_id, kind="archive", branch_id=None,
        name="Archive Card Placement", is_active=True,
    )
    actor = User(
        id=actor_id,
        username=f"archive-place-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash",
        full_name="Archive Placement Operator",
        role="archive",
        branch_id=None,
        is_active=True,
    )
    shelf = ArchiveShelf(
        id=shelf_id, section_name="A-PLACEMENT", shelf_name="Shelf-01",
        qr_code=f"QR-{uuid.uuid4().hex}", is_active=True,
    )
    inactive_shelf_id = uuid.uuid4()
    inactive_shelf = ArchiveShelf(
        id=inactive_shelf_id, section_name="Z-INACTIVE", shelf_name="Shelf-99",
        qr_code=f"QR-{uuid.uuid4().hex}", is_active=False,
    )
    card = Card(
        id=card_id,
        card_number=f"ARCH-PLACE-{uuid.uuid4().hex[:10]}",
        full_name="Archive Placement Test Patient",
        date_of_birth=date(1985, 4, 12),
        origin_branch_id=branch_id,
        current_location_id=archive_id,
        current_shipment_id=None,
        status="active",
        custody_status="available",
    )

    try:
        async with session.begin():
            session.add_all([branch, archive, actor, shelf, inactive_shelf])
            await session.flush()
            session.add(card)

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("session", token)
            page = await client.get("/placement")
            assert page.status_code == 200
            assert card.card_number in page.text
            assert "Отдельных карт на полках пока нет." in page.text

            # Inactive storage locations must be rejected without creating
            # a placement or audit event.
            client.cookies.set("session", token)
            inactive_attempt = await client.post(
                "/placement/card",
                data={"card_id": str(card_id), "shelf_id": str(inactive_shelf_id)},
            )
            assert inactive_attempt.status_code == 200
            assert "неактивна" in inactive_attempt.text
            assert await session.scalar(
                select(CardPlacement.id).where(CardPlacement.card_id == card_id)
            ) is None
            assert await session.scalar(
                select(DomainEvent.id).where(
                    DomainEvent.aggregate_type == "card",
                    DomainEvent.aggregate_id == card_id,
                )
            ) is None

            client.cookies.set("session", token)
            placed = await client.post(
                "/placement/card",
                data={"card_id": str(card_id), "shelf_id": str(shelf_id)},
            )
            assert placed.status_code == 303

            # A second active placement must be rejected and leave the
            # original placement and event history untouched.
            client.cookies.set("session", token)
            duplicate_attempt = await client.post(
                "/placement/card",
                data={"card_id": str(card_id), "shelf_id": str(shelf_id)},
            )
            assert duplicate_attempt.status_code == 200
            assert "Карта уже размещена на архивной полке" in duplicate_attempt.text

            placement_rows = list((await session.scalars(
                select(CardPlacement).where(CardPlacement.card_id == card_id)
            )).all())
            assert len(placement_rows) == 1
            first_placement = placement_rows[0]
            assert first_placement.shelf_id == shelf_id
            assert first_placement.placed_by == actor_id
            assert first_placement.removed_at is None

            events = list((await session.scalars(
                select(DomainEvent).where(
                    DomainEvent.aggregate_type == "card",
                    DomainEvent.aggregate_id == card_id,
                ).order_by(DomainEvent.id)
            )).all())
            assert [event.event_type for event in events] == ["card.placed_in_archive"]

            client.cookies.set("session", token)
            placed_page = await client.get("/placement")
            assert placed_page.status_code == 200
            assert card.card_number in placed_page.text
            assert "A-PLACEMENT / Shelf-01" in placed_page.text
            assert "Отдельные карты на полках" in placed_page.text

            client.cookies.set("session", token)
            removed = await client.post(
                "/placement/remove-card", data={"card_id": str(card_id)}
            )
            assert removed.status_code == 303
            await session.refresh(first_placement)
            assert first_placement.removed_at is not None
            assert first_placement.removed_by == actor_id

            client.cookies.set("session", token)
            available_page = await client.get("/placement")
            assert available_page.status_code == 200
            assert card.card_number in available_page.text
            assert "Отдельные карты без размещения" in available_page.text

            client.cookies.set("session", token)
            replaced = await client.post(
                "/placement/card",
                data={"card_id": str(card_id), "shelf_id": str(shelf_id)},
            )
            assert replaced.status_code == 303

            placement_rows = list((await session.scalars(
                select(CardPlacement).where(CardPlacement.card_id == card_id)
                .order_by(CardPlacement.placed_at, CardPlacement.id)
            )).all())
            assert len(placement_rows) == 2
            assert placement_rows[0].removed_at is not None
            assert placement_rows[0].removed_by == actor_id
            assert placement_rows[1].removed_at is None
            assert placement_rows[1].placed_by == actor_id

            events = list((await session.scalars(
                select(DomainEvent).where(
                    DomainEvent.aggregate_type == "card",
                    DomainEvent.aggregate_id == card_id,
                ).order_by(DomainEvent.id)
            )).all())
            assert [event.event_type for event in events] == [
                "card.placed_in_archive",
                "card.removed_from_archive_shelf",
                "card.placed_in_archive",
            ]

            stored_card = await session.get(Card, card_id)
            assert stored_card is not None
            assert stored_card.current_location_id == archive_id
            assert stored_card.current_shipment_id is None
            assert stored_card.custody_status == "available"
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
