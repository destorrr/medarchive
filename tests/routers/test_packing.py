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
    await db.conn.commit()

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login(client):
    await client.post("/login", data={"username": "branch1", "password": "test123"})


class TestPacking:
    async def test_packing_page_renders(self, client):
        await _login(client)
        r = await client.get("/packing")
        assert r.status_code == 200
        assert "упаковка" in r.text.lower()

    async def test_create_box_returns_htmx(self, client):
        await _login(client)
        r = await client.post("/packing/new-box")
        assert r.status_code == 200
        assert "B-" in r.text  # box number appears
        # htmx fragment should contain scanner
        assert "autofocus" in r.text.lower() or "scanner" in r.text.lower()

    async def test_scan_card_adds_to_box(self, client):
        await _login(client)
        # Create box first, extract box_id from session or response
        r = await client.post("/packing/new-box")
        assert r.status_code == 200
        # Now scan a card (box_id is tracked server-side via session)
        r = await client.post("/packing/scan-card", data={"full_name": "Тест Пациент"})
        assert r.status_code == 200
        assert "Тест Пациент" in r.text

    async def test_archive_role_cannot_access_packing(self, client, test_config, db):
        # Login as archive user
        from medarchive.services.auth_service import AuthService
        auth = AuthService(secret_key=test_config.effective_secret_key)
        await db.execute(
            "INSERT OR REPLACE INTO users (id, username, password_hash, role) "
            "VALUES (2, 'archive1', ?, 'archive')",
            (auth.hash_password("test123"),),
        )
        await db.conn.commit()
        await client.post("/login", data={"username": "archive1", "password": "test123"})
        r = await client.get("/packing")
        assert r.status_code == 403
