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


@pytest.mark.asyncio
async def test_users_table_has_active_box_id(db):
    """Колонка users.active_box_id существует после init()."""
    cols = await db.fetch_all("PRAGMA table_info(users)")
    names = {row["name"] for row in cols}
    assert "active_box_id" in names


@pytest.mark.asyncio
async def test_users_table_has_full_name(db):
    """Колонка users.full_name существует после init()."""
    cols = await db.fetch_all("PRAGMA table_info(users)")
    names = {row["name"] for row in cols}
    assert "full_name" in names


@pytest.mark.asyncio
async def test_migration_adds_full_name_to_legacy_db(test_config):
    """Существующая БД (старая схема без full_name) получает колонку через ALTER, данные целы."""
    import aiosqlite
    from medarchive.adapters.sqlite.migrations import run_migrations
    conn = await aiosqlite.connect(test_config.db_path)
    await conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, "
        "password_hash TEXT NOT NULL, role TEXT NOT NULL, branch_id INTEGER)"
    )
    await conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
    )
    await conn.commit()
    await run_migrations(conn)
    cols = await conn.execute_fetchall("PRAGMA table_info(users)")
    names = {row[1] for row in cols}
    assert "full_name" in names
    rows = await conn.execute_fetchall("SELECT id, username, full_name FROM users")
    assert len(rows) == 1
    assert rows[0][1] == "u1"
    assert rows[0][2] == ""
    await conn.close()


@pytest.mark.asyncio
async def test_migration_adds_active_box_id_to_legacy_db(test_config):
    """Существующая БД (старая схема) получает колонку через ALTER без потери данных."""
    import aiosqlite
    from medarchive.adapters.sqlite.migrations import run_migrations
    conn = await aiosqlite.connect(test_config.db_path)
    await conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, "
        "password_hash TEXT NOT NULL, role TEXT NOT NULL, branch_id INTEGER)"
    )
    await conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
    )
    await conn.commit()
    await run_migrations(conn)
    cols = await conn.execute_fetchall("PRAGMA table_info(users)")
    names = {row[1] for row in cols}
    assert "active_box_id" in names
    rows = await conn.execute_fetchall("SELECT id, username FROM users")
    assert len(rows) == 1
    assert rows[0][1] == "u1"
    await conn.close()
