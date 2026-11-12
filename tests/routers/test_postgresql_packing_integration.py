"""End-to-end PostgreSQL branch packing and QR intake tests."""

import json
import os
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from medarchive.adapters.postgresql.models import (
    Box,
    BoxCardMembership,
    BoxCycle,
    Branch,
    Card,
    DomainEvent,
    Location,
    User,
)
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database
from medarchive.main import create_app


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


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
        async with database.session_factory() as session:
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
            async with database.session_factory() as session:
                assert await session.scalar(
                    select(Card.id).where(Card.card_number == qr["card_number"])
                ) is None

            confirmed = await client.post(
                "/packing/confirm-card",
                data={"box_id": str(box_id), "qr_payload": json.dumps(qr, ensure_ascii=False)},
                cookies={"session": token},
            )
            assert confirmed.status_code == 303

            async with database.session_factory() as session:
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

            async with database.session_factory() as session:
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
        async with database.session_factory() as session:
            if actor_id:
                await session.execute(
                    DomainEvent.__table__.delete().where(
                        DomainEvent.actor_user_id == actor_id
                    )
                )
            if box_id is not None:
                await session.execute(
                    BoxCardMembership.__table__.delete().where(
                        BoxCardMembership.box_cycle_id.in_(
                            select(BoxCycle.id).where(BoxCycle.box_id == box_id)
                        )
                    )
                )
                await session.execute(
                    BoxCycle.__table__.delete().where(BoxCycle.box_id == box_id)
                )
                box = await session.get(Box, box_id)
                if box is not None:
                    await session.delete(box)
            if card_id is not None:
                card = await session.get(Card, card_id)
                if card is not None:
                    await session.delete(card)
            actor = await session.get(User, actor_id)
            if actor is not None:
                await session.delete(actor)
            await session.flush()
            await session.execute(
                Location.__table__.delete().where(Location.branch_id == branch_id)
            )
            branch = await session.get(Branch, branch_id)
            if branch is not None:
                await session.delete(branch)
            await session.commit()
        await database.close()
