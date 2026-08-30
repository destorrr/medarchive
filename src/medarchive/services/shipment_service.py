"""Сервис передачи: shipment для коробки, статусы, список курьеров."""
from datetime import datetime
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import (
    BoxRepositoryImpl, CourierRepositoryImpl, CardRepositoryImpl
)
from medarchive.domain.models import Courier
from medarchive.domain.statuses import BoxStatus, CardStatus


class ShipmentService:
    def __init__(
        self,
        db: Database,
        box_repo: BoxRepositoryImpl,
        courier_repo: CourierRepositoryImpl,
        card_repo: CardRepositoryImpl,
    ):
        self.db = db
        self.box_repo = box_repo
        self.courier_repo = courier_repo
        self.card_repo = card_repo

    async def transfer_box(self, box_id: int, courier_id: int, issued_by: int) -> dict:
        box = await self.box_repo.get_by_id(box_id)
        if box is None:
            raise ValueError("Коробка не найдена")
        if box.status != BoxStatus.ready:
            raise ValueError(f"Коробка должна быть в статусе 'ready', сейчас '{box.status.value}'")

        # Create shipment
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self.db.execute(
            """INSERT INTO shipments (kind, box_id, from_branch_id, courier_id, issued_by, issued_at, status)
               VALUES ('box', ?, ?, ?, ?, ?, 'issued')""",
            (box_id, box.origin_branch_id, courier_id, issued_by, now),
        )
        await self.db.conn.commit()
        shipment_id = cursor.lastrowid

        # Update box status
        await self.box_repo.update_status(box_id, BoxStatus.in_transit)

        # Update all cards in box
        cards = await self.box_repo.get_cards(box_id)
        for card in cards:
            await self.card_repo.update_status(card.id, CardStatus.in_transit)

        row = await self.db.fetch_one("SELECT * FROM shipments WHERE id = ?", (shipment_id,))
        return dict(row) if row else {}

    async def list_couriers(self) -> list[Courier]:
        return await self.courier_repo.list_all()

    async def get_shipments_for_branch(self, branch_id: int) -> list[dict]:
        rows = await self.db.fetch_all(
            """SELECT s.*, b.box_number, c.name as courier_name
               FROM shipments s
               JOIN boxes b ON s.box_id = b.id
               JOIN couriers c ON s.courier_id = c.id
               WHERE s.from_branch_id = ?
               ORDER BY s.issued_at DESC""",
            (branch_id,),
        )
        return [dict(r) for r in rows]
