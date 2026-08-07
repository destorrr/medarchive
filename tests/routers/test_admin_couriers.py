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


class TestAdminCouriers:
    async def test_couriers_page_renders(self, client):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/admin/couriers")
        assert r.status_code == 200
        assert "Курьеры" in r.text

    async def test_couriers_create(self, client, db):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            "/admin/couriers",
            data={"name": "Иван Петров", "phone": "+79990001122"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM couriers WHERE name = 'Иван Петров'")
        assert row is not None
        assert row["phone"] == "+79990001122"

    async def test_couriers_create_without_phone(self, client, db):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            "/admin/couriers",
            data={"name": "Без телефона"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM couriers WHERE name = 'Без телефона'")
        assert row is not None
        assert row["phone"] is None

    async def test_couriers_edit(self, client, db):
        await db.execute(
            "INSERT INTO couriers (name, phone) VALUES ('Старый курьер', '+70000000000')"
        )
        await db.conn.commit()
        target = await db.fetch_one("SELECT id FROM couriers WHERE name = 'Старый курьер'")
        cid = target["id"]

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            f"/admin/couriers/{cid}/edit",
            data={"name": "Новый курьер", "phone": "+71112223344"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM couriers WHERE id = ?", (cid,))
        assert row["name"] == "Новый курьер"
        assert row["phone"] == "+71112223344"

    async def test_couriers_delete(self, client, db):
        await db.execute(
            "INSERT INTO couriers (name, phone) VALUES ('Удаляемый', '+70000000001')"
        )
        await db.conn.commit()
        target = await db.fetch_one("SELECT id FROM couriers WHERE name = 'Удаляемый'")
        cid = target["id"]

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(f"/admin/couriers/{cid}/delete", follow_redirects=False)
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM couriers WHERE id = ?", (cid,))
        assert row is None

    async def test_couriers_page_denied_for_branch_user(self, client):
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/admin/couriers")
        assert r.status_code == 403
