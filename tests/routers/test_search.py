import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def search_client(test_config, db):
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
        "VALUES (2, 'viewer1', ?, 'viewer')",
        (auth.hash_password("test123"),),
    )
    await db.execute(
        "INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (1, 'Иванов Иван Иванович', 'placed')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO boxes (id, box_number, origin_branch_id, created_by, status) "
        "VALUES (10, 'B-0010', 1, 1, 'placed')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO box_cards (id, box_id, card_id) VALUES (1, 10, 1)"
    )
    await db.conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login(client, username="viewer1"):
    await client.post("/login", data={"username": username, "password": "test123"})


class TestSearch:
    async def test_search_page_renders(self, search_client):
        await _login(search_client)
        r = await search_client.get("/search")
        assert r.status_code == 200
        assert "Поиск" in r.text

    async def test_search_finds_card(self, search_client):
        await _login(search_client)
        r = await search_client.post("/search", data={"query": "Иванов"})
        assert r.status_code == 200
        assert "Иванов Иван Иванович" in r.text

    async def test_search_finds_box(self, search_client):
        await _login(search_client)
        r = await search_client.post("/search", data={"query": "B-0010"})
        assert r.status_code == 200
        assert "B-0010" in r.text
