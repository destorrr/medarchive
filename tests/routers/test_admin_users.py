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


class TestAdminUsers:
    async def test_users_page_renders(self, client):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/admin/users")
        assert r.status_code == 200
        assert "Пользователи" in r.text
        assert "admin_user" in r.text

    async def test_users_create(self, client, db):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            "/admin/users",
            data={
                "username": "new_user",
                "password": "secret123",
                "role": "viewer",
                "branch_id": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM users WHERE username = 'new_user'")
        assert row is not None
        assert row["role"] == "viewer"
        assert row["branch_id"] == 1

        from medarchive.services.auth_service import AuthService
        auth = AuthService(secret_key="test-secret-key-for-testing")
        assert auth.verify_password("secret123", row["password_hash"])

    async def test_users_edit(self, client, db):
        await db.execute(
            "INSERT INTO users (username, password_hash, role, branch_id) "
            "VALUES ('edit_me', 'hash', 'branch', 1)"
        )
        await db.conn.commit()
        target = await db.fetch_one("SELECT id FROM users WHERE username = 'edit_me'")
        uid = target["id"]

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            f"/admin/users/{uid}/edit",
            data={"role": "viewer", "branch_id": "", "password": "newpass123"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM users WHERE id = ?", (uid,))
        assert row["role"] == "viewer"

        from medarchive.services.auth_service import AuthService
        auth = AuthService(secret_key="test-secret-key-for-testing")
        assert auth.verify_password("newpass123", row["password_hash"])

    async def test_users_delete(self, client, db):
        await db.execute(
            "INSERT INTO users (username, password_hash, role, branch_id) "
            "VALUES ('delete_me', 'hash', 'viewer', NULL)"
        )
        await db.conn.commit()
        target = await db.fetch_one("SELECT id FROM users WHERE username = 'delete_me'")
        uid = target["id"]

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(f"/admin/users/{uid}/delete", follow_redirects=False)
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM users WHERE id = ?", (uid,))
        assert row is None

    async def test_users_cannot_delete_self(self, client, db):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        admin = await db.fetch_one("SELECT id FROM users WHERE username = 'admin_user'")
        uid = admin["id"]

        r = await client.post(f"/admin/users/{uid}/delete", follow_redirects=False)
        assert r.status_code in (303, 400)
        row = await db.fetch_one("SELECT * FROM users WHERE id = ?", (uid,))
        assert row is not None
