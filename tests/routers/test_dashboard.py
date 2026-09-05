import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(test_config, db):
    from medarchive.main import create_app
    app = create_app(test_config)

    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

    # Seed branches
    await db.execute(
        "INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'Test Branch', 'TB')"
    )
    # Seed users for all roles. full_name задан только branch_user
    # (ФИО из users.full_name — раунд 2): у остальных пусто → профиль
    # сайдбара показывает username и инициал из него (fallback).
    users = [
        ("branch_user", "branch", 1, "Иванова П.С."),
        ("archive_user", "archive", None, ""),
        ("viewer_user", "viewer", None, ""),
        ("admin_user", "admin", None, ""),
    ]
    for username, role, branch_id, full_name in users:
        password_hash = auth.hash_password("test123")
        await db.execute(
            "INSERT OR REPLACE INTO users (username, password_hash, role, branch_id, full_name) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, role, branch_id, full_name),
        )
    await db.conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestDashboard:
    async def test_dashboard_accessible_for_branch(self, client):
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "медархив" in r.text.lower()

    async def test_dashboard_accessible_for_archive(self, client):
        await client.post("/login", data={"username": "archive_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "медархив" in r.text.lower()

    async def test_dashboard_accessible_for_viewer(self, client):
        await client.post("/login", data={"username": "viewer_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200

    async def test_dashboard_accessible_for_admin(self, client):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200

    async def test_branch_sees_packing_menu(self, client):
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert "упаковка" in r.text.lower()

    async def test_archive_sees_receive_menu(self, client):
        await client.post("/login", data={"username": "archive_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert "приёмка" in r.text.lower()

    async def test_redirects_to_login_when_unauthenticated(self, client):
        r = await client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 303

    async def test_dashboard_shows_stats(self, client):
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "Карт всего" in r.text


    async def test_dashboard_shows_recent_movements(self, client, db):
        # Seed system user for FK
        await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) VALUES (0, 'sys', 'h', 'admin')")
        await db.execute("INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (99, 'Пациент Дашборд', 'placed')")
        await db.conn.commit()
        from medarchive.services.movement_service import MovementService
        ms = MovementService(db)
        await ms.log_movement(99, "placed", None, "shelf Полка 1", user_id=0)

        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "Пациент Дашборд" in r.text


class TestSidebarProfile:
    """Сайдбар v5 (макет return_flow_redesign_mockup_v5.html, раунд 2):
    логотип без иконки, подпись-группа роли, меню с подсветкой активного
    пункта и счётчиком очереди у Архива, профиль внизу (аватар-инициалы,
    ФИО, филиал, пилюля роли)."""

    async def test_branch_user_profile_full(self, client):
        """Роль branch с full_name: ФИО, инициалы «ИП» в аватаре, филиал,
        пилюля «Филиал»; логотип «МедАрхив/учёт медкарт» без эмодзи-иконки."""
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        # Логотип: текст + подпись, без эмодзи/иконки (в пунктах меню эмодзи допустимы).
        assert '<div class="brand"><b>МедАрхив</b><span>учёт медкарт</span></div>' in r.text
        # Профиль: аватар-инициалы из ФИО, строка ФИО, строка филиала, пилюля роли.
        assert 'class="avatar">ИП</div>' in r.text
        assert '<div class="name">Иванова П.С.</div>' in r.text
        assert '<div class="branch">Test Branch</div>' in r.text
        assert 'class="role-pill">Филиал</div>' in r.text

    async def test_user_without_full_name_falls_back_to_username(self, client):
        """full_name пуст → строка имени = username, аватар = первая буква username
        в верхнем регистре; роль без филиала → строка филиала = подпись роли."""
        await client.post("/login", data={"username": "admin_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert '<div class="name">admin_user</div>' in r.text
        assert 'class="avatar">A</div>' in r.text
        assert '<div class="branch">Администратор</div>' in r.text
        assert 'class="role-pill">Администратор</div>' in r.text

    async def test_archive_counter_on_queue_item(self, client, db):
        """Архив с активной заявкой (new/in_progress): пункт «Очередь заявок»
        на странице /requests активен и несёт красный счётчик «1»."""
        await db.execute(
            "INSERT OR REPLACE INTO cards (id, full_name, status) "
            "VALUES (500, 'Пациент Очередь', 'in_archive')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO requests (id, card_id, requested_by, "
            "target_branch_id, status) VALUES (1, 500, 1, 1, 'new')"
        )
        await db.conn.commit()
        await client.post("/login", data={"username": "archive_user", "password": "test123"})
        r = await client.get("/requests")
        assert r.status_code == 200
        assert 'href="/requests" class="active">📋 Очередь заявок<span class="cnt">1</span>' in r.text

    async def test_archive_no_badge_without_active_requests(self, client):
        """Нет активных заявок → счётчик скрыт (бейдж не выводится)."""
        await client.post("/login", data={"username": "archive_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "Очередь заявок" in r.text
        assert 'class="cnt"' not in r.text

    async def test_profile_block_after_nav(self, client):
        """Профиль внизу сайдбара: блок .profile в DOM идёт после nav."""
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert r.text.index('<div class="profile">') > r.text.index("<nav")

    async def test_requests_my_highlights_requests_new_item(self, client):
        """На /requests/my (мои заявки) активен пункт «Заявки на возврат»
        (/requests/new) — страницы /requests/my и /requests/new — один пункт меню."""
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/requests/my")
        assert r.status_code == 200
        assert 'href="/requests/new" class="active"' in r.text


class _FakeCookies:
    def __init__(self, values):
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)


class _FakeRequest:
    def __init__(self, cookies):
        self.cookies = _FakeCookies(cookies)


class TestFlashHelpers:
    """Флеш-хелперы: round-trip с кириллицей, whitelist kind, мусорный cookie."""

    def test_flash_redirect_roundtrip_cyrillic(self):
        """Русский текст флеша переживает запись в cookie и чтение.

        RED-условие: в _flash_redirect сырая кириллица падала в set_cookie
        с UnicodeEncodeError (latin-1); после quote() — round-trip чистый.
        """
        from medarchive.routers.dashboard import _flash_redirect, _pop_flash

        text = "Заявка на возврат создана. Карта будет доставлена в «Филиал_2»."
        resp = _flash_redirect("/requests/my", "success", text)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/requests/my"
        raw = resp.headers.get("set-cookie", "")
        assert "ma_flash=" in raw
        # значение cookie в заголовке — ASCII (иначе latin-1-кодирование уже упало бы)
        value = raw.split("ma_flash=", 1)[1].split(";", 1)[0]
        value.encode("latin-1")  # не должно бросить UnicodeEncodeError
        flash = _pop_flash(_FakeRequest({"ma_flash": value}))
        assert flash == {"type": "success", "text": text}

    def test_pop_flash_whitelist_kind(self):
        """kind вне whitelist (success|error) игнорируется — клиент не подставит класс."""
        from urllib.parse import quote
        from medarchive.routers.dashboard import _pop_flash

        req = _FakeRequest({"ma_flash": quote("hack|<script>alert(1)</script>", safe="|")})
        assert _pop_flash(req) is None

    def test_pop_flash_garbage_cookie(self):
        """Мусорный cookie без разделителя '|' → None (без падения)."""
        from urllib.parse import quote
        from medarchive.routers.dashboard import _pop_flash

        req = _FakeRequest({"ma_flash": quote("no-separator-value")})
        assert _pop_flash(req) is None
        assert _pop_flash(_FakeRequest({})) is None
