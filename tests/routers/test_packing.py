import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def packing_client(test_config, db):
    from medarchive.main import create_app
    app = create_app(test_config)

    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

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
    await db.conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestPackingRouter:
    async def test_packing_page_renders(self, packing_client):
        await packing_client.post("/login", data={"username": "branch1", "password": "test123"})
        r = await packing_client.get("/packing")
        assert r.status_code == 200
        assert "упаковка" in r.text.lower()

    async def test_create_box_returns_htmx(self, packing_client):
        await packing_client.post("/login", data={"username": "branch1", "password": "test123"})
        r = await packing_client.post("/packing/new-box")
        assert r.status_code == 200
        # Response should contain scanner field and box number
        assert "scanner" in r.text.lower() or "input" in r.text.lower()

    async def test_scan_card_adds_row(self, packing_client):
        await packing_client.post("/login", data={"username": "branch1", "password": "test123"})
        # Create box first
        r = await packing_client.post("/packing/new-box")
        # Extract box_id from response (it's embedded in the HTML)
        assert "B-" in r.text
        # Now scan a card
        r = await packing_client.post("/packing/scan-card", data={"full_name": "Иванов И.И."})
        assert r.status_code == 200
        assert "Иванов" in r.text

    async def test_remove_card_from_box_htmx(self, packing_client):
        """Кнопка «Убрать» убирает карту из коробки, список обновляется."""
        await packing_client.post("/login", data={"username": "branch1", "password": "test123"})
        await packing_client.post("/packing/new-box")
        r = await packing_client.post("/packing/scan-card", data={"full_name": "Иванов И.И."})
        assert "Иванов" in r.text
        # Достать card_id из БД
        from medarchive.dependencies import get_db
        db = await get_db()
        row = await db.fetch_one("SELECT id FROM cards WHERE full_name = ?", ("Иванов И.И.",))
        card_id = row["id"]
        # Удалить карту из коробки
        r = await packing_client.post("/packing/remove-card", data={"card_id": card_id})
        assert r.status_code == 200
        assert "Иванов" not in r.text
        # Статус карты вернулся в in_branch
        row = await db.fetch_one("SELECT status FROM cards WHERE id = ?", (card_id,))
        assert row["status"] == "in_branch"

    async def test_archive_role_cannot_access_packing(self, packing_client):
        await packing_client.post("/login", data={"username": "arch1", "password": "test123"})
        r = await packing_client.get("/packing")
        assert r.status_code == 403

    async def test_active_box_survives_page_reload(self, packing_client):
        """Активная коробка привязана к сессии: перезагрузка страницы её не теряет."""
        await packing_client.post("/login", data={"username": "branch1", "password": "test123"})
        r = await packing_client.post("/packing/new-box")
        assert "B-" in r.text
        # Fresh GET (как будто пользователь обновил страницу / вернулся назад)
        r = await packing_client.get("/packing")
        assert r.status_code == 200
        assert "B-" in r.text  # номер коробки снова виден

    async def test_scan_without_box_shows_error(self, packing_client):
        """Без активной коробки скан не уходит в 'невидимую' коробку — показывается ошибка."""
        await packing_client.post("/login", data={"username": "branch1", "password": "test123"})
        r = await packing_client.post("/packing/scan-card", data={"full_name": "Иванов И.И."})
        assert r.status_code == 200
        assert "Сначала создайте коробку" in r.text
        assert "Иванов" not in r.text

    async def test_active_box_survives_logout(self, packing_client):
        """Коробка — данные в БД: логаут не должен её терять, после повторного входа можно продолжить."""
        await packing_client.post("/login", data={"username": "branch1", "password": "test123"})
        r = await packing_client.post("/packing/new-box")
        assert "B-" in r.text
        # Добавить карту
        r = await packing_client.post("/packing/scan-card", data={"full_name": "Иванов И.И."})
        assert "Иванов" in r.text
        # Логаут и повторный вход
        await packing_client.get("/logout")
        await packing_client.post("/login", data={"username": "branch1", "password": "test123"})
        r = await packing_client.get("/packing")
        assert r.status_code == 200
        assert "B-" in r.text  # та же коробка видна
        assert "Иванов" in r.text  # и карты на месте
        # Можно продолжить сканировать в ту же коробку
        r = await packing_client.post("/packing/scan-card", data={"full_name": "Петров П.П."})
        assert "Петров" in r.text
        assert "Иванов" in r.text
