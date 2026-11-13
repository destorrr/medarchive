"""Location-scoped PostgreSQL search and history access for branch users."""

import os
import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import Branch, Card, DomainEvent, Location, User
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_postgresql_branch_search_and_history_are_limited_to_own_location():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="postgres-branch-search-history-secret",
    ))
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

    branch_id, other_branch_id = uuid.uuid4(), uuid.uuid4()
    actor = User(
        id=uuid.uuid4(),
        username=f"branch-search-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash",
        full_name="Branch Search Actor",
        role="branch",
        branch_id=branch_id,
        is_active=True,
    )
    branch = Branch(id=branch_id, code=f"BS-{uuid.uuid4().hex[:8]}", name="Scoped Branch")
    other_branch = Branch(
        id=other_branch_id, code=f"BO-{uuid.uuid4().hex[:8]}", name="Other Branch"
    )
    branch_location = Location(
        id=uuid.uuid4(), kind="branch", branch_id=branch_id,
        name="Scoped Branch", is_active=True,
    )
    other_location = Location(
        id=uuid.uuid4(), kind="branch", branch_id=other_branch_id,
        name="Other Branch", is_active=True,
    )
    own_card = Card(
        id=uuid.uuid4(), card_number=f"BS-CARD-{uuid.uuid4().hex[:8]}",
        full_name="Scoped Patient", date_of_birth=date(1980, 1, 2),
        origin_branch_id=branch_id, current_location_id=branch_location.id,
    )
    other_card = Card(
        id=uuid.uuid4(), card_number=f"BO-CARD-{uuid.uuid4().hex[:8]}",
        full_name="Hidden Patient", date_of_birth=date(1981, 2, 3),
        origin_branch_id=other_branch_id, current_location_id=other_location.id,
    )

    try:
        async with session.begin():
            session.add_all([
                branch, other_branch, branch_location, other_location, actor,
                own_card, other_card,
            ])
            await session.flush()
            session.add_all([
                DomainEvent(
                    event_type="card.placed_in_archive",
                    aggregate_type="card", aggregate_id=own_card.id,
                    actor_user_id=actor.id,
                    payload={"from_location_id": str(branch_location.id)},
                ),
                DomainEvent(
                    event_type="card.placed_in_archive",
                    aggregate_type="card", aggregate_id=other_card.id,
                    actor_user_id=actor.id,
                    payload={"from_location_id": str(other_location.id)},
                ),
            ])

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("session", token)
            search_page = await client.get("/search")
            client.cookies.set("session", token)
            search_results = await client.get("/search/results", params={"query": "Patient"})
            client.cookies.set("session", token)
            history = await client.get("/history")
            client.cookies.set("session", token)
            dashboard = await client.get("/dashboard")

        assert search_page.status_code == 200
        assert "Поиск" in search_page.text
        assert search_results.status_code == 200
        assert "Scoped Patient" in search_results.text
        assert "Hidden Patient" not in search_results.text
        assert history.status_code == 200
        assert own_card.card_number in history.text
        assert other_card.card_number not in history.text
        assert "Всего событий: 1" in history.text
        assert dashboard.status_code == 200
        assert "/search" in dashboard.text
        assert "/history" in dashboard.text
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
