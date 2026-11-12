"""HTTP integration coverage for Central Office external card custody."""

import os
import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import Branch, Card, DomainEvent, Location, User
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
    card = Card(
        id=uuid.uuid4(),
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
            session.add_all([branch, central, actor])
            await session.flush()
            session.add(card)

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            marked = await client.post(
                "/central/custody/mark",
                data={
                    "card_id": str(card.id),
                    "custody_status": custody_status,
                    "organization": "Test External Organization",
                    "comment": "Передано по акту № 17",
                },
                cookies={"session": token},
            )
            assert marked.status_code == 303

            await session.refresh(card)
            assert card.current_location_id == central.id
            assert card.current_shipment_id is None
            assert card.custody_status == custody_status
            assert card.external_organization == "Test External Organization"
            assert card.external_comment == "Передано по акту № 17"
            assert card.external_since is not None

            returned = await client.post(
                "/central/custody/return",
                data={
                    "card_id": str(card.id),
                    "comment": "Возвращена по акту № 18",
                },
                cookies={"session": token},
            )
            assert returned.status_code == 303

        await session.refresh(card)
        assert card.current_location_id == central.id
        assert card.current_shipment_id is None
        assert card.custody_status == "available"
        assert card.external_organization is None
        assert card.external_since is None
        assert card.external_comment == "Возвращена по акту № 18"

        events = list((
            await session.scalars(
                select(DomainEvent)
                .where(
                    DomainEvent.aggregate_type == "card",
                    DomainEvent.aggregate_id == card.id,
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
