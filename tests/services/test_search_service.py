"""SearchService: регистронезависимый поиск (кириллица), одна карта = одна строка.

Покрывает решения дизайна /search (2026-09-05):
- двухфазный поиск в Python (SQLite LIKE/LOWER не сворачивают регистр кириллицы);
- «актуальная коробка» по цепочке статусов карты (in_box→packing/ready,
  in_transit→in_transit, in_archive→received, placed/pending_return→placed),
  tie-break: box_placements.placed_at → box_cards.added_at → box.id DESC;
- остальные активные коробки — в stale (только номера).
"""
import pytest
from medarchive.services.search_service import SearchService


@pytest.fixture
async def search_service(db):
    return SearchService(db)


async def _seed_base(db):
    """Справочники: филиал, пользователь, секция и две полки."""
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'u1', 'h', 'branch', 1)"
    )
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


async def _seed_card(db, card_id: int, full_name: str, status: str) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (?, ?, ?)",
        (card_id, full_name, status),
    )


async def _seed_box(db, box_id: int, box_number: str, status: str = "placed") -> None:
    await db.execute(
        "INSERT OR REPLACE INTO boxes (id, box_number, origin_branch_id, created_by, status) "
        "VALUES (?, ?, 1, 1, ?)",
        (box_id, box_number, status),
    )


async def _add_card_to_box(db, box_id: int, card_id: int) -> None:
    """Активная запись карты в коробке (removed_at IS NULL)."""
    await db.execute(
        "INSERT OR REPLACE INTO box_cards (id, box_id, card_id) VALUES (?, ?, ?)",
        (box_id * 100 + card_id, box_id, card_id),
    )


async def _place_box(db, box_id: int, shelf_id: int, placed_at: str) -> None:
    """Активное размещение коробки на полке."""
    await db.execute(
        "INSERT OR REPLACE INTO box_placements (id, box_id, shelf_id, placed_at) "
        "VALUES (?, ?, ?, ?)",
        (box_id, box_id, shelf_id, placed_at),
    )


class TestSearchCards:
    async def test_cyrillic_lowercase_finds_uppercase_name(self, search_service, db):
        """«иванов» строчными находит «Иванов Иван Иванович» (регистронезависимость кириллицы)."""
        await _seed_base(db)
        await _seed_card(db, 1, "Иванов Иван Иванович", "placed")
        await _seed_box(db, 10, "B-0010")
        await _add_card_to_box(db, 10, 1)
        await _place_box(db, 10, 1, "2026-01-01 10:00:00")
        await db.conn.commit()

        results = await search_service.search_cards("иванов")
        assert len(results) == 1
        assert results[0]["full_name"] == "Иванов Иван Иванович"
        assert results[0]["box_number"] == "B-0010"

    async def test_two_placed_boxes_one_row_freshest_and_stale(self, search_service, db):
        """Карта в 2 активных placed-коробках → одна запись: актуальная — свежайшая по placed_at,
        её место в place, другая коробка — в stale."""
        await _seed_base(db)
        await _seed_card(db, 1, "Иванов Иван Иванович", "placed")
        await _seed_box(db, 10, "B-0010")  # старшее размещение
        await _seed_box(db, 11, "B-0011")  # свежайшее размещение
        await _add_card_to_box(db, 10, 1)
        await _add_card_to_box(db, 11, 1)
        await _place_box(db, 10, 1, "2026-01-01 10:00:00")  # Полка 1
        await _place_box(db, 11, 2, "2026-02-01 10:00:00")  # Полка 2
        await db.conn.commit()

        results = await search_service.search_cards("Иванов")
        assert len(results) == 1
        row = results[0]
        assert row["id"] == 1
        assert row["status"] == "placed"
        assert row["box_number"] == "B-0011"
        assert row["box_status"] == "placed"
        assert row["place"] == "Секция А / Полка 2"
        assert row["stale"] == ["B-0010"]

    async def test_single_box_stale_empty(self, search_service, db):
        """Карта в одной активной коробке → stale == [], место — раздел/полка."""
        await _seed_base(db)
        await _seed_card(db, 1, "Иванов Иван Иванович", "placed")
        await _seed_box(db, 10, "B-0010")
        await _add_card_to_box(db, 10, 1)
        await _place_box(db, 10, 1, "2026-01-01 10:00:00")
        await db.conn.commit()

        results = await search_service.search_cards("Иванов")
        assert len(results) == 1
        assert results[0]["box_number"] == "B-0010"
        assert results[0]["place"] == "Секция А / Полка 1"
        assert results[0]["stale"] == []

    async def test_no_active_boxes(self, search_service, db):
        """Карта без активных коробок → box_number is None, place None, stale == []."""
        await _seed_base(db)
        await _seed_card(db, 1, "Иванов Иван Иванович", "placed")
        await db.conn.commit()

        results = await search_service.search_cards("Иванов")
        assert len(results) == 1
        row = results[0]
        assert row["id"] == 1
        assert row["full_name"] == "Иванов Иван Иванович"
        assert row["box_number"] is None
        assert row["box_status"] is None
        assert row["place"] is None
        assert row["stale"] == []

    async def test_in_transit_card_in_transit_box_place_none(self, search_service, db):
        """Карта in_transit в коробке in_transit → актуальная она же, place is None."""
        await _seed_base(db)
        await _seed_card(db, 1, "Иванов Иван Иванович", "in_transit")
        await _seed_box(db, 10, "B-0010", status="in_transit")
        await _add_card_to_box(db, 10, 1)
        await db.conn.commit()

        results = await search_service.search_cards("Иванов")
        assert len(results) == 1
        row = results[0]
        assert row["box_number"] == "B-0010"
        assert row["box_status"] == "in_transit"
        assert row["place"] is None
        assert row["stale"] == []


class TestSearchBoxes:
    async def test_case_insensitive_finds_box_with_place(self, search_service, db):
        """search_boxes("b-0010") строчными находит «B-0010»; размещённая коробка имеет place."""
        await _seed_base(db)
        await _seed_card(db, 1, "Иванов Иван Иванович", "placed")
        await _seed_box(db, 10, "B-0010")
        await _seed_box(db, 11, "B-0011")
        await _add_card_to_box(db, 10, 1)
        await _place_box(db, 10, 1, "2026-01-01 10:00:00")
        await db.conn.commit()

        results = await search_service.search_boxes("b-0010")
        assert len(results) == 1
        row = results[0]
        assert row["box_number"] == "B-0010"
        assert row["status"] == "placed"
        assert row["card_count"] == 1
        assert row["place"] == "Секция А / Полка 1"


class TestCardLocation:
    async def test_get_card_location_regression(self, search_service, db):
        """get_card_location не меняется (регресс): те же ключи и логика."""
        await _seed_base(db)
        await _seed_card(db, 1, "Иванов Иван Иванович", "placed")
        await _seed_box(db, 10, "B-0010")
        await _add_card_to_box(db, 10, 1)
        await _place_box(db, 10, 1, "2026-01-01 10:00:00")
        await db.conn.commit()

        loc = await search_service.get_card_location(1)
        assert loc is not None
        assert loc["full_name"] == "Иванов Иван Иванович"
        assert loc["status"] == "placed"
        assert loc["box_number"] == "B-0010"
        assert loc["shelf_name"] == "Полка 1"
        assert loc["section_name"] == "Секция А"
