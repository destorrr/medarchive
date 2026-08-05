import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def packing_client(test_config, db):
    from medarchive.main import create_app
    app = create_app(test_config)

    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'branch1', ?, 'branch', 1)",
        (auth.hash_password("test123"),),
    )
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role) "
        "VALUES (2, 'arch1', ?, 'archive')",
        (auth.hash_password("test123"),),
    )
    await db.conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestPackingRouter:
    async def test_packing_page_renders(self, packing_client):
        await packing_client.post("/login", data={"username": "branch1", "password": "test123"})
        r = await packing_client.get("/packing")
        assert r.status_code == 200
        assert "упаковка" in r.text.lower()

    async def test_create_box_returns_htmx(self, packing_client):
        await packing_client.post("/login", data={"username": "branch1", "password": "test123"})
        r = await packing_client.post("/packing/new-box")
        assert r.status_code == 200
        # Response should contain scanner field and box number
        assert "scanner" in r.text.lower() or "input" in r.text.lower()

    async def test_scan_card_adds_row(self, packing_client):
        await packing_client.post("/login", data={"username": "branch1", "password": "test123"})
        # Create box first
        r = await packing_client.post("/packing/new-box")
        # Extract box_id from response (it's embedded in the HTML)
        assert "B-" in r.text
        # Now scan a card
        r = await packing_client.post("/packing/scan-card", data={"full_name": "Иванов И.И."})
        assert r.status_code == 200
        assert "Иванов" in r.text

    async def test_archive_role_cannot_access_packing(self, packing_client):
        await packing_client.post("/login", data={"username": "arch1", "password": "test123"})
        r = await packing_client.get("/packing")
        assert r.status_code == 403
