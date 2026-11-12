"""Central-office archive request creation integration test."""

import os
import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    ArchiveRequest,
    Branch,
    Card,
    Location,
    User,
)
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_postgresql_central_office_creates_archive_card_request():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(
        Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="postgres-central-request-integration-secret",
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
        code=f"RQ-{uuid.uuid4().hex[:10]}",
        name="Request Origin Branch",
        is_active=True,
    )
    archive = Location(
        id=uuid.uuid4(), kind="archive", branch_id=None,
        name="Request Archive", is_active=True,
    )
    central_location = Location(
        id=uuid.uuid4(), kind="central_office", branch_id=None,
        name="Central UI", is_active=True,
    )
    actor = User(
        id=uuid.uuid4(),
        username=f"central-request-{uuid.uuid4().hex[:10]}",
        password_hash="test-only-hash",
        full_name="Central Request Operator",
        role="central_office",
        branch_id=None,
        is_active=True,
    )
    card = Card(
        id=uuid.uuid4(),
        card_number=f"REQ-{uuid.uuid4().hex[:10]}",
        full_name="Request Test Patient",
        date_of_birth=date(1979, 8, 16),
        origin_branch_id=branch.id,
        current_location_id=archive.id,
        current_shipment_id=None,
        status="active",
        custody_status="available",
    )
    try:
        async with session.begin():
            session.add_all([branch, archive, central_location, actor, card])

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            search = await client.get(
                "/requests/new",
                params={"query": card.card_number},
                cookies={"session": token},
            )
            assert search.status_code == 200
            assert card.card_number in search.text

            created = await client.post(
                "/requests/create",
                data={"card_ids": str(card.id)},
                cookies={"session": token},
            )
            assert created.status_code == 303

            central_receive = await client.get("/central/receive", cookies={"session": token})
            central_packing = await client.get("/central/packing", cookies={"session": token})
            central_transfer = await client.get("/central/transfer", cookies={"session": token})
            central_custody = await client.get("/central/custody", cookies={"session": token})
            assert central_receive.status_code == 200
            assert "Приёмка отправлений в ЦО" in central_receive.text
            assert central_packing.status_code == 200
            assert "Упаковка в центральном офисе" in central_packing.text
            assert central_transfer.status_code == 200
            assert "Отправка из центрального офиса" in central_transfer.text
            assert central_custody.status_code == 200
            assert "Внешнее хранение карт" in central_custody.text

        request_row = await session.scalar(
            select(ArchiveRequest).where(ArchiveRequest.card_id == card.id)
        )
        assert request_row is not None
        assert request_row.status == "new"
        assert request_row.requested_by == actor.id
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
