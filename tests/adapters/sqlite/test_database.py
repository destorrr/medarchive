# tests/adapters/sqlite/test_database.py
import pytest


@pytest.mark.asyncio
async def test_database_init_creates_tables(db):
    """После init() все 12 таблиц существуют."""
    tables = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    table_names = {row["name"] for row in tables}

    expected = {
        "branches", "users", "couriers",
        "archive_sections", "archive_shelves",
        "cards", "boxes", "box_cards",
        "shipments", "box_placements",
        "requests", "card_movements",
    }
    assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"


@pytest.mark.asyncio
async def test_create_and_find_branch(db):
    """Можно создать и найти филиал."""
    await db.execute(
        "INSERT INTO branches (name, code) VALUES (?, ?)",
        ("Филиал 1", "F01"),
    )
    row = await db.fetch_one("SELECT * FROM branches WHERE code = ?", ("F01",))
    assert row is not None
    assert row["name"] == "Филиал 1"


@pytest.mark.asyncio
async def test_database_close(db):
    """После close() повторный вызов init() работает (переподключение)."""
    await db.close()
    await db.init()
    # Should not raise — connection re-established
    row = await db.fetch_one("SELECT 1")
    assert row is not None
