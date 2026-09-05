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

    async def test_users_create_with_full_name(self, client, db):
        """Создание пользователя с ФИО через админку сохраняет full_name в БД."""
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            "/admin/users",
            data={
                "username": "new_user_fn",
                "password": "secret123",
                "role": "viewer",
                "branch_id": "",
                "full_name": "Иванов Иван Иванович",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM users WHERE username = 'new_user_fn'")
        assert row is not None
        assert row["full_name"] == "Иванов Иван Иванович"

    async def test_users_create_without_full_name_defaults_empty(self, client, db):
        """Создание без full_name (старый вызов) даёт DEFAULT '' — не падает."""
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            "/admin/users",
            data={"username": "no_fn_user", "password": "secret123", "role": "viewer", "branch_id": ""},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM users WHERE username = 'no_fn_user'")
        assert row is not None
        assert row["full_name"] == ""

    async def test_users_edit_full_name(self, client, db):
        """Редактирование ФИО через админку сохраняет новое значение."""
        await db.execute(
            "INSERT INTO users (username, password_hash, role, branch_id) "
            "VALUES ('edit_fn', 'hash', 'branch', NULL)"
        )
        await db.conn.commit()
        target = await db.fetch_one("SELECT id FROM users WHERE username = 'edit_fn'")
        uid = target["id"]

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            f"/admin/users/{uid}/edit",
            data={"role": "branch", "branch_id": "", "password": "", "full_name": "Петров Пётр"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM users WHERE id = ?", (uid,))
        assert row["full_name"] == "Петров Пётр"

    async def test_users_page_has_full_name_column_and_input(self, client):
        """Страница /admin/users содержит колонку «ФИО» и поле full_name (создание + строка)."""
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/admin/users")
        assert r.status_code == 200
        assert "<th>ФИО</th>" in r.text
        assert 'name="full_name"' in r.text

    async def test_users_page_shows_saved_full_name(self, client, db):
        """Сохранённое ФИО отображается на странице /admin/users."""
        await db.execute(
            "INSERT INTO users (username, password_hash, role, branch_id, full_name) "
            "VALUES ('shown_fn', 'hash', 'branch', NULL, 'Сидоров Сидор Сидорович')"
        )
        await db.conn.commit()
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/admin/users")
        assert r.status_code == 200
        assert "Сидоров Сидор Сидорович" in r.text

    async def test_users_cannot_delete_self(self, client, db):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        admin = await db.fetch_one("SELECT id FROM users WHERE username = 'admin_user'")
        uid = admin["id"]

        r = await client.post(f"/admin/users/{uid}/delete", follow_redirects=False)
        assert r.status_code in (303, 400)
        row = await db.fetch_one("SELECT * FROM users WHERE id = ?", (uid,))
        assert row is not None
