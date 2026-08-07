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


async def _seed_shelves(db):
    """Секция 1 с двумя полками."""
    await db.execute("INSERT OR REPLACE INTO archive_sections (id, name) VALUES (1, 'Секция А')")
    await db.execute(
        "INSERT OR REPLACE INTO archive_shelves (id, section_id, name) VALUES (1, 1, 'Полка 1')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO archive_shelves (id, section_id, name) VALUES (2, 1, 'Полка 2')"
    )
    await db.conn.commit()


class TestAdminSectionsQR:
    async def test_qr_page_returns_png(self, client, db):
        await _seed_shelves(db)
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/admin/shelves/1/qr")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        # Магическая сигнатура PNG
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_generate_qr_sets_qr_code(self, client, db):
        await _seed_shelves(db)
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.post("/admin/shelves/1/generate-qr", follow_redirects=False)
        assert r.status_code == 303
        row = await db.fetch_one("SELECT * FROM archive_shelves WHERE id = 1")
        assert row["qr_code"] is not None
        assert len(row["qr_code"]) > 0

    async def test_qr_sheet_renders(self, client, db):
        await _seed_shelves(db)
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        # Генерируем QR для обеих полок, чтобы лист был наполнен
        for sid in (1, 2):
            await client.post(f"/admin/shelves/{sid}/generate-qr", follow_redirects=False)
        r = await client.get("/admin/shelves/qr-sheet")
        assert r.status_code == 200
        assert "S1-P1" in r.text
        assert "S1-P2" in r.text

    async def test_qr_sheet_has_base64_images(self, client, db):
        await _seed_shelves(db)
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        await client.post("/admin/shelves/1/generate-qr", follow_redirects=False)
        r = await client.get("/admin/shelves/qr-sheet")
        assert r.status_code == 200
        assert "data:image/png;base64," in r.text

    async def test_qr_sheet_forbidden_for_non_admin(self, client):
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/admin/shelves/qr-sheet")
        assert r.status_code == 403
