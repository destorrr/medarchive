"""E2E smoke test: админ-панель (Stage 4) — справочники, QR полок, инциденты, бэкап."""
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def admin_client(test_config, db):
    from medarchive.main import create_app
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

    # Admin-пользователь: username=admin / password=test123
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'admin', ?, 'admin', NULL)",
        (auth.hash_password("test123"),),
    )
    await db.conn.commit()

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login_as_admin(ac) -> None:
    """Логин админа: 303 → /dashboard."""
    r = await ac.post(
        "/login",
        data={"username": "admin", "password": "test123"},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"Admin login failed: {r.status_code}"
    assert "/dashboard" in r.headers["location"]


class TestStage4E2E:
    async def test_admin_crud_reference_flow(self, admin_client, db):
        """Справочники: филиалы → пользователи → курьеры."""
        ac = admin_client
        await _login_as_admin(ac)

        # === Филиалы: создать → проверить на странице ===
        r = await ac.post(
            "/admin/branches",
            data={"name": "Филиал E2E", "code": "FE2E"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/branches" in r.headers["location"]

        r = await ac.get("/admin/branches")
        assert r.status_code == 200
        assert "Филиал E2E" in r.text

        branch = await db.fetch_one("SELECT id FROM branches WHERE code = 'FE2E'")
        assert branch is not None, "Филиал не создан в БД"

        # === Пользователи: создать → проверить на странице ===
        r = await ac.post(
            "/admin/users",
            data={
                "username": "e2e_user",
                "password": "pass123",
                "role": "branch",
                "branch_id": str(branch["id"]),
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/users" in r.headers["location"]

        r = await ac.get("/admin/users")
        assert r.status_code == 200
        assert "e2e_user" in r.text

        user = await db.fetch_one("SELECT * FROM users WHERE username = 'e2e_user'")
        assert user is not None
        assert user["role"] == "branch"
        assert user["branch_id"] == branch["id"]

        # === Курьеры: создать → проверить на странице ===
        r = await ac.post(
            "/admin/couriers",
            data={"name": "Курьер E2E", "phone": "+79990001122"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/couriers" in r.headers["location"]

        r = await ac.get("/admin/couriers")
        assert r.status_code == 200
        assert "Курьер E2E" in r.text

        courier = await db.fetch_one("SELECT id, phone FROM couriers WHERE name = 'Курьер E2E'")
        assert courier is not None
        assert courier["phone"] == "+79990001122"

    async def test_admin_sections_qr_incidents_backup_flow(self, admin_client, db):
        """Секции/полки → QR-печать → инциденты → бэкап."""
        ac = admin_client
        await _login_as_admin(ac)

        # === Секция: создать ===
        r = await ac.post(
            "/admin/sections",
            data={"name": "Секция E2E"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/sections" in r.headers["location"]

        section = await db.fetch_one("SELECT id FROM archive_sections WHERE name = 'Секция E2E'")
        assert section is not None

        # === Полка: создать в секции ===
        r = await ac.post(
            "/admin/shelves",
            data={"section_id": str(section["id"]), "name": "Полка E2E"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/sections" in r.headers["location"]

        shelf = await db.fetch_one("SELECT id, section_id FROM archive_shelves WHERE name = 'Полка E2E'")
        assert shelf is not None
        assert shelf["section_id"] == section["id"]

        # === Страница секций: видны и секция, и полка ===
        r = await ac.get("/admin/sections")
        assert r.status_code == 200
        assert "Секция E2E" in r.text
        assert "Полка E2E" in r.text

        # === QR одной полки: PNG ===
        r = await ac.get(f"/admin/shelves/{shelf['id']}/qr")
        assert r.status_code == 200
        assert "image/png" in r.headers["content-type"]
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n", "Тело ответа не похоже на PNG"

        # === Лист QR для печати: HTML со словом QR и base64-картинками ===
        r = await ac.get("/admin/shelves/qr-sheet")
        assert r.status_code == 200
        assert "QR" in r.text
        assert "data:image/png;base64" in r.text

        # === Инциденты: рендерится (пусто) ===
        r = await ac.get("/admin/incidents")
        assert r.status_code == 200
        assert "Инциденты" in r.text

        # === Бэкап: страница + скачивание .db ===
        r = await ac.get("/admin/backup")
        assert r.status_code == 200
        assert "Бэкап" in r.text

        r = await ac.post("/admin/backup/create", follow_redirects=False)
        assert r.status_code == 200
        assert "application/octet-stream" in r.headers["content-type"]
        assert r.content.startswith(b"SQLite format 3"), "Скачанный файл не похож на SQLite БД"
        assert ".db" in r.headers.get("content-disposition", "")
