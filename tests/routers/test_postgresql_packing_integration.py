"""End-to-end PostgreSQL branch packing and QR intake tests."""

import json
import os
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    BoxCardMembership,
    Branch,
    Card,
    Location,
    User,
)
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")

async def _yield_session(session):
    yield session


@pytest.mark.asyncio
async def test_postgresql_branch_packing_registers_qr_and_preserves_membership_history():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(
        Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="postgres-packing-integration-secret",
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
    app.dependency_overrides[get_pg_session] = lambda: _yield_session(session)
    branch_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    branch = Branch(
        id=branch_id,
        code=f"QR-{uuid.uuid4().hex[:10]}",
        name="QR Integration Branch",
        is_active=True,
    )
    location = Location(
        id=uuid.uuid4(),
        kind="branch",
        branch_id=branch_id,
        name=branch.name,
        is_active=True,
    )
    actor = User(
        id=actor_id,
        username=f"packing-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash",
        full_name="Packing Integration",
        role="branch",
        branch_id=branch_id,
        is_active=True,
    )
    box_id = None
    card_id = None
    try:
        async with session.begin():
            session.add_all([branch, location, actor])

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/packing/new-box", cookies={"session": token})
            assert created.status_code == 303
            box_id = uuid.UUID(parse_qs(urlparse(created.headers["location"]).query)["box_id"][0])

            qr = {
                "version": 1,
                "card_number": f"MED-{uuid.uuid4().hex[:12]}",
                "full_name": "Тестовый Пациент",
                "birth_date": "1985-04-23",
                "phone": "+79990000000",
            }
            scanned = await client.post(
                "/packing/scan-card",
                data={"box_id": str(box_id), "qr_payload": json.dumps(qr, ensure_ascii=False)},
                cookies={"session": token},
            )
            assert scanned.status_code == 200
            assert "Новая карта ещё не зарегистрирована" in scanned.text
            assert await session.scalar(
                select(Card.id).where(Card.card_number == qr["card_number"])
            ) is None

            confirmed = await client.post(
                "/packing/confirm-card",
                data={"box_id": str(box_id), "qr_payload": json.dumps(qr, ensure_ascii=False)},
                cookies={"session": token},
            )
            assert confirmed.status_code == 303

            card = await session.scalar(
                select(Card).where(Card.card_number == qr["card_number"])
            )
            assert card is not None
            card_id = card.id
            assert card.full_name == qr["full_name"]
            assert str(card.date_of_birth) == qr["birth_date"]
            assert card.origin_branch_id == branch_id
            assert card.current_location_id == location.id
            membership = await session.scalar(
                select(BoxCardMembership).where(
                    BoxCardMembership.card_id == card.id,
                    BoxCardMembership.removed_at.is_(None),
                )
            )
            assert membership is not None

            # Re-scanning the same card into the same box is rejected, not duplicated.
            duplicate = await client.post(
                "/packing/scan-card",
                data={"box_id": str(box_id), "qr_payload": json.dumps(qr, ensure_ascii=False)},
                cookies={"session": token},
            )
            assert duplicate.status_code == 200
            assert "Карта уже находится в активной коробке" in duplicate.text

            removed = await client.post(
                "/packing/remove-card",
                data={"box_id": str(box_id), "card_id": str(card_id)},
                cookies={"session": token},
            )
            assert removed.status_code == 303

            membership = await session.scalar(
                select(BoxCardMembership).where(
                    BoxCardMembership.card_id == card_id
                )
            )
            assert membership is not None
            assert membership.removed_at is not None
            card = await session.get(Card, card_id)
            assert card is not None
            assert card.current_location_id == location.id
            assert card.origin_branch_id == branch_id
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
