"""Сервис инцидентов: ручной разбор потерянных коробок и повреждённых отправок."""
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import (
    BoxRepositoryImpl,
    CardRepositoryImpl,
)
from medarchive.domain.statuses import CardStatus

# Действия по разбору потерянной коробки
ACTION_RETURN_CARDS = "return_cards_to_branch"  # карты → in_branch
ACTION_MARK_LOST = "mark_cards_lost"  # карты остаются в текущем статусе


class IncidentService:
    """Ручной разбор инцидентов (admin only).

    Методы принимают db первым аргументом — сервис не держит состояния.
    """

    async def get_lost_boxes(self, db: Database) -> list[dict]:
        """Коробки со статусом 'lost' + карты в каждой из них."""
        rows = await db.fetch_all(
            "SELECT * FROM boxes WHERE status = 'lost' ORDER BY id"
        )
        result = []
        for row in rows:
            box = dict(row)
            cards = await db.fetch_all(
                """SELECT c.* FROM cards c
                   JOIN box_cards bc ON c.id = bc.card_id
                   WHERE bc.box_id = ? AND bc.removed_at IS NULL
                   ORDER BY c.full_name""",
                (box["id"],),
            )
            box["cards"] = [dict(c) for c in cards]
            result.append(box)
        return result

    async def get_damaged_shipments(self, db: Database) -> list[dict]:
        """Отправки со статусом 'lost' / 'damaged'."""
        rows = await db.fetch_all(
            """SELECT s.*, b.box_number, c.name AS courier_name
               FROM shipments s
               LEFT JOIN boxes b ON s.box_id = b.id
               LEFT JOIN couriers c ON s.courier_id = c.id
               WHERE s.status IN ('lost', 'damaged')
               ORDER BY s.issued_at DESC"""
        )
        return [dict(r) for r in rows]

    async def resolve_lost_box(self, db: Database, box_id: int, action: str) -> None:
        """Разбор потерянной коробки.

        action:
          - 'return_cards_to_branch' (или 'return_cards'): карты → in_branch
          - 'mark_cards_lost' (или 'mark_lost'): карты остаются в текущем статусе
        """
        if action not in (ACTION_RETURN_CARDS, ACTION_MARK_LOST, "return_cards", "mark_lost"):
            raise ValueError(f"Неизвестное действие: {action}")

        box_repo = BoxRepositoryImpl(db)
        card_repo = CardRepositoryImpl(db)

        box = await box_repo.get_by_id(box_id)
        if box is None:
            raise ValueError("Коробка не найдена")

        cards = await box_repo.get_cards(box_id)
        if action in (ACTION_RETURN_CARDS, "return_cards"):
            for card in cards:
                await card_repo.update_status(card.id, CardStatus.in_branch)
        # ACTION_MARK_LOST: карты остаются в текущем статусе — намеренно no-op

    async def resolve_card_status(self, db: Database, card_id: int, new_status: str) -> None:
        """Принудительная смена статуса карты (admin only)."""
        try:
            status = CardStatus(new_status)
        except ValueError:
            raise ValueError(f"Некорректный статус: {new_status}")
        card_repo = CardRepositoryImpl(db)
        await card_repo.update_status(card_id, status)
