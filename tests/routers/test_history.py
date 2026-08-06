"""Тесты экрана журнала движений."""
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def history_client(test_config, db):
    from medarchive.main import create_app
    app = create_app(test_config)
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    # System user (id=0) for FK on card_movements.user_id
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) VALUES (0, 'sys', 'h', 'admin')")
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) "
                     "VALUES (1, 'viewer1', ?, 'viewer')", (auth.hash_password("test123"),))
    await db.execute("INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (1, 'Тест Тест', 'placed')")
    await db.conn.commit()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login(client):
    r = await client.post("/login", data={"username": "viewer1", "password": "test123"}, follow_redirects=False)
    return r


class TestHistory:
    async def test_history_page_renders(self, history_client):
        await _login(history_client)
        r = await history_client.get("/history")
        assert r.status_code == 200

    async def test_history_shows_movements(self, history_client, db):
        await _login(history_client)
        from medarchive.services.movement_service import MovementService
        ms = MovementService(db)
        await ms.log_movement(1, "placed", None, "shelf Полка 1", user_id=0)

        r = await history_client.get("/history")
        assert r.status_code == 200
        assert "Тест Тест" in r.text
        assert "placed" in r.text
