"""Миграции — создание всех таблиц."""
import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('branch','archive','viewer','admin')),
    branch_id INTEGER REFERENCES branches(id)
);

CREATE TABLE IF NOT EXISTS couriers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT
);

CREATE TABLE IF NOT EXISTS archive_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archive_shelves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id INTEGER NOT NULL REFERENCES archive_sections(id),
    name TEXT NOT NULL,
    qr_code TEXT
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_branch',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS boxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_number TEXT NOT NULL UNIQUE,
    origin_branch_id INTEGER NOT NULL REFERENCES branches(id),
    status TEXT NOT NULL DEFAULT 'packing',
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS box_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id INTEGER NOT NULL REFERENCES boxes(id),
    card_id INTEGER NOT NULL REFERENCES cards(id),
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('box','card')),
    box_id INTEGER REFERENCES boxes(id),
    card_id INTEGER REFERENCES cards(id),
    from_branch_id INTEGER REFERENCES branches(id),
    to_branch_id INTEGER REFERENCES branches(id),
    to_department INTEGER NOT NULL DEFAULT 0,
    courier_id INTEGER NOT NULL REFERENCES couriers(id),
    issued_by INTEGER NOT NULL REFERENCES users(id),
    issued_at TEXT NOT NULL DEFAULT (datetime('now')),
    received_by INTEGER REFERENCES users(id),
    received_at TEXT,
    status TEXT NOT NULL DEFAULT 'issued'
);

CREATE TABLE IF NOT EXISTS box_placements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id INTEGER NOT NULL REFERENCES boxes(id),
    shelf_id INTEGER NOT NULL REFERENCES archive_shelves(id),
    placed_at TEXT NOT NULL DEFAULT (datetime('now')),
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id),
    requested_by INTEGER NOT NULL REFERENCES users(id),
    target_branch_id INTEGER REFERENCES branches(id),
    target_department INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    fulfilled_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS card_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id),
    action TEXT NOT NULL,
    from_location TEXT,
    to_location TEXT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def run_migrations(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA)
    await conn.commit()
