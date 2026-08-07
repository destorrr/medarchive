import pytest
from httpx import ASGITransport, AsyncClient
from medarchive.adapters.sqlite.repositories import (
    CardRepositoryImpl, BoxRepositoryImpl
)
from medarchive.domain.statuses import BoxStatus, CardStatus


@pytest.fixture
async def client(test_config, db):
    from medarchive.main import create_app
    app = create_app(test_config)

    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

    # Seed branches
    await db.execute(
        "INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'Test Branch', 'TB')"
    )
    # Seed users
    users = [
        ("branch_user", "branch", 1),
        ("admin_user", "admin", None),
    ]
    for username, role, branch_id in users:
        password_hash = auth.hash_password("test123")
        await db.execute(
            "INSERT OR REPLACE INTO users (username, password_hash, role, branch_id) "
            "VALUES (?, ?, ?, ?)",
            (username, password_hash, role, branch_id),
        )
    await db.conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_lost_box(db):
    """Потерянная коробка с одной картой."""
    await db.execute(
        "INSERT OR REPLACE INTO couriers (id, name, phone) VALUES (1, 'Курьер', '+79990000000')"
    )
    await db.conn.commit()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    card = await card_repo.create("Инцидент Пациент")
    await card_repo.update_status(card.id, CardStatus.in_box)
    box = await box_repo.create("B-LOST-77", origin_branch_id=1, created_by=1)
    await box_repo.add_card(box.id, card.id)
    await box_repo.update_status(box.id, BoxStatus.lost)
    return box, card


class TestAdminIncidents:
    async def test_incidents_page_renders(self, client):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/admin/incidents")
        assert r.status_code == 200
        assert "Инциденты" in r.text

    async def test_resolve_lost_box(self, client, db):
        box, card = await _seed_lost_box(db)
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            "/admin/incidents/resolve-lost-box",
            data={"box_id": str(box.id), "action": "return_cards"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        card_repo = CardRepositoryImpl(db)
        updated = await card_repo.get_by_id(card.id)
        assert updated.status == CardStatus.in_branch

    async def test_incidents_forbidden_for_non_admin(self, client):
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/admin/incidents")
        assert r.status_code == 403
