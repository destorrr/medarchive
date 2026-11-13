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
    Box,
    BoxCardMembership,
    BoxCycle,
    Branch,
    DomainEvent,
    Card,
    Location,
    User,
)
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
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
            client.cookies.set("session", token)
            created = await client.post("/packing/new-box")
            assert created.status_code == 303
            box_id = uuid.UUID(parse_qs(urlparse(created.headers["location"]).query)["box_id"][0])

            # The printable label is reproducible and contains no patient data.
            client.cookies.set("session", token)
            label = await client.get(f"/boxes/{box_id}/label")
            assert label.status_code == 200
            assert "QR-этикетка" not in label.text
            assert f"MEDARCHIVE:BOX:{box_id}" in label.text
            assert "data:image/png;base64," in label.text

            # A user without an operational role cannot access box labels.
            viewer = User(
                id=uuid.uuid4(),
                username=f"viewer-{uuid.uuid4().hex[:10]}",
                password_hash="test-only-hash",
                full_name="Read Only Viewer",
                role="viewer",
                branch_id=None,
                is_active=True,
            )
            session.add(viewer)
            await session.flush()
            viewer_token = get_auth().create_session_token(viewer)
            client.cookies.set("session", viewer_token)
            forbidden_label = await client.get(f"/boxes/{box_id}/label")
            assert forbidden_label.status_code == 403

            qr = {
                "version": 1,
                "card_number": f"MED-{uuid.uuid4().hex[:12]}",
                "full_name": "Тестовый Пациент",
                "birth_date": "1985-04-23",
                "phone": "+79990000000",
            }
            client.cookies.set("session", token)
            scanned = await client.post(
                "/packing/scan-card",
                data={"box_id": str(box_id), "qr_payload": json.dumps(qr, ensure_ascii=False)},
            )
            assert scanned.status_code == 200
            assert "Новая карта ещё не зарегистрирована" in scanned.text
            assert 'href="/search"' in scanned.text
            assert 'href="/history"' in scanned.text
            assert await session.scalar(
                select(Card.id).where(Card.card_number == qr["card_number"])
            ) is None

            client.cookies.set("session", token)
            confirmed = await client.post(
                "/packing/confirm-card",
                data={"box_id": str(box_id), "qr_payload": json.dumps(qr, ensure_ascii=False)},
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
            client.cookies.set("session", token)
            duplicate = await client.post(
                "/packing/scan-card",
                data={"box_id": str(box_id), "qr_payload": json.dumps(qr, ensure_ascii=False)},
            )
            assert duplicate.status_code == 200
            assert "Карта уже находится в активной коробке" in duplicate.text

            client.cookies.set("session", token)
            removed = await client.post(
                "/packing/remove-card",
                data={"box_id": str(box_id), "card_id": str(card_id)},
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

            # Reuse the same empty box: the next scan must open a new cycle,
            # while the previous membership remains in the audit history.
            client.cookies.set("session", token)
            reused = await client.post(
                "/packing/scan-card",
                data={"box_id": str(box_id), "qr_payload": json.dumps(qr, ensure_ascii=False)},
            )
            assert reused.status_code == 303
            cycles = list((await session.scalars(
                select(BoxCycle).where(BoxCycle.box_id == box_id).order_by(BoxCycle.cycle_number)
            )).all())
            assert [cycle.cycle_number for cycle in cycles] == [1, 2]
            assert cycles[0].ended_at is not None
            assert cycles[1].ended_at is None
            memberships = list((await session.scalars(
                select(BoxCardMembership)
                .where(BoxCardMembership.card_id == card_id)
                .order_by(BoxCardMembership.added_at)
            )).all())
            assert len(memberships) == 2
            assert memberships[0].removed_at is not None
            assert memberships[1].removed_at is None
            assert memberships[0].box_cycle_id == cycles[0].id
            assert memberships[1].box_cycle_id == cycles[1].id

            # Remove the card again; only then is the box eligible for write-off.
            client.cookies.set("session", token)
            removed_again = await client.post(
                "/packing/remove-card",
                data={"box_id": str(box_id), "card_id": str(card_id)},
            )
            assert removed_again.status_code == 303
            written_off = await client.post(
                "/packing/write-off-box", data={"box_id": str(box_id)}
            )
            assert written_off.status_code == 303
            box = await session.get(Box, box_id)
            assert box is not None and box.status == "written_off"
            event = await session.scalar(select(DomainEvent).where(
                DomainEvent.event_type == "box.written_off",
                DomainEvent.aggregate_id == box_id,
            ))
            assert event is not None
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
