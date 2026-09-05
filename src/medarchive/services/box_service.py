"""Сервис упаковки коробок: создать → сканировать карты → закрыть."""
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl
from medarchive.domain.models import Card, Box
from medarchive.domain.statuses import CardStatus, BoxStatus


class DuplicateCardError(Exception):
    """Карта уже находится в этой коробке."""


class CardInAnotherBoxError(Exception):
    """Карта числится в другой активной коробке (остаток) — нужен перенос."""

    def __init__(self, card, other_boxes):
        self.card = card
        self.other_boxes = other_boxes  # [{box_id, box_number, status}] — активные, кроме текущей
        super().__init__(
            f"Карта «{card.full_name}» уже числится в другой коробке"
        )


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
        # Новая коробка становится активной для пользователя
        await self.box_repo.set_active_box(user_id, box.id)
        return box

    async def scan_card(self, box_id: int, full_name: str) -> Card:
        # Find existing card or create new
        cards = await self.card_repo.find_by_name(full_name)
        if cards:
            card = cards[0]  # Take first match (operator should resolve duplicates in UI)
        else:
            card = await self.card_repo.create(full_name)

        # Карта уже в ЭТОЙ коробке — дубль (приоритет, как раньше)
        if await self.box_repo.card_in_box(box_id, card.id):
            raise DuplicateCardError("Карта уже в этой коробке")

        # Карта числится в ДРУГИХ активных коробках — остаток, не добавляем
        other_boxes = await self.box_repo.card_other_boxes(card.id, box_id)
        if other_boxes:
            raise CardInAnotherBoxError(card, other_boxes)

        await self.box_repo.add_card(box_id, card.id)
        await self._mark_packed(card.id, box_id)
        card.status = CardStatus.in_box
        return card

    async def _mark_packed(self, card_id: int, box_id: int) -> None:
        """Общий хвост scan/transfer: статус in_box + лог packed."""
        await self.card_repo.update_status(card_id, CardStatus.in_box)

        from medarchive.services.movement_service import MovementService
        ms = MovementService(self.db)
        box = await self.box_repo.get_by_id(box_id)
        box_label = box.box_number if box else str(box_id)
        await ms.log_movement(card_id, "packed", None, f"box {box_label}")

    async def transfer_card(self, box_id: int, card_id: int) -> Card:
        """Перенести карту в текущую коробку: снять со всех остальных активных коробок,
        добавить сюда (статус in_box, лог packed).
        Guard: карта не числится в других коробках или уже в текущей — ValueError."""
        other_boxes = await self.box_repo.card_other_boxes(card_id, box_id)
        if not other_boxes:
            raise ValueError("Карта не числится в других коробках — переносить нечего")
        if await self.box_repo.card_in_box(box_id, card_id):
            raise ValueError("Карта уже в этой коробке — перенос не нужен")

        for other in other_boxes:
            await self.remove_card(other["box_id"], card_id)

        await self.box_repo.add_card(box_id, card_id)
        await self._mark_packed(card_id, box_id)
        card = await self.card_repo.get_by_id(card_id)
        card.status = CardStatus.in_box
        return card

    async def remove_card(self, box_id: int, card_id: int) -> None:
        """Убрать карту из коробки (мягкое удаление). Статус карты возвращается в in_branch,
        если карта больше не числится ни в одной другой коробке."""
        removed = await self.box_repo.remove_card(box_id, card_id)
        if not removed:
            return
        if not await self.box_repo.card_in_other_boxes(card_id, box_id):
            await self.card_repo.update_status(card_id, CardStatus.in_branch)

        from medarchive.services.movement_service import MovementService
        ms = MovementService(self.db)
        box = await self.box_repo.get_by_id(box_id)
        box_label = box.box_number if box else str(box_id)
        await ms.log_movement(card_id, "unpacked", f"box {box_label}", None)

    async def get_box_contents(self, box_id: int) -> list[Card]:
        return await self.box_repo.get_cards(box_id)

    async def close_box(self, box_id: int, user_id: int | None = None) -> None:
        box = await self.box_repo.get_by_id(box_id)
        if box is None:
            raise ValueError("Коробка не найдена")
        if user_id is not None and box.created_by != user_id:
            raise ValueError("Нельзя закрыть чужую коробку")
        cards = await self.box_repo.get_cards(box_id)
        if not cards:
            raise ValueError("Нельзя закрыть пустую коробку")
        await self.box_repo.update_status(box_id, BoxStatus.ready)
        # Если закрыли активную коробку — выбор сбрасывается (остаются другие открытые)
        if user_id is not None:
            row = await self.db.fetch_one(
                "SELECT active_box_id FROM users WHERE id = ?", (user_id,)
            )
            if row is not None and row["active_box_id"] == box_id:
                await self.box_repo.set_active_box(user_id, None)

    async def select_box(self, box_id: int, user_id: int) -> Box:
        """Сделать коробку активной (проверка: своя, ещё открытая)."""
        box = await self.box_repo.get_by_id(box_id)
        if box is None or box.created_by != user_id or box.status != BoxStatus.packing:
            raise ValueError("Коробка не найдена или уже закрыта")
        await self.box_repo.set_active_box(user_id, box.id)
        return box

    async def list_open_boxes(self, user_id: int) -> list[dict]:
        """Все открытые коробки пользователя: id, номер, число карт, дата создания."""
        return await self.box_repo.list_open_for_user(user_id)
