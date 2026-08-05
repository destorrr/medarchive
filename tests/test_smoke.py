import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def app_client(test_config, db):
    from medarchive.main import create_app
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'admin', ?, 'admin', NULL)",
        (auth.hash_password("admin123"),),
    )
    await db.conn.commit()

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestSmoke:
    async def test_login_to_dashboard_flow(self, app_client):
        """Полный цикл: GET /login → POST login → GET /dashboard → logout."""
        # 1. Login page renders
        r = await app_client.get("/login")
        assert r.status_code == 200
        assert "логин" in r.text.lower() or "вход" in r.text.lower()

        # 2. Submit login
        r = await app_client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"
        assert "session" in r.cookies

        # 3. Follow redirect to dashboard
        r = await app_client.get("/dashboard")
        assert r.status_code == 200
        assert "admin" in r.text.lower()
        assert "администратор" in r.text.lower()

        # 4. Logout
        r = await app_client.get("/logout", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    async def test_protected_routes_block_unauthenticated(self, app_client):
        for path in ["/dashboard"]:
            r = await app_client.get(path, follow_redirects=False)
            assert r.status_code == 303, f"{path} should redirect to login"
            assert "/login" in r.headers["location"], f"{path} should redirect to /login"
