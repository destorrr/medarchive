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


class TestReceiveReturnCheck:
    """Задача 5 редизайна контура возврата: /receive-return — двухшаговый приём.

    GET-страница — карточка сканера (htmx POST /receive-return/check в
    #scan-result); check — превью карты БЕЗ изменения состояния (таблица
    Карта | Статус | Назначение + кнопка «Подтвердить получение» в обычной
    форме на /receive-return/confirm), ошибки — фрагмент с flash-error.
    """

    @staticmethod
    async def _seed_card(db, card_id, full_name, status):
        """Прямой посев карты для сценариев ошибок check."""
        await db.execute(
            "INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (?, ?, ?)",
            (card_id, full_name, status),
        )
        await db.conn.commit()

    async def test_page_has_scanner_card_and_htmx_target(self, receive_client):
        """GET /receive-return: карточка сканера — label «Сканер (QR-код или
        ID карты)», input name=card_id, кнопка «Проверить» btn-primary, форма
        htmx (hx-post=/receive-return/check, hx-target=#scan-result), контейнер
        #scan-result под формой; формы confirm на странице нет (она появляется
        только во фрагменте после проверки)."""
        await _login(receive_client)
        r = await receive_client.get("/receive-return")
        assert r.status_code == 200
        assert "Сканер (QR-код или ID карты)" in r.text
        assert 'name="card_id"' in r.text
        assert "Проверить" in r.text
        assert 'hx-post="/receive-return/check"' in r.text
        assert 'hx-target="#scan-result"' in r.text
        assert '<div id="scan-result"></div>' in r.text
        assert 'action="/receive-return/confirm"' not in r.text

    async def test_check_ok_preview_card(self, receive_client, db):
        """check для in_transit карты с активной card-отправкой (to_branch_id=1):
        200-фрагмент с ФИО, бейджем «В пути», именем филиала назначения (B1),
        формой на /receive-return/confirm и кнопкой «Подтвердить получение»;
        состояние НЕ меняется (карта in_transit, отправка issued) — превью
        не двигает статусы."""
        await _login(receive_client)
        r = await receive_client.post("/receive-return/check", data={"card_id": "1"})
        assert r.status_code == 200
        assert "Карта к приёму" in r.text
        assert "Иванов И.И." in r.text
        assert '<span class="badge badge-in_transit">В пути</span>' in r.text
        assert "<td>B1</td>" in r.text  # филиал назначения (to_branch_id=1)
        assert "Подтвердить получение" in r.text
        assert 'action="/receive-return/confirm"' in r.text
        assert 'name="card_id" value="1"' in r.text
        assert "flash flash-error" not in r.text
        card = await db.fetch_one("SELECT status FROM cards WHERE id = 1")
        assert card["status"] == "in_transit"
        sh = await db.fetch_one("SELECT status FROM shipments WHERE id = 1")
        assert sh["status"] == "issued"

    async def test_check_no_active_shipment_error(self, receive_client, db):
        """check для карты in_transit БЕЗ активной отправки → 200-фрагмент
        с flash-error и текстом сервиса, без таблицы/кнопки подтверждения."""
        await _login(receive_client)
        await self._seed_card(db, 2, "Петров П.П.", "in_transit")
        r = await receive_client.post("/receive-return/check", data={"card_id": "2"})
        assert r.status_code == 200
        assert "flash flash-error" in r.text
        assert "Не найдена активная отправка" in r.text
        assert "Карта к приёму" not in r.text
        assert "Подтвердить получение" not in r.text

    async def test_check_not_in_transit_error(self, receive_client, db):
        """check для карты не в in_transit (placed) → flash-error с текущим
        статусом (текст как в сервисе receive_return); карта не меняется."""
        await _login(receive_client)
        await self._seed_card(db, 3, "Сидоров С.С.", "placed")
        r = await receive_client.post("/receive-return/check", data={"card_id": "3"})
        assert r.status_code == 200
        assert "flash flash-error" in r.text
        # кавычки статуса Jinja экранирует в HTML (&#39;) — проверяем сегменты
        assert "должна быть в статусе" in r.text
        assert "&#39;in_transit&#39;, сейчас &#39;placed&#39;" in r.text
        assert "Подтвердить получение" not in r.text
        card = await db.fetch_one("SELECT status FROM cards WHERE id = 3")
        assert card["status"] == "placed"

    async def test_check_unknown_card_not_found(self, receive_client):
        """check для несуществующей карты → «Карта не найдена», без кнопки."""
        await _login(receive_client)
        r = await receive_client.post("/receive-return/check", data={"card_id": "9999"})
        assert r.status_code == 200
        assert "flash flash-error" in r.text
        assert "Карта не найдена" in r.text
        assert "Подтвердить получение" not in r.text

    async def test_check_box_shipment_destination_dash(self, receive_client, db):
        """check для карты в активной box-отправке (kind='box' через box_cards):
        превью валидно (карта в пути, кнопка подтверждения есть), но
        «Назначение» = «—»: в box-отправке to_branch_id нет (в shipments только
        from_branch_id — отправитель коробки), показывать отправителя как
        назначение — врать."""
        await _login(receive_client)
        await self._seed_card(db, 4, "Николаев Н.Н.", "in_transit")
        await db.execute(
            "INSERT OR REPLACE INTO boxes (id, box_number, origin_branch_id, "
            "created_by, status) VALUES (20, 'RB-0020', 1, 2, 'in_transit')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO box_cards (id, box_id, card_id) VALUES (20, 20, 4)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO shipments (id, kind, box_id, from_branch_id, "
            "courier_id, issued_by, status) VALUES (20, 'box', 20, 1, 1, 2, 'issued')"
        )
        await db.conn.commit()
        r = await receive_client.post("/receive-return/check", data={"card_id": "4"})
        assert r.status_code == 200
        assert "Николаев Н.Н." in r.text
        assert '<span class="badge badge-in_transit">В пути</span>' in r.text
        assert "Подтвердить получение" in r.text
        assert "<td>—</td>" in r.text  # получатель в box-отправке не закодирован
        assert "<td>B1</td>" not in r.text  # from_branch_id не врём как назначение

    async def test_confirm_error_redirects_with_flash(self, receive_client, db):
        """Ошибка подтверждения (карта не в in_transit) — не 400-страница:
        303 на /receive-return + одноразовый флеш-error на GET (единый
        флеш-паттерн контура); валидный confirm (303 на /my-shipments,
        smoke-E2E) не задет."""
        await _login(receive_client)
        await self._seed_card(db, 3, "Сидоров С.С.", "placed")
        r = await receive_client.post(
            "/receive-return/confirm", data={"card_id": "3"}, follow_redirects=False
        )
        assert r.status_code == 303
        assert "/receive-return" in r.headers["location"]
        g1 = await receive_client.get("/receive-return")
        assert "flash flash-error" in g1.text
        assert "должна быть в статусе" in g1.text
        assert "&#39;in_transit&#39;, сейчас &#39;placed&#39;" in g1.text
        g2 = await receive_client.get("/receive-return")
        assert "должна быть в статусе" not in g2.text
