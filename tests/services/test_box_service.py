import pytest
from medarchive.services.box_service import BoxService
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

    async def test_scan_duplicate_in_same_box_ignored(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.scan_card(box.id, "Дубль")
        await box_service.scan_card(box.id, "Дубль")

        contents = await box_service.get_box_contents(box.id)
        assert len(contents) == 1

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
