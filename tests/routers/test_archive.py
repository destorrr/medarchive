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

    # === /placement — partial'ы списка и панели (Задача 2 редизайна) ===

    async def _make_received_box(self, db, box_id=20, box_number="B-0020",
                                 received_at="2026-09-01 10:12:00"):
        """Вспомогательный: коробка received + shipment received + 2 карты + секция/полка."""
        await db.execute(
            "INSERT INTO cards (id, full_name, status) VALUES (2, 'Пациент 2', 'in_transit')"
        )
        await db.execute(
            "INSERT INTO cards (id, full_name, status) VALUES (3, 'Пациент 3', 'in_transit')"
        )
        await db.execute(
            "INSERT INTO boxes (id, box_number, origin_branch_id, created_by, status) "
            "VALUES (?, ?, 1, 1, 'received')",
            (box_id, box_number),
        )
        await db.execute(
            "INSERT INTO box_cards (id, box_id, card_id) VALUES (2, ?, 2)",
            (box_id,),
        )
        await db.execute(
            "INSERT INTO box_cards (id, box_id, card_id) VALUES (3, ?, 3)",
            (box_id,),
        )
        await db.execute(
            "INSERT INTO shipments (id, kind, box_id, from_branch_id, courier_id, issued_by, "
            "received_at, status) VALUES (2, 'box', ?, 1, 1, 1, ?, 'received')",
            (box_id, received_at),
        )
        await db.conn.commit()

    async def test_placement_empty_state(self, archive_client):
        """GET /placement без принятых коробок → карточка пустого состояния
        «Все коробки размещены» + стили флешей в base.html."""
        await archive_client.post("/login", data={"username": "arch1", "password": "test123"})
        r = await archive_client.get("/placement")
        assert r.status_code == 200
        assert "Все коробки размещены" in r.text
        assert "Новых коробок, ожидающих размещения, нет." in r.text
        assert "Коробки появляются здесь после приёмки на странице „Приёмка“." in r.text
        # стили флешей (base.html)
        assert ".flash-success" in r.text
        assert ".flash-error" in r.text

    async def test_placement_lists_received_boxes(self, archive_client, db):
        """GET /placement с принятой коробкой → карточка «Ожидают размещения (N)»,
        заголовки таблицы, htmx-кнопка и скрытый контейнер панели #panel-row-N."""
        await archive_client.post("/login", data={"username": "arch1", "password": "test123"})
        await self._make_received_box(db)

        r = await archive_client.get("/placement")
        assert r.status_code == 200
        assert "Ожидают размещения" in r.text
        for header in ("Коробка", "Откуда", "Принята", "Карт"):
            assert header in r.text
        assert "B-0020" in r.text
        assert "B1" in r.text  # branch_name из JOIN branches
        assert "2026-09-01 10:12" in r.text  # received_at из shipments
        # htmx-кнопка «Разместить» → GET /placement/select в контейнер панели (td, чтобы свап был валидным HTML)
        assert 'hx-get="/placement/select?box_id=20"' in r.text
        assert 'hx-target="#panel-td-20"' in r.text
        assert "if(event.detail.successful)" in r.text  # строку не прячем при ошибке запроса
        # скрытая строка-контейнер панели под строкой коробки
        assert 'id="panel-row-20"' in r.text
        assert "display:none" in r.text

    async def test_placement_selected_box_renders_panel_inline(self, archive_client, db):
        """?box_id=N (переход с приёмки через HX-Redirect) → панель каскада
        рендерится инлайн: строка скрыта, панель видима, селекты «Секция → Полка»,
        hidden input box_id, данные SHELVES для JS."""
        await archive_client.post("/login", data={"username": "arch1", "password": "test123"})
        await self._make_received_box(db)

        r = await archive_client.get("/placement", params={"box_id": "20"})
        assert r.status_code == 200
        # шапка панели: chip + мета
        assert "📦 B-0020" in r.text
        assert "2 карт" in r.text
        assert "принята 2026-09-01 10:12" in r.text
        # каскад «Секция → Полка» (id уникальны на коробку)
        assert 'id="section-20"' in r.text
        assert 'id="shelf-20"' in r.text
        assert "Секция 1" in r.text
        assert "— Сначала выберите секцию —" in r.text
        assert "var SHELVES =" in r.text  # данные каскада для JS
        assert 'hx-disabled-elt="button[type=\'submit\']"' in r.text  # защита от двойного сабмита
        # скрытый box_id в форме
        assert 'name="box_id"' in r.text
        assert 'value="20"' in r.text
        # форма confirm через htmx — целится в #placement-area
        assert 'hx-post="/placement/confirm"' in r.text
        assert 'hx-target="#placement-area"' in r.text
        # строка скрыта, контейнер панели есть
        assert 'id="row-20"' in r.text
        assert 'id="panel-row-20"' in r.text

    async def test_branch_cannot_access_receive(self, archive_client):
        await archive_client.post("/login", data={"username": "branch1", "password": "test123"})
        r = await archive_client.get("/receive")
        assert r.status_code == 403
