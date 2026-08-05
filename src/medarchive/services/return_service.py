"""Сервис возврата: одиночная карта или коробка возврата + приёмка возврата."""
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import (
    CardRepositoryImpl, BoxRepositoryImpl, CourierRepositoryImpl
)
from medarchive.domain.statuses import CardStatus, BoxStatus


class ReturnService:
    def __init__(
        self, db: Database,
        card_repo: CardRepositoryImpl,
        box_repo: BoxRepositoryImpl,
        courier_repo: CourierRepositoryImpl,
    ):
        self.db = db
        self.card_repo = card_repo
        self.box_repo = box_repo
        self.courier_repo = courier_repo

    async def _origin_branch_id(self, issued_by: int) -> int:
        """Филиал-источник возврата: филиал выдающего пользователя,
        иначе первый филиал (филиал архива)."""
        row = await self.db.fetch_one(
            "SELECT branch_id FROM users WHERE id = ?", (issued_by,)
        )
        if row is not None and row["branch_id"] is not None:
            return row["branch_id"]
        fallback = await self.db.fetch_one("SELECT MIN(id) AS id FROM branches")
        return fallback["id"] if fallback is not None and fallback["id"] is not None else 1

    async def return_single_card(
        self, card_id: int, courier_id: int, to_branch_id: int, issued_by: int
    ) -> dict:
        card = await self.card_repo.get_by_id(card_id)
        if card is None:
            raise ValueError("Карта не найдена")
        if card.status != CardStatus.pending_return:
            raise ValueError(f"Карта должна быть в статусе 'pending_return', сейчас '{card.status.value}'")

        cursor = await self.db.execute(
            """INSERT INTO shipments (kind, card_id, to_branch_id, courier_id, issued_by, status)
               VALUES ('card', ?, ?, ?, ?, 'issued')""",
            (card_id, to_branch_id, courier_id, issued_by),
        )
        await self.db.conn.commit()

        await self.card_repo.update_status(card_id, CardStatus.in_transit)

        row = await self.db.fetch_one("SELECT * FROM shipments WHERE id = ?", (cursor.lastrowid,))
        return dict(row) if row else {}

    async def return_as_box(self, card_id: int, courier_id: int, issued_by: int) -> dict:
        card = await self.card_repo.get_by_id(card_id)
        if card is None:
            raise ValueError("Карта не найдена")
        if card.status != CardStatus.pending_return:
            raise ValueError(f"Карта должна быть в статусе 'pending_return', сейчас '{card.status.value}'")

        origin_branch_id = await self._origin_branch_id(issued_by)

        # Create return box (временный номер → RB-XXXX)
        box = await self.box_repo.create(
            box_number="TEMP-RB", origin_branch_id=origin_branch_id, created_by=issued_by
        )
        box_number = f"RB-{box.id:04d}"
        await self.db.execute(
            "UPDATE boxes SET box_number = ? WHERE id = ?", (box_number, box.id)
        )

        # Add card to box
        await self.box_repo.add_card(box.id, card_id)
        await self.box_repo.update_status(box.id, BoxStatus.ready)

        # Create shipment
        cursor = await self.db.execute(
            """INSERT INTO shipments (kind, box_id, from_branch_id, courier_id, issued_by, status)
               VALUES ('box', ?, ?, ?, ?, 'issued')""",
            (box.id, origin_branch_id, courier_id, issued_by),
        )
        await self.db.conn.commit()

        await self.card_repo.update_status(card_id, CardStatus.in_transit)

        row = await self.db.fetch_one("SELECT * FROM shipments WHERE id = ?", (cursor.lastrowid,))
        return dict(row) if row else {}

    async def receive_return(self, card_id: int, user_id: int) -> None:
        card = await self.card_repo.get_by_id(card_id)
        if card is None:
            raise ValueError("Карта не найдена")
        if card.status != CardStatus.in_transit:
            raise ValueError(f"Карта должна быть в статусе 'in_transit', сейчас '{card.status.value}'")

        # 1) Одиночная карта — shipment kind='card'
        shipment = await self.db.fetch_one(
            "SELECT id, box_id FROM shipments WHERE card_id = ? AND status = 'issued' AND kind = 'card'",
            (card_id,),
        )
        # 2) Карта в коробке возврата — shipment kind='box' через box_cards
        if shipment is None:
            shipment = await self.db.fetch_one(
                """SELECT s.id, s.box_id FROM shipments s
                   JOIN box_cards bc ON bc.box_id = s.box_id
                   WHERE bc.card_id = ? AND bc.removed_at IS NULL
                     AND s.status = 'issued' AND s.kind = 'box'
                   LIMIT 1""",
                (card_id,),
            )

        if shipment is None:
            raise ValueError("Не найдена активная отправка для приёмки возврата")

        await self.db.execute(
            "UPDATE shipments SET status = 'received', received_by = ?, received_at = datetime('now') "
            "WHERE id = ?",
            (user_id, shipment["id"]),
        )

        if shipment["box_id"] is not None:
            await self.box_repo.update_status(shipment["box_id"], BoxStatus.received)

        await self.card_repo.update_status(card_id, CardStatus.in_branch)
        await self.db.conn.commit()
