"""Тесты экрана «Приём возврата» (роль: Филиал)."""
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def receive_client(test_config, db):
    from medarchive.main import create_app
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (2, 'B2', 'B2')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'branch1', ?, 'branch', 1)",
        (auth.hash_password("test123"),),
    )
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role) "
        "VALUES (2, 'arch1', ?, 'archive')",
        (auth.hash_password("test123"),),
    )
    await db.execute("INSERT OR REPLACE INTO couriers (id, name) VALUES (1, 'K1')")
    # Карта уже в пути + активная одиночная отправка возврата
    await db.execute(
        "INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (1, 'Иванов И.И.', 'in_transit')"
    )
    await db.execute(
        """INSERT OR REPLACE INTO shipments
           (id, kind, card_id, to_branch_id, courier_id, issued_by, status)
           VALUES (1, 'card', 1, 1, 1, 2, 'issued')"""
    )
    await db.conn.commit()

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login(client, role="branch1"):
    await client.post("/login", data={"username": role, "password": "test123"})


class TestReceiveReturn:
    async def test_page_renders(self, receive_client):
        """GET /receive-return отдаёт страницу с полем сканера."""
        await _login(receive_client)
        r = await receive_client.get("/receive-return")
        assert r.status_code == 200
        assert "приём" in r.text.lower() or "сканер" in r.text.lower()

    async def test_confirm_receive_return(self, receive_client, db):
        """POST /receive-return/confirm переводит карту в in_branch."""
        await _login(receive_client)
        r = await receive_client.post(
            "/receive-return/confirm",
            data={"card_id": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/my-shipments" in r.headers["location"]

        card = await db.fetch_one("SELECT status FROM cards WHERE id = 1")
        assert card["status"] == "in_branch"

        shipment = await db.fetch_one("SELECT status FROM shipments WHERE id = 1")
        assert shipment["status"] == "received"
