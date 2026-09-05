import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def req_client(test_config, db):
    from medarchive.main import create_app
    app = create_app(test_config)
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (2, 'B2', 'B2')")
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) VALUES (1, 'branch1', ?, 'branch', 1)", (auth.hash_password("test123"),))
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) VALUES (2, 'arch1', ?, 'archive')", (auth.hash_password("test123"),))
    # Админ без филиала: создание заявки для него должно падать с ошибкой, а не 500.
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) VALUES (3, 'boss1', ?, 'admin')", (auth.hash_password("test123"),))
    await db.execute("INSERT OR REPLACE INTO couriers (id, name) VALUES (1, 'K1')")
    await db.execute("INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (1, 'Иванов И.И.', 'placed')")
    await db.execute("INSERT OR REPLACE INTO boxes (id, box_number, origin_branch_id, created_by, status) VALUES (10, 'B-0010', 1, 1, 'placed')")
    await db.execute("INSERT OR REPLACE INTO box_cards (id, box_id, card_id) VALUES (1, 10, 1)")
    await db.conn.commit()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login(client, role="branch1"):
    await client.post("/login", data={"username": role, "password": "test123"})


class TestRequestNew:
    async def test_request_new_page_renders(self, req_client):
        await _login(req_client)
        r = await req_client.get("/requests/new")
        assert r.status_code == 200
        assert "заявк" in r.text.lower() or "поиск" in r.text.lower()

    async def test_auto_branch_hint_on_search_page(self, req_client):
        """GET /requests/new: подсказка про авто-филиал с именем филиала
        пользователя; селекта филиала (target_branch_id) на странице нет."""
        await _login(req_client)
        r = await req_client.get("/requests/new")
        assert r.status_code == 200
        assert "Филиал возврата" in r.text
        assert "B1" in r.text  # имя филиала пользователя branch1 (branch_id=1)
        assert "(ваш, из учётной записи)" in r.text
        assert "Поле выбора филиала убрано" in r.text
        assert "target_branch_id" not in r.text

    async def test_create_request_uses_user_branch(self, req_client, db):
        """POST create БЕЗ target_branch_id (форма селект не шлёт): заявка
        создаётся с target_branch_id == user.branch_id; 303 на /requests/my."""
        await _login(req_client)
        # Поиск карты: результат — русская подпись статуса, бейдж, кнопка «Запросить».
        r = await req_client.post("/requests/new", data={"search_name": "Иванов И.И."})
        assert r.status_code == 200
        assert "Иванов И.И." in r.text
        assert "Размещена" in r.text  # русская подпись placed через CARD_STATUS_LABELS
        assert "Запросить" in r.text
        assert "target_branch_id" not in r.text
        # Создание заявки без поля филиала в форме.
        r = await req_client.post("/requests/new", data={"card_id": 1}, follow_redirects=False)
        assert r.status_code == 303
        assert "/requests/my" in r.headers["location"]
        row = await db.fetch_one("SELECT * FROM requests WHERE card_id = 1")
        assert row["target_branch_id"] == 1  # филиал пользователя branch1

    async def test_create_shows_flash_on_my_requests(self, req_client):
        """После создания GET /requests/my показывает одноразовый флеш
        (cookie-флеш работает end-to-end); повторный GET — уже без флеша."""
        await _login(req_client)
        await req_client.post("/requests/new", data={"card_id": 1}, follow_redirects=False)
        r = await req_client.get("/requests/my")
        assert r.status_code == 200
        assert "Заявка на возврат создана" in r.text
        assert "B1" in r.text  # имя филиала в сообщении
        r2 = await req_client.get("/requests/my")
        assert "Заявка на возврат создана" not in r2.text

    async def test_create_without_branch_returns_400(self, req_client, db):
        """Пользователь без филиала (admin boss1) при создании → не 500,
        а 400 с сообщением «не привязан филиал»; заявка не создаётся."""
        await _login(req_client, "boss1")
        r = await req_client.post("/requests/new", data={"card_id": 1}, follow_redirects=False)
        assert r.status_code == 400
        assert "не привязан филиал" in r.text
        cnt = await db.fetch_one("SELECT COUNT(*) as cnt FROM requests")
        assert cnt["cnt"] == 0

    async def test_search_no_results_empty_state(self, req_client):
        """Поиск без совпадений: пустое состояние, а не голая таблица."""
        await _login(req_client)
        r = await req_client.post("/requests/new", data={"search_name": "Неизвестный Пациент"})
        assert r.status_code == 200
        assert "не найдено" in r.text

    async def test_my_requests_page(self, req_client, db):
        await _login(req_client)
        # Create a request first
        await req_client.post("/requests/new", data={"card_id": 1})
        r = await req_client.get("/requests/my")
        assert r.status_code == 200


class TestRequestsMy:
    """Задача 3 редизайна контура возврата: /requests/my — русские статусы-бейджи,
    формат даты «ДД.ММ.ГГГГ ЧЧ:ММ», кнопка «Отменить» только для активных,
    одноразовые флеши отмены, пустое состояние."""

    @staticmethod
    async def _seed_request(db, *, status="new", requested_by=1, created_at="2026-09-05 16:48:13"):
        """Прямой посев заявки с фиксированным created_at (сервис пишет только
        'new' и текущее время — для проверки рендера нужны разные статусы/даты)."""
        await db.execute(
            "INSERT INTO requests (card_id, requested_by, target_branch_id, "
            "target_department, created_at, status) VALUES (1, ?, 1, 0, ?, ?)",
            (requested_by, created_at, status),
        )
        await db.conn.commit()

    async def test_status_shown_as_russian_label_not_raw(self, req_client, db):
        """Статус заявки на странице — русская подпись «Новая» с бейджем
        badge-new, а не сырое значение 'new' в ячейке статуса."""
        await _login(req_client)
        await self._seed_request(db, status="new")
        r = await req_client.get("/requests/my")
        assert r.status_code == 200
        assert '<span class="badge badge-new">Новая</span>' in r.text
        assert "<td>new</td>" not in r.text

    async def test_created_at_formatted_dmy_hm(self, req_client, db):
        """Дата создания — «ДД.ММ.ГГГГ ЧЧ:ММ»; сырой формат хранения
        «YYYY-MM-DD HH:MM:SS» в таблице не показывается."""
        await _login(req_client)
        await self._seed_request(db, created_at="2026-09-05 16:48:13")
        r = await req_client.get("/requests/my")
        assert r.status_code == 200
        assert '<td class="date muted">05.09.2026 16:48</td>' in r.text
        assert "2026-09-05 16:48:13" not in r.text

    async def test_cancel_button_only_for_active_statuses(self, req_client, db):
        """Кнопка «Отменить» — только у строк new/in_progress; у completed
        и cancelled ячейка действия пустая (кнопок ровно две из четырёх)."""
        await _login(req_client)
        await self._seed_request(db, status="new", created_at="2026-09-05 16:48:13")
        await self._seed_request(db, status="in_progress", created_at="2026-09-05 16:30:00")
        await self._seed_request(db, status="completed", created_at="2026-09-05 16:00:00")
        await self._seed_request(db, status="cancelled", created_at="2026-09-05 15:30:00")
        r = await req_client.get("/requests/my")
        assert r.status_code == 200
        # Кнопок отмены ровно две (new + in_progress); в CSS base.html класс
        # .btn-danger-ghost встречается дважды (правило + :hover), поэтому
        # считаем именно разметку кнопки.
        assert r.text.count('btn-danger-ghost">Отменить') == 2
        assert '<span class="badge badge-completed">Выполнена</span>' in r.text
        assert '<span class="badge badge-cancelled">Отменена</span>' in r.text

    async def test_cancel_success_redirects_with_flash_once(self, req_client, db):
        """Отмена своей активной заявки end-to-end: 303 на /requests/my +
        флеш «Заявка отменена.»; первый GET показывает флеш, повторный — нет
        (cookie-флеш одноразовый). Статус в БД — cancelled."""
        await _login(req_client)
        await req_client.post("/requests/new", data={"card_id": 1}, follow_redirects=False)
        row = await db.fetch_one("SELECT id FROM requests ORDER BY id DESC LIMIT 1")
        r = await req_client.post(
            "/requests/cancel", data={"request_id": str(row["id"])}, follow_redirects=False
        )
        assert r.status_code == 303
        assert "/requests/my" in r.headers["location"]
        upd = await db.fetch_one("SELECT status FROM requests WHERE id = ?", (row["id"],))
        assert upd["status"] == "cancelled"
        g1 = await req_client.get("/requests/my")
        assert "Заявка отменена." in g1.text
        g2 = await req_client.get("/requests/my")
        assert "Заявка отменена." not in g2.text

    async def test_cancel_already_cancelled_error_flash(self, req_client, db):
        """Повторная отмена (статус cancelled) — не 500: 303 + флеш-error
        с текстом сервиса; флеш одноразовый."""
        await _login(req_client)
        await self._seed_request(db, status="cancelled")
        row = await db.fetch_one("SELECT id FROM requests ORDER BY id DESC LIMIT 1")
        r = await req_client.post(
            "/requests/cancel", data={"request_id": str(row["id"])}, follow_redirects=False
        )
        assert r.status_code == 303
        assert "/requests/my" in r.headers["location"]
        g1 = await req_client.get("/requests/my")
        assert "flash flash-error" in g1.text
        assert "Нельзя отменить заявку в статусе" in g1.text
        g2 = await req_client.get("/requests/my")
        assert "Нельзя отменить заявку в статусе" not in g2.text

    async def test_cancel_foreign_request_error_flash(self, req_client, db):
        """Чужая заявка (requested_by != текущий пользователь) — не 500:
        303 + флеш-error «Нельзя отменить чужую заявку»; статус не меняется."""
        await _login(req_client)
        await self._seed_request(db, status="new", requested_by=2)  # arch1, не владелец
        row = await db.fetch_one("SELECT id FROM requests WHERE requested_by = 2 ORDER BY id DESC LIMIT 1")
        r = await req_client.post(
            "/requests/cancel", data={"request_id": str(row["id"])}, follow_redirects=False
        )
        assert r.status_code == 303
        g1 = await req_client.get("/requests/my")
        assert "flash flash-error" in g1.text
        assert "Нельзя отменить чужую заявку" in g1.text
        upd = await db.fetch_one("SELECT status FROM requests WHERE id = ?", (row["id"],))
        assert upd["status"] == "new"

    async def test_cancel_missing_request_error_flash(self, req_client):
        """Невалидный id (заявки нет) — не 500: 303 + флеш-error «Заявка не найдена»."""
        await _login(req_client)
        r = await req_client.post(
            "/requests/cancel", data={"request_id": "999999"}, follow_redirects=False
        )
        assert r.status_code == 303
        g1 = await req_client.get("/requests/my")
        assert "flash flash-error" in g1.text
        assert "Заявка не найдена" in g1.text

    async def test_my_requests_empty_state(self, req_client):
        """Заявок нет — пустое состояние «Нет заявок» со ссылкой на создание."""
        await _login(req_client)
        r = await req_client.get("/requests/my")
        assert r.status_code == 200
        assert "Нет заявок" in r.text
        assert "Создать новую" in r.text
        assert 'href="/requests/new"' in r.text


class TestRequestQueue:
    async def test_queue_page_renders(self, req_client):
        await _login(req_client, "arch1")
        r = await req_client.get("/requests")
        assert r.status_code == 200

    async def test_fulfill_request(self, req_client, db):
        # Branch creates request
        await _login(req_client, "branch1")
        await req_client.post("/requests/new", data={"card_id": 1})
        await req_client.get("/logout")

        # Archive fulfills
        await _login(req_client, "arch1")
        r = await req_client.get("/requests")
        assert r.status_code == 200
        # Find request id
        req_row = await db.fetch_one("SELECT id FROM requests ORDER BY id DESC LIMIT 1")
        r = await req_client.post("/requests/fulfill", data={"request_id": str(req_row["id"]), "action": "single"}, follow_redirects=False)
        assert r.status_code == 303

    async def test_branch_cannot_access_queue(self, req_client):
        await _login(req_client, "branch1")
        r = await req_client.get("/requests")
        assert r.status_code == 403


class TestRequestQueueRedesign:
    """Задача 4 редизайна контура возврата: очередь /requests — колонка
    «Филиал» (имя филиала-получателя, JOIN branches в get_queue), русские
    статусы-бейджи (REQUEST_STATUS_LABELS), дата «ДД.ММ.ГГГГ ЧЧ:ММ»,
    селект курьера + «Выполнить» в строке; вместо сырого user#<id> — филиал;
    выполнение → 303 + одноразовый флеш; ошибки → 303 + флеш-error;
    пустое состояние."""

    @staticmethod
    async def _seed_request(
        db, *, status="new", target_branch_id=2, requested_by=1,
        created_at="2026-09-05 16:48:13",
    ):
        """Прямой посев заявки (статусы/филиал/дата для проверки рендера)."""
        await db.execute(
            "INSERT INTO requests (card_id, requested_by, target_branch_id, "
            "target_department, created_at, status) VALUES (1, ?, ?, 0, ?, ?)",
            (requested_by, target_branch_id, created_at, status),
        )
        await db.conn.commit()

    async def test_queue_row_shows_branch_status_date_courier(self, req_client, db):
        """Строка очереди: имя филиала-получателя (B2/B1), русский статус-
        бейдж («Новая»/«В работе»), дата «ДД.ММ.ГГГГ ЧЧ:ММ», селект курьера
        (option курьера) и кнопка «Выполнить»; сырых user#<id>, 'new' и
        «YYYY-MM-DD HH:MM:SS» в таблице нет."""
        await _login(req_client, "arch1")
        await self._seed_request(
            db, status="new", target_branch_id=2, created_at="2026-09-05 16:48:13"
        )
        await self._seed_request(
            db, status="in_progress", target_branch_id=1, created_at="2026-09-05 15:02:00"
        )
        r = await req_client.get("/requests")
        assert r.status_code == 200
        # заголовки колонок: Карта | Филиал | Статус | Дата | действие
        assert "<th>Карта</th>" in r.text
        assert "<th>Филиал</th>" in r.text
        assert "<th>Статус</th>" in r.text
        assert "<th>Дата</th>" in r.text
        # имя филиала-получателя в строке (не user#<id>)
        assert "<td>B2</td>" in r.text
        assert "<td>B1</td>" in r.text
        assert "user#" not in r.text
        # русские статусы-бейджи, а не сырые значения
        assert '<span class="badge badge-new">Новая</span>' in r.text
        assert '<span class="badge badge-in_progress">В работе</span>' in r.text
        assert "<td>new</td>" not in r.text
        # дата «ДД.ММ.ГГГГ ЧЧ:ММ»; сырой формат хранения не показывается
        assert '<td class="date muted">05.09.2026 16:48</td>' in r.text
        assert "2026-09-05 16:48:13" not in r.text
        # действие: селект курьера (option курьера K1, «Без отправки» = 0)
        # + кнопка «Выполнить» в каждой строке
        assert 'name="courier_id"' in r.text
        assert '<option value="1">K1</option>' in r.text
        assert '<option value="0">Без отправки</option>' in r.text
        assert r.text.count(">Выполнить</button>") == 2

    async def test_queue_empty_state(self, req_client):
        """Заявок нет — пустое состояние «Нет активных заявок»."""
        await _login(req_client, "arch1")
        r = await req_client.get("/requests")
        assert r.status_code == 200
        assert "Нет активных заявок" in r.text

    async def test_fulfill_success_303_flash_once(self, req_client, db):
        """Выполнение заявки без курьера (courier_id=0) end-to-end: 303 на
        /requests + флеш «Заявка выполнена» (cookie одноразовый — первый GET
        показывает, повторный нет); в БД статус completed."""
        await _login(req_client, "branch1")
        await req_client.post("/requests/new", data={"card_id": 1}, follow_redirects=False)
        req_row = await db.fetch_one("SELECT id FROM requests ORDER BY id DESC LIMIT 1")
        await req_client.get("/logout")
        await _login(req_client, "arch1")
        r = await req_client.post(
            "/requests/fulfill",
            data={"request_id": str(req_row["id"]), "action": "single", "courier_id": "0"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/requests" in r.headers["location"]
        g1 = await req_client.get("/requests")
        assert "flash flash-success" in g1.text
        assert "Заявка выполнена" in g1.text
        g2 = await req_client.get("/requests")
        assert "Заявка выполнена" not in g2.text
        upd = await db.fetch_one("SELECT status FROM requests WHERE id = ?", (req_row["id"],))
        assert upd["status"] == "completed"

    async def test_fulfill_with_courier_creates_return_shipment(self, req_client, db):
        """Курьер выбран (courier_id>0): заявка выполнена, создаётся возвратная
        отправка (запись в shipments с курьером), карта уходит in_transit;
        303 + флеш «…и передана курьеру»."""
        await _login(req_client, "branch1")
        await req_client.post("/requests/new", data={"card_id": 1}, follow_redirects=False)
        req_row = await db.fetch_one("SELECT id FROM requests ORDER BY id DESC LIMIT 1")
        await req_client.get("/logout")
        await _login(req_client, "arch1")
        r = await req_client.post(
            "/requests/fulfill",
            data={"request_id": str(req_row["id"]), "action": "single", "courier_id": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        sh = await db.fetch_one("SELECT * FROM shipments ORDER BY id DESC LIMIT 1")
        assert sh is not None
        assert sh["courier_id"] == 1
        card = await db.fetch_one("SELECT status FROM cards WHERE id = 1")
        assert card["status"] == "in_transit"
        g1 = await req_client.get("/requests")
        assert "передана курьеру" in g1.text

    async def test_fulfill_missing_request_error_flash(self, req_client):
        """Невалидный request_id — не 500: 303 + флеш-error «Заявка не найдена»."""
        await _login(req_client, "arch1")
        r = await req_client.post(
            "/requests/fulfill",
            data={"request_id": "999999", "action": "single"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/requests" in r.headers["location"]
        g1 = await req_client.get("/requests")
        assert "flash flash-error" in g1.text
        assert "Заявка не найдена" in g1.text

    async def test_fulfill_completed_request_error_flash(self, req_client, db):
        """Уже выполненная заявка — не 500: 303 + флеш-error с текстом сервиса
        («Нельзя выполнить заявку в статусе …»)."""
        await _login(req_client, "arch1")
        await self._seed_request(db, status="completed", target_branch_id=1)
        req_row = await db.fetch_one(
            "SELECT id FROM requests WHERE status = 'completed' ORDER BY id DESC LIMIT 1"
        )
        r = await req_client.post(
            "/requests/fulfill",
            data={"request_id": str(req_row["id"]), "action": "single", "courier_id": "0"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        g1 = await req_client.get("/requests")
        assert "flash flash-error" in g1.text
        assert "Нельзя выполнить заявку в статусе" in g1.text
