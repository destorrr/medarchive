# tests/routers/test_auth.py
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(test_config, db):
    """FastAPI TestClient с временной БД."""
    from medarchive.main import create_app
    app = create_app(test_config)

    # Seed a test user
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)
    password_hash = auth.hash_password("test123")
    await db.execute(
        "INSERT INTO branches (id, name, code) VALUES (1, 'Test Branch', 'TB')"
    )
    await db.execute(
        "INSERT INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'test_branch', ?, 'branch', 1)",
        (password_hash,),
    )
    await db.execute(
        "INSERT INTO users (id, username, password_hash, role) "
        "VALUES (2, 'test_admin', ?, 'admin')",
        (password_hash,),
    )
    await db.conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestAuthRouter:
    async def test_login_page_renders(self, client):
        r = await client.get("/login")
        assert r.status_code == 200
        # Login page should contain form elements
        assert "вход" in r.text.lower() or "логин" in r.text.lower()

    async def test_login_success_redirects(self, client):
        r = await client.post("/login", data={"username": "test_branch", "password": "test123"}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"

    async def test_login_wrong_password(self, client):
        r = await client.post("/login", data={"username": "test_branch", "password": "wrong"})
        assert r.status_code == 200  # re-renders login page
        assert "неверный" in r.text.lower() or "ошиб" in r.text.lower()

    async def test_logout_clears_session(self, client):
        # Login first
        await client.post("/login", data={"username": "test_branch", "password": "test123"})
        r = await client.get("/logout", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    async def test_protected_page_redirects_to_login(self, client):
        r = await client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 303
        assert "/login" in r.headers["location"]
