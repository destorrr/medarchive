import pytest
from httpx import ASGITransport, AsyncClient


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
    # Seed users for all roles
    users = [
        ("branch_user", "branch", 1),
        ("archive_user", "archive", None),
        ("viewer_user", "viewer", None),
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


class TestDashboard:
    async def test_dashboard_accessible_for_branch(self, client):
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "медархив" in r.text.lower()

    async def test_dashboard_accessible_for_archive(self, client):
        await client.post("/login", data={"username": "archive_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "медархив" in r.text.lower()

    async def test_dashboard_accessible_for_viewer(self, client):
        await client.post("/login", data={"username": "viewer_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200

    async def test_dashboard_accessible_for_admin(self, client):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200

    async def test_branch_sees_packing_menu(self, client):
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert "упаковка" in r.text.lower()

    async def test_archive_sees_receive_menu(self, client):
        await client.post("/login", data={"username": "archive_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert "приёмка" in r.text.lower()

    async def test_redirects_to_login_when_unauthenticated(self, client):
        r = await client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 303

    async def test_dashboard_shows_stats(self, client):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "Карт всего" in r.text

    async def test_dashboard_shows_recent_movements(self, client, db):
        # Seed system user for FK
        await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) VALUES (0, 'sys', 'h', 'admin')")
        await db.execute("INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (99, 'Пациент Дашборд', 'placed')")
        await db.conn.commit()
        from medarchive.services.movement_service import MovementService
        ms = MovementService(db)
        await ms.log_movement(99, "placed", None, "shelf Полка 1", user_id=0)

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "Пациент Дашборд" in r.text
