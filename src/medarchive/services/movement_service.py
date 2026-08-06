"""Журнал движений карт."""
from medarchive.adapters.sqlite.database import Database


class MovementService:
    def __init__(self, db: Database):
        self.db = db

    async def log_movement(self, card_id: int, action: str, from_location: str | None, to_location: str | None, user_id: int | None = None) -> None:
        await self.db.execute(
            "INSERT INTO card_movements (card_id, action, from_location, to_location, user_id) VALUES (?, ?, ?, ?, ?)",
            (card_id, action, from_location, to_location, user_id),
        )
        await self.db.conn.commit()

    async def get_card_history(self, card_id: int) -> list[dict]:
        rows = await self.db.fetch_all(
            "SELECT * FROM card_movements WHERE card_id = ? ORDER BY timestamp",
            (card_id,),
        )
        return [dict(r) for r in rows]

    async def get_recent_movements(self, limit: int = 50) -> list[dict]:
        rows = await self.db.fetch_all(
            """SELECT cm.*, c.full_name as card_name
               FROM card_movements cm
               JOIN cards c ON cm.card_id = c.id
               ORDER BY cm.timestamp DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]
