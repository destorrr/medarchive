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


class TestAdminBackup:
    async def test_backup_page_renders(self, client, db):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/admin/backup")
        assert r.status_code == 200
        assert "Бэкап" in r.text
        # Путь к БД показан на странице
        assert db.config.db_path in r.text

    async def test_backup_create_returns_file(self, client, db):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post("/admin/backup/create")
        assert r.status_code == 200
        assert "application/octet-stream" in r.headers["content-type"]
        assert "attachment" in r.headers.get("content-disposition", "")
        assert ".db" in r.headers.get("content-disposition", "")
        assert len(r.content) > 0

    async def test_backup_forbidden_for_non_admin(self, client):
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/admin/backup")
        assert r.status_code == 403
