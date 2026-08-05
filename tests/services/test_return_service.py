import pytest
from medarchive.services.return_service import ReturnService
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl, CourierRepositoryImpl
from medarchive.domain.statuses import CardStatus, BoxStatus


@pytest.fixture
async def return_service(db):
    return ReturnService(db, CardRepositoryImpl(db), BoxRepositoryImpl(db), CourierRepositoryImpl(db))


async def _seed(db):
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (2, 'B2', 'B2')")
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) VALUES (1, 'a1', 'h', 'archive')")
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) VALUES (2, 'b1', 'h', 'branch', 2)")
    await db.execute("INSERT OR REPLACE INTO couriers (id, name) VALUES (1, 'K1')")
    await db.conn.commit()


class TestReturnService:
    async def test_return_single_card(self, return_service, db):
        await _seed(db)
        card_repo = CardRepositoryImpl(db)
        card = await card_repo.create("Возвращаемый Пациент")
        await card_repo.update_status(card.id, CardStatus.pending_return)

        shipment = await return_service.return_single_card(
            card_id=card.id, courier_id=1, to_branch_id=2, issued_by=1
        )
        assert shipment["kind"] == "card"
        assert shipment["card_id"] == card.id
        assert shipment["to_branch_id"] == 2

        updated = await card_repo.get_by_id(card.id)
        assert updated.status == CardStatus.in_transit

    async def test_return_as_box(self, return_service, db):
        await _seed(db)
        card_repo = CardRepositoryImpl(db)
        card = await card_repo.create("Пациент В Коробке")
        await card_repo.update_status(card.id, CardStatus.pending_return)

        shipment = await return_service.return_as_box(
            card_id=card.id, courier_id=1, issued_by=1
        )
        assert shipment is not None
        assert shipment["kind"] == "box"

        updated = await card_repo.get_by_id(card.id)
        assert updated.status == CardStatus.in_transit

    async def test_receive_return(self, return_service, db):
        await _seed(db)
        card_repo = CardRepositoryImpl(db)
        card = await card_repo.create("Пациент К Возврату")
        await card_repo.update_status(card.id, CardStatus.pending_return)

        await return_service.return_single_card(card.id, 1, 2, 1)
        # Now receive it
        await return_service.receive_return(card.id, user_id=2)

        updated = await card_repo.get_by_id(card.id)
        assert updated.status == CardStatus.in_branch

    async def test_cannot_return_card_not_pending(self, return_service, db):
        await _seed(db)
        card_repo = CardRepositoryImpl(db)
        card = await card_repo.create("Неготовый Пациент")
        # Status is in_branch (default) — NOT pending_return

        with pytest.raises(ValueError, match="pending"):
            await return_service.return_single_card(card.id, 1, 2, 1)
