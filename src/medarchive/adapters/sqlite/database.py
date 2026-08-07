"""Управление подключением к SQLite."""
import aiosqlite
from medarchive.config import Config
from medarchive.adapters.sqlite.migrations import run_migrations


class Database:
    def __init__(self, config: Config):
        self.config = config
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self._conn is not None:
            return  # уже инициализировано — идемпотентный вызов
        conn = aiosqlite.connect(self.config.db_path)
        # Рабочий поток aiosqlite — не-daemon; если соединение не закрыть,
        # интерпретатор зависает при выходе (тесты прошли, а процесс висит).
        # Помечаем поток daemon — выход не блокируется, sqlite3-соединение
        # финализируется своим __del__ при завершении процесса.
        conn._thread.daemon = True
        self._conn = await conn
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await run_migrations(self._conn)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call await db.init() first.")
        return self._conn

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        return await self.conn.execute(sql, params)

    async def fetch_one(self, sql: str, params: tuple = ()):
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetch_all(self, sql: str, params: tuple = ()):
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchall()
