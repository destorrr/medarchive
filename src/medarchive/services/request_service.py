"""Сервис заявок на выдачу карт."""
from datetime import datetime
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl
from medarchive.domain.statuses import CardStatus


class RequestService:
    def __init__(self, db: Database, card_repo: CardRepositoryImpl, box_repo: BoxRepositoryImpl):
        self.db = db
        self.card_repo = card_repo
        self.box_repo = box_repo

    async def create_request(
        self, card_id: int, requested_by: int, target_branch_id: int | None, target_department: bool = False
    ) -> dict:
        cursor = await self.db.execute(
            """INSERT INTO requests (card_id, requested_by, target_branch_id, target_department, created_at, status)
               VALUES (?, ?, ?, ?, ?, 'new')""",
            (card_id, requested_by, target_branch_id, int(target_department),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        await self.db.conn.commit()
        row = await self.db.fetch_one("SELECT * FROM requests WHERE id = ?", (cursor.lastrowid,))
        return dict(row) if row else {}

    async def get_queue(self) -> list[dict]:
        """Очередь активных заявок (new/in_progress) для архива.

        Строка: все поля заявки + card_name (ФИО карты) и branch_name
        (имя филиала-получателя через LEFT JOIN branches по
        target_branch_id — колонка «Филиал» очереди, редизайн контура
        возврата, задача 4)."""
        rows = await self.db.fetch_all(
            """SELECT r.*, c.full_name as card_name, b.name as branch_name
               FROM requests r
               JOIN cards c ON r.card_id = c.id
               LEFT JOIN branches b ON r.target_branch_id = b.id
               WHERE r.status IN ('new', 'in_progress')
               ORDER BY r.created_at ASC"""
        )
        return [dict(r) for r in rows]

    async def get_my_requests(self, user_id: int) -> list[dict]:
        rows = await self.db.fetch_all(
            """SELECT r.*, c.full_name as card_name
               FROM requests r JOIN cards c ON r.card_id = c.id
               WHERE r.requested_by = ?
               ORDER BY r.created_at DESC""",
            (user_id,),
        )
        return [dict(r) for r in rows]

    async def fulfill_request(self, request_id: int, user_id: int) -> None:
        req = await self.db.fetch_one("SELECT * FROM requests WHERE id = ?", (request_id,))
        if req is None:
            raise ValueError("Заявка не найдена")
        if req["status"] not in ("new", "in_progress"):
            raise ValueError(f"Нельзя выполнить заявку в статусе '{req['status']}'")

        # Set in_progress
        await self.db.execute("UPDATE requests SET status = 'in_progress' WHERE id = ?", (request_id,))

        # Remove card from its current box
        await self.db.execute(
            "UPDATE box_cards SET removed_at = ? WHERE card_id = ? AND removed_at IS NULL",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), req["card_id"]),
        )

        # Update card status
        from medarchive.services.movement_service import MovementService
        ms = MovementService(self.db)
        box_row = await self.db.fetch_one(
            "SELECT b.box_number FROM boxes b JOIN box_cards bc ON b.id = bc.box_id "
            "WHERE bc.card_id = ? AND bc.removed_at IS NULL",
            (req["card_id"],),
        )
        box_number = box_row["box_number"] if box_row else None
        from_location = f"box {box_number}" if box_number else None
        await ms.log_movement(req["card_id"], "retrieved_for_return", from_location, None, user_id)

        await self.card_repo.update_status(req["card_id"], CardStatus.pending_return)

        # Complete
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.db.execute(
            "UPDATE requests SET status = 'completed', completed_at = ?, fulfilled_by = ? WHERE id = ?",
            (now, user_id, request_id),
        )
        await self.db.conn.commit()

    async def cancel_request(self, request_id: int) -> None:
        req = await self.db.fetch_one("SELECT * FROM requests WHERE id = ?", (request_id,))
        if req is None:
            raise ValueError("Заявка не найдена")
        if req["status"] not in ("new", "in_progress"):
            raise ValueError(f"Нельзя отменить заявку в статусе '{req['status']}'")
        await self.db.execute("UPDATE requests SET status = 'cancelled' WHERE id = ?", (request_id,))
        await self.db.conn.commit()
