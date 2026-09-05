"""Роутер /search: GET с query, htmx-фрагмент /search/results, legacy POST.

Покрывает решения дизайна /search (2026-09-05), Задача 2:
- GET /search — страница с полем поиска, опциональный ?query= (нет-JS фолбэк);
- GET /search/results?query= — фрагмент только с результатами (без полной страницы);
- POST /search — legacy 303-редирект на GET /search?query=…;
- русские подписи статусов + классы бейджей, место «Секция А / Полка 1»;
- регистронезависимость кириллицы на уровне роутера;
- серверная экранированная подсветка <mark> (без неэкранированного HTML).
"""
import re
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from markupsafe import Markup


@pytest.fixture
async def search_client(test_config, db):
    """Ветка B1 + пользователи (branch/viewer/archive) + секция/полки +
    карта «Иванов Иван Иванович» (placed) в коробке B-0010 на «Полке 1»."""
    from medarchive.main import create_app
    app = create_app(test_config)

    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    pwd = auth.hash_password("test123")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'branch1', ?, 'branch', 1)",
        (pwd,),
    )
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role) "
        "VALUES (2, 'viewer1', ?, 'viewer')",
        (pwd,),
    )
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role) "
        "VALUES (3, 'arch1', ?, 'archive')",
        (pwd,),
    )
    # Раздел/полки (как в сервис-тестах поиска).
    await db.execute(
        "INSERT OR REPLACE INTO archive_sections (id, name) VALUES (1, 'Секция А')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO archive_shelves (id, section_id, name, qr_code) "
        "VALUES (1, 1, 'Полка 1', 'A-1')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO archive_shelves (id, section_id, name, qr_code) "
        "VALUES (2, 1, 'Полка 2', 'A-2')"
    )
    # Карта «размещена» в коробке B-0010, коробка размещена на «Полке 1».
    await db.execute(
        "INSERT OR REPLACE INTO cards (id, full_name, status) "
        "VALUES (1, 'Иванов Иван Иванович', 'placed')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO boxes (id, box_number, origin_branch_id, created_by, status) "
        "VALUES (10, 'B-0010', 1, 1, 'placed')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO box_cards (id, box_id, card_id) VALUES (1001, 10, 1)"
    )
    await db.execute(
        "INSERT OR REPLACE INTO box_placements (id, box_id, shelf_id, placed_at) "
        "VALUES (10, 10, 1, '2026-01-01 10:00:00')"
    )
    await db.conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login(client, username="viewer1"):
    await client.post("/login", data={"username": username, "password": "test123"})


def _strip_tags(html: str) -> str:
    """Текст страницы без разметки (для проверок, не зависящих от тегов)."""
    return re.sub(r"<[^>]+>", "", html)


class TestSearchPage:
    async def test_page_renders_with_search_field(self, search_client):
        """GET /search → 200, есть поле поиска name=query."""
        await _login(search_client)
        r = await search_client.get("/search")
        assert r.status_code == 200
        assert "Поиск" in r.text
        assert 'name="query"' in r.text

    async def test_search_by_name_finds_card_with_label_and_place(self, search_client):
        """GET /search?query=иванов (строчными!) → карта, бейдж «Размещена»,
        место «Секция А / Полка 1», совпадение подсвечено <mark>."""
        await _login(search_client)
        r = await search_client.get("/search", params={"query": "иванов"})
        assert r.status_code == 200
        text = _strip_tags(r.text)
        assert "Иванов Иван Иванович" in text
        assert "Размещена" in r.text
        assert "Секция А / Полка 1" in r.text
        assert "<mark>Иванов</mark>" in r.text

    async def test_archive_role_can_search(self, search_client):
        """Поиск доступен роли archive (тот же контент, что у viewer)."""
        await _login(search_client, username="arch1")
        r = await search_client.get("/search", params={"query": "иванов"})
        assert r.status_code == 200
        assert "Иванов Иван Иванович" in _strip_tags(r.text)

    async def test_results_fragment_has_no_full_page(self, search_client):
        """GET /search/results?query=… → фрагмент: результаты есть, полной
        страницы нет (ни <html, ни сайдбара «МедАрхив»/«Выход»), есть фрагментные
        секции."""
        await _login(search_client)
        r = await search_client.get("/search/results", params={"query": "иванов"})
        assert r.status_code == 200
        assert "Иванов Иван Иванович" in _strip_tags(r.text)
        assert "<html" not in r.text.lower()
        assert "МедАрхив" not in r.text
        assert "Выход" not in r.text
        assert 'class="ci"' in r.text
        assert "Карты (1)" in r.text

    async def test_box_query_shows_boxes_hides_cards(self, search_client):
        """GET /search?query=B-0010 → секция коробок (номер, подпись «карт»),
        секция карт скрыта (нет ФИО и заголовка «Карты»)."""
        await _login(search_client)
        r = await search_client.get("/search", params={"query": "B-0010"})
        assert r.status_code == 200
        assert "B-0010" in r.text
        assert "Коробки (1)" in r.text
        assert "карт" in r.text
        assert "Иванов Иван Иванович" not in r.text
        assert "Карты (" not in r.text

    async def test_no_matches_shows_empty_state(self, search_client):
        """Запрос без совпадений → «ничего не найдено»."""
        await _login(search_client)
        r = await search_client.get("/search", params={"query": "zzz-не-существует"})
        assert r.status_code == 200
        assert "ничего не найдено" in r.text
        assert "Иванов Иван Иванович" not in r.text

    async def test_card_in_transit_box_shows_box_number_without_place(self, search_client, db):
        """Карта в коробке без размещения (в пути): рендер «в коробке B-…» без места."""
        await db.execute(
            "INSERT OR REPLACE INTO cards (id, full_name, status) "
            "VALUES (2, 'Транзитов Тарас Тарасович', 'in_transit')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO boxes (id, box_number, origin_branch_id, created_by, status) "
            "VALUES (11, 'B-0011', 1, 1, 'in_transit')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO box_cards (id, box_id, card_id) VALUES (1002, 11, 2)"
        )
        await db.conn.commit()

        await _login(search_client)
        r = await search_client.get("/search", params={"query": "транзитов"})
        assert r.status_code == 200
        assert "в коробке B-0011" in r.text
        assert "Секция А" not in _strip_tags(r.text).replace("Секция А / Полка 1", "")

    async def test_card_without_box_renders_no_active_box(self, search_client, db):
        """Карта без активной коробки → box_number None, рендер «нет активной коробки»."""
        await db.execute(
            "INSERT OR REPLACE INTO cards (id, full_name, status) "
            "VALUES (3, 'Безкоробков Борис Борисович', 'in_branch')"
        )
        await db.conn.commit()

        await _login(search_client)
        r = await search_client.get("/search", params={"query": "безкоробков"})
        assert r.status_code == 200
        assert "нет активной коробки" in r.text

    async def test_name_with_html_is_escaped(self, search_client, db):
        """Значения из БД экранируются: подсветка <mark> и текст — без сырого HTML."""
        await db.execute(
            "INSERT OR REPLACE INTO cards (id, full_name, status) "
            "VALUES (4, 'Смирнов <script>alert(1)</script>', 'placed')"
        )
        await db.conn.commit()

        await _login(search_client)
        r = await search_client.get("/search", params={"query": "смирнов"})
        assert r.status_code == 200
        assert "<mark>Смирнов</mark>" in r.text
        assert "&lt;script&gt;" in r.text
        assert "<script>alert" not in r.text
        assert "Смирнов" in _strip_tags(r.text)


class TestSearchPostRedirect:
    async def test_post_redirects_to_get_with_query(self, search_client):
        """POST /search (legacy form) → 303 на /search?query=… (URL-encoded)."""
        await _login(search_client)
        r = await search_client.post("/search", data={"query": "иванов"})
        assert r.status_code == 303
        assert r.headers["location"] == "/search?query=" + quote("иванов")


class TestHighlightHelper:
    def test_highlight_first_case_insensitive_match(self):
        """helper: первое регистронезависимое вхождение оборачивается в <mark>."""
        from medarchive.routers.dashboard import highlight_match
        out = highlight_match("Иванов Иван Иванович", "иванов")
        assert isinstance(out, Markup)
        assert str(out) == "<mark>Иванов</mark> Иван Иванович"

    def test_highlight_escapes_non_matching_html(self):
        """helper: без совпадения текст экранируется целиком."""
        from medarchive.routers.dashboard import highlight_match
        out = highlight_match("<script>alert(1)</script>", "zzz")
        assert "&lt;script&gt;" in str(out)
        assert "<script>" not in str(out)

    def test_highlight_empty_query_returns_escaped_text(self):
        from medarchive.routers.dashboard import highlight_match
        assert str(highlight_match("<b>x</b>", "")) == "&lt;b&gt;x&lt;/b&gt;"
