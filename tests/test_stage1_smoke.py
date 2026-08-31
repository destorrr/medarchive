"""E2E smoke test: полный прямой поток Stage 1 — упаковка → передача → приёмка → размещение."""
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def e2e_client(test_config, db):
    from medarchive.main import create_app
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

    # Seed: branch, users, courier, section, shelf
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
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
    await db.execute(
        "INSERT OR REPLACE INTO couriers (id, name) VALUES (1, 'Курьер 1')"
    )
    await db.execute("INSERT OR REPLACE INTO archive_sections (id, name) VALUES (1, 'Секция 1')")
    await db.execute(
        "INSERT OR REPLACE INTO archive_shelves (id, section_id, name, qr_code) VALUES (1, 1, 'Полка 3', 'S1-P3')"
    )
    await db.conn.commit()

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestStage1E2E:
    async def test_full_forward_flow(self, e2e_client):
        """Сквозной сценарий: упаковка → передача → приёмка → размещение."""
        client = e2e_client

        # === BRANCH: Упаковка ===
        await client.post("/login", data={"username": "branch1", "password": "test123"})

        # Создать коробку
        r = await client.post("/packing/new-box")
        assert r.status_code == 200
        assert "B-" in r.text

        # Отсканировать 2 карты
        await client.post("/packing/scan-card", data={"full_name": "Пациент А"})
        await client.post("/packing/scan-card", data={"full_name": "Пациент Б"})

        # Проверить, что карты в списке
        r = await client.post("/packing/scan-card", data={"full_name": "Пациент В"})
        assert r.status_code == 200

        # Закрыть коробку → редирект на /transfer
        r = await client.post("/packing/close-box", follow_redirects=False)
        assert r.status_code == 303
        assert "/transfer" in r.headers["location"]

        # === BRANCH: Передача курьеру ===
        r = await client.get("/transfer")
        assert r.status_code == 200

        # Найти box_id из БД (последняя созданная коробка)
        from medarchive.dependencies import get_db
        db = await get_db()
        box_row = await db.fetch_one("SELECT id FROM boxes ORDER BY id DESC LIMIT 1")
        box_id = box_row["id"]

        r = await client.post(
            "/transfer/confirm",
            data={"box_id": str(box_id), "courier_id": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/my-shipments" in r.headers["location"]

        # Проверить мои отправки
        r = await client.get("/my-shipments")
        assert r.status_code == 200
        assert "В пути" in r.text or "в пути" in r.text.lower()

        # Выйти из branch
        await client.get("/logout")

        # === ARCHIVE: Приёмка ===
        await client.post("/login", data={"username": "arch1", "password": "test123"})

        r = await client.get("/receive")
        assert r.status_code == 200

        box_row = await db.fetch_one("SELECT box_number FROM boxes WHERE id = ?", (box_id,))
        box_number = box_row["box_number"]

        r = await client.post("/receive/scan", data={"box_number": box_number})
        assert r.status_code == 200

        r = await client.post("/receive/confirm", data={"box_id": str(box_id)}, follow_redirects=False)
        assert r.status_code == 200
        assert r.headers["HX-Redirect"] == f"/placement?box_id={box_id}"

        # === ARCHIVE: Размещение ===
        r = await client.get("/placement")
        assert r.status_code == 200

        r = await client.post(
            "/placement/confirm",
            data={"box_id": str(box_id), "shelf_id": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303

        # === VERIFY: Проверить финальные статусы в БД ===
        db = await get_db()
        box = await db.fetch_one("SELECT status FROM boxes WHERE id = ?", (box_id,))
        assert box["status"] == "placed"

        cards = await db.fetch_all(
            "SELECT c.status FROM cards c JOIN box_cards bc ON c.id = bc.card_id WHERE bc.box_id = ?",
            (box_id,),
        )
        for card in cards:
            assert card["status"] == "placed"
