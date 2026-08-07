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


class TestAdminBranches:
    async def test_branches_page_renders(self, client):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/admin/branches")
        assert r.status_code == 200
        assert "Филиалы" in r.text
        assert "Test Branch" in r.text

    async def test_branches_create(self, client, db):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            "/admin/branches",
            data={"name": "Новый Филиал", "code": "NF"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM branches WHERE code = 'NF'")
        assert row is not None
        assert row["name"] == "Новый Филиал"

    async def test_branches_edit(self, client, db):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            "/admin/branches/1/edit",
            data={"name": "Renamed Branch", "code": "RB"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM branches WHERE id = 1")
        assert row["name"] == "Renamed Branch"
        assert row["code"] == "RB"

    async def test_branches_delete(self, client, db):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post("/admin/branches/1/delete", follow_redirects=False)
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM branches WHERE id = 1")
        assert row is None

    async def test_branches_forbidden_for_non_admin(self, client):
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/admin/branches")
        assert r.status_code == 403
