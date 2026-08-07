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


class TestAdminSections:
    async def test_sections_page_renders(self, client):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/admin/sections")
        assert r.status_code == 200
        assert "Секции" in r.text

    async def test_sections_create(self, client, db):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            "/admin/sections",
            data={"name": "Секция А"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM archive_sections WHERE name = 'Секция А'")
        assert row is not None

    async def test_sections_edit(self, client, db):
        await db.execute("INSERT INTO archive_sections (name) VALUES ('Старая секция')")
        await db.conn.commit()
        target = await db.fetch_one(
            "SELECT id FROM archive_sections WHERE name = 'Старая секция'"
        )
        sid = target["id"]

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            f"/admin/sections/{sid}/edit",
            data={"name": "Новая секция"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM archive_sections WHERE id = ?", (sid,))
        assert row["name"] == "Новая секция"

    async def test_sections_delete(self, client, db):
        await db.execute("INSERT INTO archive_sections (name) VALUES ('Пустая секция')")
        await db.conn.commit()
        target = await db.fetch_one(
            "SELECT id FROM archive_sections WHERE name = 'Пустая секция'"
        )
        sid = target["id"]

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(f"/admin/sections/{sid}/delete", follow_redirects=False)
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM archive_sections WHERE id = ?", (sid,))
        assert row is None

    async def test_sections_delete_blocked_with_shelves(self, client, db):
        await db.execute("INSERT INTO archive_sections (name) VALUES ('Занятая секция')")
        await db.execute(
            "INSERT INTO archive_shelves (section_id, name) VALUES (?, 'Полка 1')",
            (1,),
        )
        await db.conn.commit()
        target = await db.fetch_one(
            "SELECT id FROM archive_sections WHERE name = 'Занятая секция'"
        )
        sid = target["id"]

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(f"/admin/sections/{sid}/delete", follow_redirects=False)
        assert r.status_code == 303
        # Секция с полками не удаляется
        row = await db.fetch_one("SELECT * FROM archive_sections WHERE id = ?", (sid,))
        assert row is not None

    async def test_shelves_create(self, client, db):
        await db.execute("INSERT INTO archive_sections (id, name) VALUES (1, 'Секция А')")
        await db.conn.commit()

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            "/admin/shelves",
            data={"section_id": "1", "name": "Полка 1"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM archive_shelves WHERE name = 'Полка 1'")
        assert row is not None
        assert row["section_id"] == 1

    async def test_shelves_edit(self, client, db):
        await db.execute("INSERT INTO archive_sections (id, name) VALUES (1, 'Секция А')")
        await db.execute(
            "INSERT INTO archive_shelves (section_id, name) VALUES (1, 'Полка старая')"
        )
        await db.conn.commit()
        target = await db.fetch_one(
            "SELECT id FROM archive_shelves WHERE name = 'Полка старая'"
        )
        shelf_id = target["id"]

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(
            f"/admin/shelves/{shelf_id}/edit",
            data={"name": "Полка новая"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM archive_shelves WHERE id = ?", (shelf_id,))
        assert row["name"] == "Полка новая"

    async def test_shelves_delete(self, client, db):
        await db.execute("INSERT INTO archive_sections (id, name) VALUES (1, 'Секция А')")
        await db.execute(
            "INSERT INTO archive_shelves (section_id, name) VALUES (1, 'Полка на удаление')"
        )
        await db.conn.commit()
        target = await db.fetch_one(
            "SELECT id FROM archive_shelves WHERE name = 'Полка на удаление'"
        )
        shelf_id = target["id"]

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post(f"/admin/shelves/{shelf_id}/delete", follow_redirects=False)
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM archive_shelves WHERE id = ?", (shelf_id,))
        assert row is None
