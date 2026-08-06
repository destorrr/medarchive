import pytest
from medarchive.services.search_service import SearchService


@pytest.fixture
async def search_service(db):
    return SearchService(db)


async def _seed(db):
    """Базовые данные: 2 карты, 2 коробки, карта в коробке на полке секции."""
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'u1', 'h', 'branch', 1)"
    )
    await db.execute(
        "INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (1, 'Сидоров Сидор', 'placed')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (2, 'Сидорова Анна', 'placed')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO boxes (id, box_number, origin_branch_id, created_by, status) "
        "VALUES (10, 'B-0010', 1, 1, 'placed')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO boxes (id, box_number, origin_branch_id, created_by, status) "
        "VALUES (11, 'B-0011', 1, 1, 'placed')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO box_cards (id, box_id, card_id) VALUES (1, 10, 1)"
    )
    await db.execute(
        "INSERT OR REPLACE INTO archive_sections (id, name) VALUES (1, 'Секция А')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO archive_shelves (id, section_id, name, qr_code) "
        "VALUES (1, 1, 'Полка 1', 'A-1')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO box_placements (id, box_id, shelf_id) VALUES (1, 10, 1)"
    )
    await db.conn.commit()


class TestSearchCards:
    async def test_search_cards_finds_by_substring(self, search_service, db):
        await _seed(db)
        results = await search_service.search_cards("Сидор")
        assert len(results) == 2
        names = {r["full_name"] for r in results}
        assert names == {"Сидоров Сидор", "Сидорова Анна"}

    async def test_search_cards_returns_location(self, search_service, db):
        await _seed(db)
        results = await search_service.search_cards("Сидоров Сидор")
        assert len(results) == 1
        assert results[0]["box_number"] == "B-0010"
        assert results[0]["shelf_name"] == "Полка 1"
        assert results[0]["section_name"] == "Секция А"


class TestSearchBoxes:
    async def test_search_boxes_by_number(self, search_service, db):
        await _seed(db)
        results = await search_service.search_boxes("B-001")
        assert len(results) == 2
        numbers = {r["box_number"] for r in results}
        assert numbers == {"B-0010", "B-0011"}

    async def test_search_boxes_returns_card_count(self, search_service, db):
        await _seed(db)
        results = await search_service.search_boxes("B-0010")
        assert len(results) == 1
        assert results[0]["card_count"] == 1


class TestCardLocation:
    async def test_get_card_location_full_info(self, search_service, db):
        await _seed(db)
        loc = await search_service.get_card_location(1)
        assert loc is not None
        assert loc["full_name"] == "Сидоров Сидор"
        assert loc["status"] == "placed"
        assert loc["box_number"] == "B-0010"
        assert loc["shelf_name"] == "Полка 1"
        assert loc["section_name"] == "Секция А"
