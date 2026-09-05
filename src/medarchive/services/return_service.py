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

    async def _find_active_return_shipment(self, card_id: int):
        """Активная возвратная отправка карты (строки shipments) или None.

        Общая выборка для receive_return (приём) и get_receivable_card_info
        (превью): 1) одиночная карта — kind='card' по card_id; 2) карта в
        коробке возврата — kind='box' через box_cards (removed_at IS NULL).
        Семантика поиска одна, состояние не меняется. Возвращает id/box_id/
        kind/to_branch_id/from_branch_id — для превью нужен kind и адресат.
        """
        shipment = await self.db.fetch_one(
            """SELECT id, box_id, kind, to_branch_id, from_branch_id
               FROM shipments
               WHERE card_id = ? AND status = 'issued' AND kind = 'card'""",
            (card_id,),
        )
        if shipment is not None:
            return shipment
        return await self.db.fetch_one(
            """SELECT s.id, s.box_id, s.kind, s.to_branch_id, s.from_branch_id
               FROM shipments s
               JOIN box_cards bc ON bc.box_id = s.box_id
               WHERE bc.card_id = ? AND bc.removed_at IS NULL
                 AND s.status = 'issued' AND s.kind = 'box'
               LIMIT 1""",
            (card_id,),
        )

    async def get_receivable_card_info(self, card_id: int) -> dict:
        """Превью карты для /receive-return/check — проверка БЕЗ изменения
        состояния.

        Те же проверки, что в receive_return (карта есть, статус in_transit,
        есть активная возвратная отправка), но без UPDATE: страница
        «Проверить» не должна двигать статусы. Ошибки — теми же текстами
        ValueError (их покажет и подтверждение).

        Возврат: card_id, full_name, status, shipment_kind ('card'|'box'),
        destination — имя филиала-получателя или None. Для kind='card'
        назначение = to_branch_id (филиал-заказчик возврата из заявки); для
        kind='box' в shipments to_branch_id нет — есть только from_branch_id
        (филиал, откуда ушла RB-коробка), получатель в данных не закодирован,
        поэтому destination=None (интерфейс показывает «—», а не врёт
        отправителем как назначением).
        """
        card = await self.card_repo.get_by_id(card_id)
        if card is None:
            raise ValueError("Карта не найдена")
        if card.status != CardStatus.in_transit:
            raise ValueError(
                f"Карта должна быть в статусе 'in_transit', сейчас '{card.status.value}'"
            )
        shipment = await self._find_active_return_shipment(card_id)
        if shipment is None:
            raise ValueError("Не найдена активная отправка для приёмки возврата")

        info = {
            "card_id": card.id,
            "full_name": card.full_name,
            "status": card.status.value,
            "shipment_kind": shipment["kind"],
            "destination": None,
        }
        if shipment["kind"] == "card" and shipment["to_branch_id"] is not None:
            row = await self.db.fetch_one(
                "SELECT name FROM branches WHERE id = ?", (shipment["to_branch_id"],)
            )
            if row is not None:
                info["destination"] = row["name"]
        return info

    async def receive_return(self, card_id: int, user_id: int) -> None:
        card = await self.card_repo.get_by_id(card_id)
        if card is None:
            raise ValueError("Карта не найдена")
        if card.status != CardStatus.in_transit:
            raise ValueError(f"Карта должна быть в статусе 'in_transit', сейчас '{card.status.value}'")

        shipment = await self._find_active_return_shipment(card_id)

        if shipment is None:
            raise ValueError("Не найдена активная отправка для приёмки возврата")

        await self.db.execute(
            "UPDATE shipments SET status = 'received', received_by = ?, received_at = datetime('now') "
            "WHERE id = ?",
            (user_id, shipment["id"]),
        )

        if shipment["box_id"] is not None:
            await self.box_repo.update_status(shipment["box_id"], BoxStatus.received)

        from medarchive.services.movement_service import MovementService
        ms = MovementService(self.db)
        branch_row = await self.db.fetch_one(
            "SELECT branch_id FROM users WHERE id = ?", (user_id,)
        )
        branch_id = branch_row["branch_id"] if branch_row is not None else None
        to_location = f"branch {branch_id}" if branch_id is not None else "branch ?"
        await ms.log_movement(card_id, "returned", None, to_location, user_id)

        await self.card_repo.update_status(card_id, CardStatus.in_branch)
        await self.db.conn.commit()
