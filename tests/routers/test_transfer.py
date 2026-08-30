import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(test_config, db):
    from medarchive.main import create_app
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'branch1', ?, 'branch', 1)",
        (auth.hash_password("test123"),),
    )
    await db.execute(
        "INSERT OR REPLACE INTO couriers (id, name, phone) VALUES (1, 'Курьер 1', '+79990000001')"
    )
    # Create a ready box for transfer
    await db.execute(
        "INSERT INTO boxes (id, box_number, origin_branch_id, created_by, status) "
        "VALUES (10, 'B-0010', 1, 1, 'ready')"
    )
    await db.conn.commit()

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login(client):
    await client.post("/login", data={"username": "branch1", "password": "test123"})


class TestTransfer:
    async def test_base_has_form_styles(self, client):
        """Единые стили форм живут в base.html (передаются всем страницам)."""
        await _login(client)
        r = await client.get("/transfer")
        assert r.status_code == 200
        assert ".form-group { margin-bottom: 16px" in r.text
        assert ".form-group select { width: 100%" in r.text

    async def test_transfer_page_renders(self, client):
        await _login(client)
        r = await client.get("/transfer")
        assert r.status_code == 200
        assert "курьер" in r.text.lower()

    async def test_transfer_confirm_success(self, client):
        await _login(client)
        r = await client.post("/transfer/confirm", data={"box_id": "10", "courier_id": "1"}, follow_redirects=False)
        assert r.status_code == 303
        assert "/my-shipments" in r.headers["location"]

    async def test_archive_role_cannot_access_transfer(self, client, db, test_config):
        from medarchive.services.auth_service import AuthService
        auth = AuthService(secret_key=test_config.effective_secret_key)
        await db.execute(
            "INSERT OR REPLACE INTO users (id, username, password_hash, role) "
            "VALUES (2, 'archive1', ?, 'archive')",
            (auth.hash_password("test123"),),
        )
        await db.conn.commit()
        await client.post("/login", data={"username": "archive1", "password": "test123"})
        r = await client.get("/transfer")
        assert r.status_code == 403
