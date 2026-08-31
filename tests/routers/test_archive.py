import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def archive_client(test_config, db):
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
    await db.execute(
        "INSERT OR REPLACE INTO couriers (id, name) VALUES (1, 'K1')"
    )
    # Create a box in_transit with a shipment
    await db.execute(
        "INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (1, 'Пациент 1', 'in_transit')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO boxes (id, box_number, origin_branch_id, created_by, status) "
        "VALUES (10, 'B-0010', 1, 1, 'in_transit')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO box_cards (id, box_id, card_id) VALUES (1, 10, 1)"
    )
    await db.execute(
        "INSERT OR REPLACE INTO shipments (id, kind, box_id, from_branch_id, courier_id, issued_by, status) "
        "VALUES (1, 'box', 10, 1, 1, 1, 'issued')"
    )
    # Section + shelf for placement
    await db.execute("INSERT OR REPLACE INTO archive_sections (id, name) VALUES (1, 'Секция 1')")
    await db.execute(
        "INSERT OR REPLACE INTO archive_shelves (id, section_id, name, qr_code) VALUES (1, 1, 'Полка 3', 'S1-P3')"
    )
    await db.conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestArchiveRouter:
    async def test_receive_page_lists_pending(self, archive_client):
        await archive_client.post("/login", data={"username": "arch1", "password": "test123"})
        r = await archive_client.get("/receive")
        assert r.status_code == 200
        assert "Ожидают приёмки" in r.text

    async def test_scan_box_shows_contents(self, archive_client):
        await archive_client.post("/login", data={"username": "arch1", "password": "test123"})
        r = await archive_client.post("/receive/scan", data={"box_number": "B-0010"})
        assert r.status_code == 200
        assert "Состав коробки B-0010" in r.text  # card name in contents
        assert 'class="badge badge-in_transit"' in r.text  # status badge
        assert "В пути" in r.text  # russian status label
        assert "Подтвердить приёмку" in r.text  # confirm button

    async def test_scan_fragment_confirm_is_htmx(self, archive_client):
        """Кнопка «Подтвердить приёмку» — htmx-форма: hx-post на /receive/confirm,
        ответ вставляется в отдельный контейнер #confirm-error ПОД кнопкой,
        чтобы состав коробки оставался на экране. Страница не перезагружается."""
        await archive_client.post("/login", data={"username": "arch1", "password": "test123"})
        r = await archive_client.post("/receive/scan", data={"box_number": "B-0010"})
        assert r.status_code == 200
        assert 'hx-post="/receive/confirm"' in r.text
        assert 'hx-target="#confirm-error"' in r.text
        assert 'id="confirm-error"' in r.text
        assert 'method="post"' not in r.text

    async def test_confirm_receive_success_sends_hx_redirect(self, archive_client):
        """Успешная приёмка: 200 + HX-Redirect на /placement (htmx выполняет переход)."""
        await archive_client.post("/login", data={"username": "arch1", "password": "test123"})
        r = await archive_client.post("/receive/confirm", data={"box_id": "10"})
        assert r.status_code == 200
        assert r.headers["HX-Redirect"] == "/placement?box_id=10"

    async def test_confirm_receive_error_returns_fragment(self, archive_client, db):
        """Коробка не в статусе in_transit: 200 + flash-фрагмент с русским текстом —
        вставляется в блок скана, страница не перезагружается."""
        await archive_client.post("/login", data={"username": "arch1", "password": "test123"})
        await db.execute("UPDATE boxes SET status = 'received' WHERE id = 10")
        await db.conn.commit()
        r = await archive_client.post("/receive/confirm", data={"box_id": "10"})
        assert r.status_code == 200
        assert 'class="flash"' in r.text
        assert "в статусе 'В пути', сейчас 'Принята'" in r.text

    async def test_placement_page_shows_sections(self, archive_client):
        await archive_client.post("/login", data={"username": "arch1", "password": "test123"})
        r = await archive_client.get("/placement")
        assert r.status_code == 200
        assert "размещен" in r.text.lower() or "секци" in r.text.lower()

    async def test_branch_cannot_access_receive(self, archive_client):
        await archive_client.post("/login", data={"username": "branch1", "password": "test123"})
        r = await archive_client.get("/receive")
        assert r.status_code == 403
