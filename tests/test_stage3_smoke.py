"""E2E smoke test: поиск + аудит (Stage 3)."""
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def stage3_client(test_config, db):
    from medarchive.main import create_app
    app = create_app(test_config)
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) "
                     "VALUES (0, 'sys', 'h', 'admin')")
    pwd = auth.hash_password("test123")
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
                     "VALUES (1, 'branch1', ?, 'branch', 1)", (pwd,))
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) "
                     "VALUES (2, 'arch1', ?, 'archive')", (pwd,))
    await db.execute("INSERT OR REPLACE INTO couriers (id, name) VALUES (1, 'K1')")
    await db.execute("INSERT OR REPLACE INTO archive_sections (id, name) VALUES (1, 'Секция 1')")
    await db.execute("INSERT OR REPLACE INTO archive_shelves (id, section_id, name) "
                     "VALUES (1, 1, 'Полка 1')")
    await db.conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestStage3E2E:
    @pytest.mark.asyncio
    async def test_full_search_and_audit_flow(self, stage3_client):
        """Полный цикл: упаковка → передача → приёмка → размещение → поиск → журнал → дашборд."""
        ac = stage3_client

        # 1. Login as branch
        r = await ac.post("/login", data={"username": "branch1", "password": "test123"}, follow_redirects=False)
        assert r.status_code == 303

        # 2. Create box + add card
        r = await ac.post("/packing/new-box", follow_redirects=False)
        assert r.status_code == 200
        assert "box" in r.text.lower() or "короб" in r.text.lower()

        r = await ac.post("/packing/scan-card", data={"full_name": "Петров П.П."}, follow_redirects=False)
        assert r.status_code == 200

        r = await ac.post("/packing/close-box", follow_redirects=False)
        assert r.status_code == 303  # redirects to /transfer

        # 3. Transfer to courier
        r = await ac.post("/transfer/confirm", data={"box_id": "1", "courier_id": "1"}, follow_redirects=False)
        assert r.status_code == 303

        # 4. Login as archive
        r = await ac.post("/login", data={"username": "arch1", "password": "test123"}, follow_redirects=False)
        assert r.status_code == 303, f"Archive login failed: {r.status_code}"

        # 5. Receive box (box number is 0001)
        r = await ac.post("/receive/scan", data={"box_number": "0001"}, follow_redirects=False)
        assert r.status_code == 200
        r = await ac.post("/receive/confirm", data={"box_id": "1"}, follow_redirects=False)
        assert r.status_code == 200
        assert r.headers["HX-Redirect"] == "/placement?box_id=1"

        # 6. Place box — htmx-фрагмент: 200 + success-флеш «размещена на полке»
        r = await ac.post("/placement/confirm", data={"box_id": "1", "shelf_id": "1"}, follow_redirects=False)
        assert r.status_code == 200
        assert "размещена на полке" in r.text

        # 7. Search — find the card
        r = await ac.get("/search")
        assert r.status_code == 200
        r = await ac.post("/search", data={"query": "Петров"})
        assert r.status_code == 200
        assert "Петров" in r.text

        # 8. History — contains movements
        r = await ac.get("/history")
        assert r.status_code == 200
        # At least one movement exists (packed/received/placed)
        assert "packed" in r.text.lower() or "received" in r.text.lower() or "placed" in r.text.lower()

        # 9. Dashboard — shows stats
        r = await ac.get("/dashboard")
        assert r.status_code == 200
        assert "Карт всего" in r.text
