"""Сервис приёмки и размещения коробок в архиве."""
from datetime import datetime
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import BoxRepositoryImpl, CardRepositoryImpl
from medarchive.domain.statuses import BoxStatus, CardStatus


class ReceiveService:
    def __init__(self, db: Database, box_repo: BoxRepositoryImpl, card_repo: CardRepositoryImpl):
        self.db = db
        self.box_repo = box_repo
        self.card_repo = card_repo

    async def receive_box(self, box_id: int, user_id: int) -> None:
        box = await self.box_repo.get_by_id(box_id)
        if box is None:
            raise ValueError("Коробка не найдена")
        if box.status != BoxStatus.in_transit:
            raise ValueError(f"Коробка должна быть в статусе 'in_transit', сейчас '{box.status.value}'")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Update shipment
        await self.db.execute(
            "UPDATE shipments SET status = 'received', received_by = ?, received_at = ? "
            "WHERE box_id = ? AND status = 'issued'",
            (user_id, now, box_id),
        )

        # Update box
        await self.box_repo.update_status(box_id, BoxStatus.received)

        # Update cards + audit log
        cards = await self.box_repo.get_cards(box_id)
        from medarchive.services.movement_service import MovementService
        ms = MovementService(self.db)
        for card in cards:
            await self.card_repo.update_status(card.id, CardStatus.in_archive)
            await ms.log_movement(card.id, "received", None, "archive", user_id)

        await self.db.conn.commit()

    async def place_box(self, box_id: int, shelf_id: int, user_id: int | None = None) -> None:
        box = await self.box_repo.get_by_id(box_id)
        if box is None:
            raise ValueError("Коробка не найдена")
        if box.status != BoxStatus.received:
            raise ValueError(f"Коробка должна быть в статусе 'received', сейчас '{box.status.value}'")

        # Create placement
        await self.db.execute(
            "INSERT INTO box_placements (box_id, shelf_id, placed_at) VALUES (?, ?, ?)",
            (box_id, shelf_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )

        # Update box
        await self.box_repo.update_status(box_id, BoxStatus.placed)

        # Update cards
        cards = await self.box_repo.get_cards(box_id)
        shelf = await self.db.fetch_one("SELECT name FROM archive_shelves WHERE id = ?", (shelf_id,))
        shelf_name = shelf["name"] if shelf else None
        from medarchive.services.movement_service import MovementService
        ms = MovementService(self.db)
        for card in cards:
            await self.card_repo.update_status(card.id, CardStatus.placed)
            await ms.log_movement(card.id, "placed", "archive", f"shelf {shelf_name or shelf_id}", user_id)

        await self.db.conn.commit()

    async def get_pending_boxes(self) -> list[dict]:
        rows = await self.db.fetch_all(
            """SELECT b.*, s.issued_at, c.name as courier_name
               FROM boxes b
               JOIN shipments s ON s.box_id = b.id AND s.status = 'issued'
               JOIN couriers c ON s.courier_id = c.id
               WHERE b.status = 'in_transit'
               ORDER BY s.issued_at"""
        )
        return [dict(r) for r in rows]

    async def get_received_boxes(self) -> list[dict]:
        rows = await self.db.fetch_all(
            "SELECT * FROM boxes WHERE status = 'received' ORDER BY box_number"
        )
        return [dict(r) for r in rows]

    async def get_sections_and_shelves(self) -> list[dict]:
        rows = await self.db.fetch_all(
            """SELECT sec.id as section_id, sec.name as section_name,
                      sh.id as shelf_id, sh.name as shelf_name, sh.qr_code
               FROM archive_sections sec
               LEFT JOIN archive_shelves sh ON sh.section_id = sec.id
               ORDER BY sec.name, sh.name"""
        )
        return [dict(r) for r in rows]
