"""Поиск карт и коробок по архиву."""
from medarchive.adapters.sqlite.database import Database


class SearchService:
    def __init__(self, db: Database):
        self.db = db

    async def search_cards(self, query: str) -> list[dict]:
        rows = await self.db.fetch_all(
            """SELECT c.id, c.full_name, c.status,
                      b.box_number,
                      ash.name as shelf_name, asec.name as section_name
               FROM cards c
               LEFT JOIN box_cards bc ON c.id = bc.card_id AND bc.removed_at IS NULL
               LEFT JOIN boxes b ON bc.box_id = b.id
               LEFT JOIN box_placements bp ON b.id = bp.box_id AND bp.removed_at IS NULL
               LEFT JOIN archive_shelves ash ON bp.shelf_id = ash.id
               LEFT JOIN archive_sections asec ON ash.section_id = asec.id
               WHERE c.full_name LIKE ?
               ORDER BY c.full_name""",
            (f"%{query}%",),
        )
        return [dict(r) for r in rows]

    async def search_boxes(self, query: str) -> list[dict]:
        rows = await self.db.fetch_all(
            """SELECT b.id, b.box_number, b.status, b.origin_branch_id,
                      COUNT(bc.id) as card_count
               FROM boxes b
               LEFT JOIN box_cards bc ON b.id = bc.box_id AND bc.removed_at IS NULL
               WHERE b.box_number LIKE ?
               GROUP BY b.id
               ORDER BY b.box_number""",
            (f"%{query}%",),
        )
        return [dict(r) for r in rows]

    async def get_card_location(self, card_id: int) -> dict | None:
        row = await self.db.fetch_one(
            """SELECT c.id, c.full_name, c.status,
                      b.box_number,
                      ash.name as shelf_name, asec.name as section_name
               FROM cards c
               LEFT JOIN box_cards bc ON c.id = bc.card_id AND bc.removed_at IS NULL
               LEFT JOIN boxes b ON bc.box_id = b.id
               LEFT JOIN box_placements bp ON b.id = bp.box_id AND bp.removed_at IS NULL
               LEFT JOIN archive_shelves ash ON bp.shelf_id = ash.id
               LEFT JOIN archive_sections asec ON ash.section_id = asec.id
               WHERE c.id = ?""",
            (card_id,),
        )
        return dict(row) if row else None
