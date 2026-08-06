"""E2E smoke test: полный обратный поток Stage 2 — заявка → выполнение → возврат → приём."""
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def e2e_client(test_config, db):
    from medarchive.main import create_app
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

    # Seed: филиалы, пользователи, курьер, карта
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
    await db.execute("INSERT OR REPLACE INTO couriers (id, name) VALUES (1, 'Курьер 1')")
    await db.execute(
        "INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (1, 'Пациент Возврат', 'placed')"
    )
    await db.conn.commit()

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestStage2E2E:
    async def test_full_return_flow(self, e2e_client, db):
        """Сквозной сценарий: заявка → выполнение → возврат → приём возврата."""
        client = e2e_client

        # === BRANCH1: Создаёт заявку на возврат карты ===
        await client.post("/login", data={"username": "branch1", "password": "test123"})
        r = await client.post(
            "/requests/new",
            data={"card_id": "1", "target_branch_id": "1", "target_department": "0"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        req = await db.fetch_one(
            "SELECT * FROM requests WHERE card_id = 1 ORDER BY id DESC LIMIT 1"
        )
        assert req["status"] == "new"

        # Сменить пользователя: архив
        await client.get("/logout")
        await client.post("/login", data={"username": "arch1", "password": "test123"})

        # === ARCHIVE: Выполняет заявку (без создания отправки) ===
        r = await client.post(
            "/requests/fulfill",
            data={"request_id": str(req["id"]), "action": "single", "courier_id": "0"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        card = await db.fetch_one("SELECT status FROM cards WHERE id = 1")
        assert card["status"] == "pending_return"

        # === ARCHIVE: Создаёт возвратную отправку (одиночная карта) ===
        from medarchive.adapters.sqlite.repositories import (
            BoxRepositoryImpl,
            CardRepositoryImpl,
            CourierRepositoryImpl,
        )
        from medarchive.services.return_service import ReturnService

        return_service = ReturnService(
            db, CardRepositoryImpl(db), BoxRepositoryImpl(db), CourierRepositoryImpl(db)
        )
        shipment = await return_service.return_single_card(
            card_id=1, courier_id=1, to_branch_id=1, issued_by=2
        )
        assert shipment["kind"] == "card"
        card = await db.fetch_one("SELECT status FROM cards WHERE id = 1")
        assert card["status"] == "in_transit"

        # Сменить пользователя: филиал
        await client.get("/logout")
        await client.post("/login", data={"username": "branch1", "password": "test123"})

        # === BRANCH1: Подтверждает приём возврата ===
        r = await client.post(
            "/receive-return/confirm",
            data={"card_id": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/my-shipments" in r.headers["location"]

        # === VERIFY: Финальные статусы ===
        card = await db.fetch_one("SELECT status FROM cards WHERE id = 1")
        assert card["status"] == "in_branch"
        shipment_row = await db.fetch_one(
            "SELECT status FROM shipments WHERE id = ?", (shipment["id"],)
        )
        assert shipment_row["status"] == "received"
