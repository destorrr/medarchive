import pytest
from medarchive.services.box_service import (
    BoxService,
    DuplicateCardError,
    CardInAnotherBoxError,
)
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl
from medarchive.domain.statuses import BoxStatus, CardStatus


@pytest.fixture
async def box_service(db):
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    return BoxService(db, card_repo, box_repo)


async def _seed_branch_and_user(db):
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'u1', 'h', 'branch', 1)"
    )
    await db.conn.commit()


class TestBoxService:
    async def test_create_box_generates_number(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        assert box.box_number == f"B-{box.id:04d}"
        assert box.status == BoxStatus.packing

    async def test_scan_new_card_adds_to_box(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        card = await box_service.scan_card(box.id, "Новый Пациент")
        assert card.full_name == "Новый Пациент"
        assert card.status == CardStatus.in_box

        contents = await box_service.get_box_contents(box.id)
        assert len(contents) == 1

    async def test_scan_existing_card_adds_to_box(self, box_service, db):
        await _seed_branch_and_user(db)
        # Pre-create a card
        card_repo = CardRepositoryImpl(db)
        existing = await card_repo.create("Существующий Пациент")

        box = await box_service.create_box(branch_id=1, user_id=1)
        card = await box_service.scan_card(box.id, "Существующий Пациент")
        assert card.id == existing.id

    async def test_scan_duplicate_raises(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.scan_card(box.id, "Дубль")
        with pytest.raises(DuplicateCardError):
            await box_service.scan_card(box.id, "Дубль")

        contents = await box_service.get_box_contents(box.id)
        assert len(contents) == 1

    async def test_create_box_sets_active(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        row = await db.fetch_one("SELECT active_box_id FROM users WHERE id = 1")
        assert row["active_box_id"] == box.id

    async def test_select_box_switches_active(self, box_service, db):
        await _seed_branch_and_user(db)
        box1 = await box_service.create_box(branch_id=1, user_id=1)
        box2 = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.select_box(box1.id, user_id=1)
        row = await db.fetch_one("SELECT active_box_id FROM users WHERE id = 1")
        assert row["active_box_id"] == box1.id

    async def test_select_foreign_box_raises(self, box_service, db):
        await _seed_branch_and_user(db)
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (2, 'u2', 'h', 'branch')"
        )
        await db.conn.commit()
        box = await box_service.create_box(branch_id=1, user_id=1)
        with pytest.raises(ValueError):
            await box_service.select_box(box.id, user_id=2)

    async def test_select_closed_box_raises(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.scan_card(box.id, "Пациент 1")
        await box_service.close_box(box.id, user_id=1)
        with pytest.raises(ValueError):
            await box_service.select_box(box.id, user_id=1)

    async def test_close_active_box_resets_active(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.scan_card(box.id, "Пациент 1")
        await box_service.close_box(box.id, user_id=1)
        row = await db.fetch_one("SELECT active_box_id FROM users WHERE id = 1")
        assert row["active_box_id"] is None

    async def test_list_open_boxes(self, box_service, db):
        await _seed_branch_and_user(db)
        box1 = await box_service.create_box(branch_id=1, user_id=1)
        box2 = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.scan_card(box1.id, "Пациент 1")
        await box_service.scan_card(box2.id, "Пациент 2")
        await box_service.close_box(box2.id, user_id=1)  # box2 закрыта, box1 остаётся открытой
        boxes = await box_service.list_open_boxes(1)
        assert len(boxes) == 1
        assert boxes[0]["id"] == box1.id

    async def test_cannot_close_empty_box(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        with pytest.raises(ValueError, match="пуст"):
            await box_service.close_box(box.id)

    async def test_close_box_with_cards(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.scan_card(box.id, "Пациент 1")
        await box_service.close_box(box.id)

        closed = await box_service.box_repo.get_by_id(box.id)
        assert closed.status == BoxStatus.ready

    async def test_close_foreign_box_raises(self, box_service, db):
        await _seed_branch_and_user(db)
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (2, 'u2', 'h', 'branch')"
        )
        await db.conn.commit()
        box = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.scan_card(box.id, "Пациент 1")
        with pytest.raises(ValueError):
            await box_service.close_box(box.id, user_id=2)
        # статус не изменился
        closed = await box_service.box_repo.get_by_id(box.id)
        assert closed.status == BoxStatus.packing

    async def test_remove_card_from_box(self, box_service, db):
        """Удаление карты из коробки: карта исчезает из содержимого, статус → in_branch."""
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        card = await box_service.scan_card(box.id, "Пациент 1")

        await box_service.remove_card(box.id, card.id)

        contents = await box_service.get_box_contents(box.id)
        assert len(contents) == 0
        card_after = await box_service.card_repo.get_by_id(card.id)
        assert card_after.status == CardStatus.in_branch

    async def test_remove_card_not_in_box_ignored(self, box_service, db):
        """Удаление карты, которой нет в коробке, — безопасный no-op."""
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        card = await box_service.scan_card(box.id, "Пациент 1")
        other_box = await box_service.create_box(branch_id=1, user_id=1)

        # Карта в другой коробке, а не в other_box — удаление из other_box ничего не меняет
        await box_service.remove_card(other_box.id, card.id)
        contents = await box_service.get_box_contents(other_box.id)
        assert len(contents) == 0
        card_after = await box_service.card_repo.get_by_id(card.id)
        assert card_after.status == CardStatus.in_box

    async def test_scan_card_in_another_box_raises(self, box_service, db):
        """Скан карты из другой активной коробки: CardInAnotherBoxError, ничего не меняется."""
        await _seed_branch_and_user(db)
        box1 = await box_service.create_box(branch_id=1, user_id=1)
        box2 = await box_service.create_box(branch_id=1, user_id=1)
        card = await box_service.scan_card(box1.id, "Иванов И.И.")

        with pytest.raises(CardInAnotherBoxError) as ei:
            await box_service.scan_card(box2.id, "Иванов И.И.")
        assert ei.value.card.id == card.id
        assert len(ei.value.other_boxes) == 1
        assert ei.value.other_boxes[0]["box_id"] == box1.id
        assert ei.value.other_boxes[0]["box_number"] == box1.box_number
        assert ei.value.other_boxes[0]["status"] == BoxStatus.packing

        # в box2 карта НЕ добавлена, статус карты не тронут, box1 цела
        contents2 = await box_service.get_box_contents(box2.id)
        assert len(contents2) == 0
        row = await db.fetch_one("SELECT status FROM cards WHERE id = ?", (card.id,))
        assert row["status"] == "in_box"
        contents1 = await box_service.get_box_contents(box1.id)
        assert len(contents1) == 1

    async def test_transfer_card_moves_between_boxes(self, box_service, db):
        """Перенос: старая коробка — removed_at, новая — добавлена, статус in_box, логи unpacked+packed."""
        await _seed_branch_and_user(db)
        box1 = await box_service.create_box(branch_id=1, user_id=1)
        box2 = await box_service.create_box(branch_id=1, user_id=1)
        card = await box_service.scan_card(box1.id, "Иванов И.И.")

        await box_service.transfer_card(box2.id, card.id)

        # старая коробка: запись мягко снята
        row = await db.fetch_one(
            "SELECT removed_at FROM box_cards WHERE box_id = ? AND card_id = ?",
            (box1.id, card.id),
        )
        assert row["removed_at"] is not None
        # новая коробка: карта на месте
        contents2 = await box_service.get_box_contents(box2.id)
        assert [c.id for c in contents2] == [card.id]
        assert contents2[0].status == CardStatus.in_box
        # статус карты in_box
        row = await db.fetch_one("SELECT status FROM cards WHERE id = ?", (card.id,))
        assert row["status"] == "in_box"
        # логи: packed (в box1) → unpacked (из box1) → packed (в box2)
        rows = await db.fetch_all(
            "SELECT action FROM card_movements WHERE card_id = ? ORDER BY id", (card.id,)
        )
        assert [r["action"] for r in rows] == ["packed", "unpacked", "packed"]

    async def test_transfer_card_without_other_boxes_raises(self, box_service, db):
        """Карта только в текущей коробке: других коробок нет — ValueError, ничего не меняется."""
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        card = await box_service.scan_card(box.id, "Иванов И.И.")

        with pytest.raises(ValueError):
            await box_service.transfer_card(box.id, card.id)

        row = await db.fetch_one(
            "SELECT removed_at FROM box_cards WHERE box_id = ? AND card_id = ?",
            (box.id, card.id),
        )
        assert row["removed_at"] is None
        card_after = await box_service.card_repo.get_by_id(card.id)
        assert card_after.status == CardStatus.in_box

    async def test_transfer_card_already_in_target_raises(self, box_service, db):
        """Карта уже числится в целевой коробке: ValueError, из других коробок не снимается."""
        await _seed_branch_and_user(db)
        box1 = await box_service.create_box(branch_id=1, user_id=1)
        box2 = await box_service.create_box(branch_id=1, user_id=1)
        card = await box_service.scan_card(box1.id, "Иванов И.И.")
        # остаток: карта числится и в box1, и в box2
        await box_service.box_repo.add_card(box2.id, card.id)

        with pytest.raises(ValueError):
            await box_service.transfer_card(box2.id, card.id)

        # box1 не тронута
        row = await db.fetch_one(
            "SELECT removed_at FROM box_cards WHERE box_id = ? AND card_id = ?",
            (box1.id, card.id),
        )
        assert row["removed_at"] is None
