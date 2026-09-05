"""Тесты экрана «Приём возврата» (роль: Филиал) — живой поиск по ФИО.

Задача 5 раунда 2 редизайна контура возврата: POST /receive-return/check
(принимал int card_id — источник «тихого 422» при вводе ФИО) заменён на
GET /receive-return/search — живой htmx-поиск карт по ФИО (подстрока
.lower(), кириллица регистронезависимо, паттерн /requests/new/search).
POST /receive-return/confirm не менялся (обратная совместимость: smoke-E2E
шлёт card_id и ждёт 303 на /my-shipments).
"""
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
        """GET /receive-return отдаёт страницу живого поиска."""
        await _login(receive_client)
        r = await receive_client.get("/receive-return")
        assert r.status_code == 200
        assert "приём" in r.text.lower() or "поиск карты" in r.text.lower()

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


class TestReceiveReturnPage:
    """Задача 5 раунда 2: GET /receive-return — живой поиск по ФИО.

    Кнопка «Проверить» и htmx-форма POST /receive-return/check удалены
    (int card_id давал «тихий 422» при вводе ФИО); вместо них — инпут на
    всю ширину hx-get=/receive-return/search (debounce 350ms) в контейнер
    #scan-result; страница рендерит фрагмент с idle-состоянием.
    """

    async def test_page_has_live_search_no_check_form(self, receive_client):
        """GET /receive-return: label «Поиск карты (ФИО)», инпут живого
        поиска (hx-get=/receive-return/search, hx-trigger c debounce 350ms,
        hx-target=#scan-result), hint; кнопки «Проверить», htmx-формы
        /receive-return/check и форм confirm (name=card_id) на странице
        нет — подтверждение появляется только в строке результата."""
        await _login(receive_client)
        r = await receive_client.get("/receive-return")
        assert r.status_code == 200
        assert "Поиск карты (ФИО)" in r.text
        assert "Результаты обновляются по мере ввода" in r.text
        assert 'hx-get="/receive-return/search"' in r.text
        assert 'hx-trigger="input changed delay:350ms, search"' in r.text
        assert 'hx-target="#scan-result"' in r.text
        assert 'id="scan-result"' in r.text
        # старого сканера с кнопкой и int-поля card_id нет
        assert "Сканер (QR-код или ID карты)" not in r.text
        assert ">Проверить</button>" not in r.text
        assert 'name="card_id"' not in r.text
        assert 'hx-post="/receive-return/check"' not in r.text
        # формы подтверждения на GET-странице нет (только во фрагменте)
        assert 'action="/receive-return/confirm"' not in r.text


class TestReceiveReturnSearch:
    """Задача 5 раунда 2: GET /receive-return/search — живой поиск по ФИО.

    Подстрока .lower() (кириллица, регистронезависимо), как
    /requests/new/search; для найденных карт: статус-бейдж (CARD_STATUS_LABELS),
    назначение (филиал to_branch_id для in_transit с одиночной card-отправкой;
    «—» для box-отправки и карт без активной отправки), receivable (in_transit
    + активная возвратная отправка kind='card'|'box') — для receivable-строк
    обычная форма POST /receive-return/confirm с hidden card_id, остальные —
    статус без кнопки.
    """

    @staticmethod
    async def _seed_card(db, card_id, full_name, status):
        """Прямой посев карты (без активной возвратной отправки)."""
        await db.execute(
            "INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (?, ?, ?)",
            (card_id, full_name, status),
        )
        await db.conn.commit()

    async def test_search_finds_receivable_card_with_button(self, receive_client, db):
        """Поиск по «иванов»: карта в пути с активной card-отправкой → строка
        с ФИО, бейджем «В пути», именем филиала назначения (B1,
        to_branch_id=1) и кнопкой «Подтвердить получение» в ОБЫЧНОЙ форме
        POST /receive-return/confirm (hidden card_id); состояние НЕ меняется
        (превью не двигает статусы)."""
        await _login(receive_client)
        r = await receive_client.get("/receive-return/search", params={"query": "иванов"})
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

    async def test_search_case_insensitive(self, receive_client):
        """Регистронезависимость кириллицы: «ИВАНОВ» и «иванов» находят
        карту «Иванов И.И.» (python-фильтр .lower(), как /requests/new)."""
        await _login(receive_client)
        for q in ("ИВАНОВ", "иванов"):
            r = await receive_client.get("/receive-return/search", params={"query": q})
            assert r.status_code == 200
            assert "Иванов И.И." in r.text
            assert "Подтвердить получение" in r.text

    async def test_search_in_transit_without_shipment_no_button(self, receive_client, db):
        """Карта в пути БЕЗ активной возвратной отправки: строка со статусом
        «В пути» и назначением «—», кнопки подтверждения нет (guard)."""
        await _login(receive_client)
        await self._seed_card(db, 2, "Петров П.П.", "in_transit")
        r = await receive_client.get("/receive-return/search", params={"query": "петров"})
        assert r.status_code == 200
        assert "Петров П.П." in r.text
        assert '<span class="badge badge-in_transit">В пути</span>' in r.text
        assert "<td>—</td>" in r.text
        assert "Подтвердить получение" not in r.text

    async def test_search_not_in_transit_shows_status_no_button(self, receive_client, db):
        """Карта не в пути (placed): строка со статусом «Размещена» и
        назначением «—», без кнопки — состояние показывается строкой
        таблицы, а не флешем-ошибкой (как было в /check)."""
        await _login(receive_client)
        await self._seed_card(db, 3, "Сидоров С.С.", "placed")
        r = await receive_client.get("/receive-return/search", params={"query": "сидоров"})
        assert r.status_code == 200
        assert "Сидоров С.С." in r.text
        assert '<span class="badge badge-placed">Размещена</span>' in r.text
        assert "<td>—</td>" in r.text
        assert "Подтвердить получение" not in r.text
        assert "flash flash-error" not in r.text

    async def test_search_box_shipment_receivable_destination_dash(self, receive_client, db):
        """Карта в активной box-отправке (kind='box' через box_cards):
        приём возможен (карта in_transit — кнопка есть), но «Назначение» =
        «—»: в box-отправке to_branch_id нет (в shipments только
        from_branch_id — отправитель коробки), показывать отправителя как
        назначение — врать (решение раунда 1)."""
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
        r = await receive_client.get("/receive-return/search", params={"query": "николаев"})
        assert r.status_code == 200
        assert "Николаев Н.Н." in r.text
        assert '<span class="badge badge-in_transit">В пути</span>' in r.text
        assert "Подтвердить получение" in r.text
        assert "<td>—</td>" in r.text  # получатель в box-отправке не закодирован
        assert "<td>B1</td>" not in r.text  # from_branch_id не врём как назначение

    async def test_search_mixed_rows_only_receivable_gets_button(self, receive_client, db):
        """Смешанная выдача по подстроке «ов» (Иванов с активной отправкой +
        Петров без): кнопка «Подтвердить получение» ровно одна — только у
        receivable-строки; остальные — статус без кнопки."""
        await _login(receive_client)
        await self._seed_card(db, 2, "Петров П.П.", "in_transit")
        r = await receive_client.get("/receive-return/search", params={"query": "ов"})
        assert r.status_code == 200
        assert "Иванов И.И." in r.text
        assert "Петров П.П." in r.text
        assert r.text.count("Подтвердить получение") == 1

    async def test_search_empty_query_idle_state(self, receive_client):
        """Пустой query: пустое состояние-подсказка, без таблицы и кнопок."""
        await _login(receive_client)
        r = await receive_client.get("/receive-return/search")
        assert r.status_code == 200
        assert "Карта к приёму" not in r.text
        assert "Подтвердить получение" not in r.text

    async def test_search_no_results_empty_state(self, receive_client):
        """Поиск без совпадений: пустое состояние «не найдено», а не голая
        таблица с кнопками."""
        await _login(receive_client)
        r = await receive_client.get(
            "/receive-return/search", params={"query": "Неизвестный Пациент"}
        )
        assert r.status_code == 200
        assert "не найдено" in r.text
        assert "Карта к приёму" not in r.text
        assert "Подтвердить получение" not in r.text

    async def test_confirm_error_redirects_with_flash(self, receive_client, db):
        """Ошибка подтверждения (карта не в in_transit) — не 400-страница:
        303 на /receive-return + одноразовый флеш-error на GET (единый
        флеш-паттерн контура через _render_page сохранён); валидный confirm
        (303 на /my-shipments, smoke-E2E) не задет."""
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
