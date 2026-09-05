"""Поиск карт и коробок по архиву.

SQLite не сворачивает регистр кириллицы ни в LIKE, ни в LOWER(), поэтому поиск —
двухфазный в Python (дизайн /search, 2026-09-05):
1) лёгкая выборка id/имени → фильтр query.lower() in value.lower();
2) по найденным id — join-запрос местонахождений и схлопывание «одна карта = одна строка».
"""
from medarchive.adapters.sqlite.database import Database

# Цепочка «статус карты → допустимые статусы актуальной коробки» (дизайн /search, п.1):
# in_box → packing/ready; in_transit → in_transit; in_archive → received;
# placed/pending_return → placed (карта физически на полке).
_CARD_STATUS_TO_BOX_STATUSES: dict[str, tuple[str, ...]] = {
    "in_box": ("packing", "ready"),
    "in_transit": ("in_transit",),
    "in_archive": ("received",),
    "placed": ("placed",),
    "pending_return": ("placed",),
}


class SearchService:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _freshest_key(box: dict) -> tuple[str, str, int]:
        """Ключ «свежести» активной коробки: размещение, затем добавление, затем id DESC.

        Отсутствие размещения (placed_at = None) сортируется как самое старое —
        коробка без полки не может быть «свежайшим размещением».
        """
        return (box["placed_at"] or "", box["added_at"] or "", box["box_id"])

    @staticmethod
    def _place(section_name: str | None, shelf_name: str | None) -> str | None:
        if section_name and shelf_name:
            return f"{section_name} / {shelf_name}"
        return None

    async def search_cards(self, query: str) -> list[dict]:
        """Одна запись на карту: {id, full_name, status, box_number, box_status,
        place, stale} — актуальная коробка по цепочке статусов, прочие активные
        коробки (кроме актуальной) номерами в stale."""
        query = query.strip()
        if not query:
            return []
        needle = query.lower()

        # Фаза 1: лёгкая выборка всех карт, фильтр подстроки — в Python (кириллица).
        rows = await self.db.fetch_all(
            "SELECT id, full_name, status FROM cards ORDER BY full_name"
        )
        matched = [dict(r) for r in rows if needle in r["full_name"].lower()]

        # Фаза 2: по найденным id — активные коробки с текущими размещениями.
        boxes_by_card: dict[int, dict[int, dict]] = {}
        if matched:
            ids = tuple(c["id"] for c in matched)
            placeholders = ",".join("?" for _ in ids)
            joined = await self.db.fetch_all(
                f"""SELECT c.id AS card_id, b.id AS box_id,
                           b.box_number, b.status AS box_status,
                           bp.placed_at, bc.added_at,
                           ash.name AS shelf_name, asec.name AS section_name
                    FROM cards c
                    JOIN box_cards bc ON bc.card_id = c.id AND bc.removed_at IS NULL
                    JOIN boxes b ON b.id = bc.box_id
                    LEFT JOIN box_placements bp ON bp.box_id = b.id AND bp.removed_at IS NULL
                    LEFT JOIN archive_shelves ash ON ash.id = bp.shelf_id
                    LEFT JOIN archive_sections asec ON asec.id = ash.section_id
                    WHERE c.id IN ({placeholders})
                    ORDER BY c.full_name""",
                ids,
            )
            # На коробку берём свежайшее активное размещение (страховка от дублей
            # box_placements; в норме активное размещение одно).
            for row in joined:
                box = dict(row)
                per_box = boxes_by_card.setdefault(box["card_id"], {})
                prev = per_box.get(box["box_id"])
                if prev is None or (box["placed_at"] or "") > (prev["placed_at"] or ""):
                    per_box[box["box_id"]] = box

        results: list[dict] = []
        for card in matched:
            candidates = list(boxes_by_card.get(card["id"], {}).values())
            if not candidates:
                results.append({
                    "id": card["id"],
                    "full_name": card["full_name"],
                    "status": card["status"],
                    "box_number": None,
                    "box_status": None,
                    "place": None,
                    "stale": [],
                })
                continue
            ordered = sorted(candidates, key=self._freshest_key, reverse=True)
            allowed = _CARD_STATUS_TO_BOX_STATUSES.get(card["status"], ())
            preferred = [b for b in ordered if b["box_status"] in allowed]
            actual = (preferred or ordered)[0]
            results.append({
                "id": card["id"],
                "full_name": card["full_name"],
                "status": card["status"],
                "box_number": actual["box_number"],
                "box_status": actual["box_status"],
                "place": self._place(actual["section_name"], actual["shelf_name"]),
                "stale": [b["box_number"] for b in ordered
                          if b["box_id"] != actual["box_id"]],
            })
        return results

    async def search_boxes(self, query: str) -> list[dict]:
        """Коробки по номеру (тот же двухфазный приём, регистронезависимо):
        {id, box_number, status, card_count, place}."""
        query = query.strip()
        if not query:
            return []
        needle = query.lower()

        # Фаза 1: лёгкая выборка номеров, фильтр в Python (единообразно с картами).
        rows = await self.db.fetch_all("SELECT id, box_number FROM boxes")
        matched = tuple(
            r["id"] for r in rows if needle in r["box_number"].lower()
        )
        if not matched:
            return []

        # Фаза 2: детали по найденным id + место текущего размещения.
        placeholders = ",".join("?" for _ in matched)
        joined = await self.db.fetch_all(
            f"""SELECT b.id, b.box_number, b.status,
                       COUNT(bc.id) AS card_count,
                       ash.name AS shelf_name, asec.name AS section_name
                FROM boxes b
                LEFT JOIN box_cards bc ON bc.box_id = b.id AND bc.removed_at IS NULL
                LEFT JOIN box_placements bp ON bp.box_id = b.id AND bp.removed_at IS NULL
                LEFT JOIN archive_shelves ash ON ash.id = bp.shelf_id
                LEFT JOIN archive_sections asec ON asec.id = ash.section_id
                WHERE b.id IN ({placeholders})
                GROUP BY b.id
                ORDER BY b.box_number""",
            matched,
        )
        return [
            {
                "id": r["id"],
                "box_number": r["box_number"],
                "status": r["status"],
                "card_count": r["card_count"],
                "place": self._place(r["section_name"], r["shelf_name"]),
            }
            for r in joined
        ]

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
