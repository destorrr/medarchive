"""Сервис упаковки коробок: создать → сканировать карты → закрыть."""
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl
from medarchive.domain.models import Card, Box
from medarchive.domain.statuses import CardStatus, BoxStatus


class BoxService:
    def __init__(self, db: Database, card_repo: CardRepositoryImpl, box_repo: BoxRepositoryImpl):
        self.db = db
        self.card_repo = card_repo
        self.box_repo = box_repo

    async def create_box(self, branch_id: int, user_id: int) -> Box:
        # Create temporary box to get ID, then update number
        box = await self.box_repo.create(
            box_number="TEMP",
            origin_branch_id=branch_id,
            created_by=user_id,
        )
        box_number = f"B-{box.id:04d}"
        await self.db.execute(
            "UPDATE boxes SET box_number = ? WHERE id = ?",
            (box_number, box.id),
        )
        await self.db.conn.commit()
        box.box_number = box_number
        return box

    async def scan_card(self, box_id: int, full_name: str) -> Card:
        # Find existing card or create new
        cards = await self.card_repo.find_by_name(full_name)
        if cards:
            card = cards[0]  # Take first match (operator should resolve duplicates in UI)
        else:
            card = await self.card_repo.create(full_name)

        # Add to box (dedup handled by repository)
        await self.box_repo.add_card(box_id, card.id)

        # Update card status to in_box
        await self.card_repo.update_status(card.id, CardStatus.in_box)
        card.status = CardStatus.in_box

        from medarchive.services.movement_service import MovementService
        ms = MovementService(self.db)
        box = await self.box_repo.get_by_id(box_id)
        box_label = box.box_number if box else str(box_id)
        await ms.log_movement(card.id, "packed", None, f"box {box_label}", user_id=0)

        return card

    async def get_box_contents(self, box_id: int) -> list[Card]:
        return await self.box_repo.get_cards(box_id)

    async def close_box(self, box_id: int) -> None:
        cards = await self.box_repo.get_cards(box_id)
        if not cards:
            raise ValueError("Нельзя закрыть пустую коробку")
        await self.box_repo.update_status(box_id, BoxStatus.ready)
