import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def req_client(test_config, db):
    from medarchive.main import create_app
    app = create_app(test_config)
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (2, 'B2', 'B2')")
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) VALUES (1, 'branch1', ?, 'branch', 1)", (auth.hash_password("test123"),))
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) VALUES (2, 'arch1', ?, 'archive')", (auth.hash_password("test123"),))
    await db.execute("INSERT OR REPLACE INTO couriers (id, name) VALUES (1, 'K1')")
    await db.execute("INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (1, 'Иванов И.И.', 'placed')")
    await db.execute("INSERT OR REPLACE INTO boxes (id, box_number, origin_branch_id, created_by, status) VALUES (10, 'B-0010', 1, 1, 'placed')")
    await db.execute("INSERT OR REPLACE INTO box_cards (id, box_id, card_id) VALUES (1, 10, 1)")
    await db.conn.commit()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login(client, role="branch1"):
    await client.post("/login", data={"username": role, "password": "test123"})


class TestRequestNew:
    async def test_request_new_page_renders(self, req_client):
        await _login(req_client)
        r = await req_client.get("/requests/new")
        assert r.status_code == 200
        assert "заявк" in r.text.lower() or "поиск" in r.text.lower()

    async def test_create_request_via_search(self, req_client):
        await _login(req_client)
        # Search for card
        r = await req_client.post("/requests/new", data={"search_name": "Иванов"})
        assert r.status_code == 200
        assert "Иванов" in r.text
        # Now create the request
        r = await req_client.post("/requests/new", data={"card_id": 1, "target_branch_id": 2, "target_department": "0"}, follow_redirects=False)
        assert r.status_code == 303
        assert "/requests/my" in r.headers["location"]

    async def test_my_requests_page(self, req_client, db):
        await _login(req_client)
        # Create a request first
        await req_client.post("/requests/new", data={"card_id": 1, "target_branch_id": 2, "target_department": "0"})
        r = await req_client.get("/requests/my")
        assert r.status_code == 200


class TestRequestQueue:
    async def test_queue_page_renders(self, req_client):
        await _login(req_client, "arch1")
        r = await req_client.get("/requests")
        assert r.status_code == 200

    async def test_fulfill_request(self, req_client, db):
        # Branch creates request
        await _login(req_client, "branch1")
        await req_client.post("/requests/new", data={"card_id": 1, "target_branch_id": 2, "target_department": "0"})
        await req_client.get("/logout")

        # Archive fulfills
        await _login(req_client, "arch1")
        r = await req_client.get("/requests")
        assert r.status_code == 200
        # Find request id
        req_row = await db.fetch_one("SELECT id FROM requests ORDER BY id DESC LIMIT 1")
        r = await req_client.post("/requests/fulfill", data={"request_id": str(req_row["id"]), "action": "single"}, follow_redirects=False)
        assert r.status_code == 303

    async def test_branch_cannot_access_queue(self, req_client):
        await _login(req_client, "branch1")
        r = await req_client.get("/requests")
        assert r.status_code == 403
