import pytest
from medarchive.services.request_service import RequestService
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl
from medarchive.domain.statuses import CardStatus, RequestStatus


@pytest.fixture
async def request_service(db):
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    return RequestService(db, card_repo, box_repo)


async def _seed(db):
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (2, 'B2', 'B2')")
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) VALUES (1, 'u1', 'h', 'branch', 1)")
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) VALUES (2, 'arch1', 'h2', 'archive')")
    await db.execute("INSERT OR REPLACE INTO couriers (id, name) VALUES (1, 'K1')")
    await db.conn.commit()


class TestRequestService:
    async def test_create_request(self, request_service, db):
        await _seed(db)
        card_repo = CardRepositoryImpl(db)
        card = await card_repo.create("Пациент Для Заявки")
        req = await request_service.create_request(
            card_id=card.id, requested_by=1, target_branch_id=1, target_department=False
        )
        assert req is not None
        assert req["status"] == "new"
        assert req["card_id"] == card.id

    async def test_get_queue(self, request_service, db):
        await _seed(db)
        card_repo = CardRepositoryImpl(db)
        c1 = await card_repo.create("Пациент А")
        c2 = await card_repo.create("Пациент Б")
        await request_service.create_request(c1.id, 1, 1, False)
        await request_service.create_request(c2.id, 1, 1, False)
        queue = await request_service.get_queue()
        assert len(queue) == 2

    async def test_get_my_requests(self, request_service, db):
        await _seed(db)
        card_repo = CardRepositoryImpl(db)
        c1 = await card_repo.create("Пациент В")
        c2 = await card_repo.create("Пациент Г")
        await request_service.create_request(c1.id, 1, 1, False)
        await request_service.create_request(c2.id, 1, 1, False)
        mine = await request_service.get_my_requests(1)
        assert len(mine) == 2

    async def test_fulfill_request_removes_card_from_box(self, request_service, db):
        await _seed(db)
        card_repo = CardRepositoryImpl(db)
        box_repo = BoxRepositoryImpl(db)
        card = await card_repo.create("Пациент Д")
        box = await box_repo.create("B-0200", origin_branch_id=1, created_by=1)
        await box_repo.add_card(box.id, card.id)
        await card_repo.update_status(card.id, CardStatus.placed)

        req = await request_service.create_request(card.id, 1, 1, False)
        await request_service.fulfill_request(req["id"], user_id=2)

        # Card removed from box
        cards_in_box = await box_repo.get_cards(box.id)
        assert len(cards_in_box) == 0
        # Card status = pending_return
        updated = await card_repo.get_by_id(card.id)
        assert updated.status == CardStatus.pending_return
        # Request completed
        updated_req = await db.fetch_one("SELECT * FROM requests WHERE id = ?", (req["id"],))
        assert updated_req["status"] == "completed"
        assert updated_req["fulfilled_by"] == 2

    async def test_cancel_request(self, request_service, db):
        await _seed(db)
        card_repo = CardRepositoryImpl(db)
        card = await card_repo.create("Пациент Е")
        req = await request_service.create_request(card.id, 1, 1, False)
        await request_service.cancel_request(req["id"])
        updated = await db.fetch_one("SELECT status FROM requests WHERE id = ?", (req["id"],))
        assert updated["status"] == "cancelled"

    async def test_cannot_fulfill_completed_request(self, request_service, db):
        await _seed(db)
        card_repo = CardRepositoryImpl(db)
        box_repo = BoxRepositoryImpl(db)
        card = await card_repo.create("Пациент Ж")
        box = await box_repo.create("B-0300", origin_branch_id=1, created_by=1)
        await box_repo.add_card(box.id, card.id)
        await card_repo.update_status(card.id, CardStatus.placed)

        req = await request_service.create_request(card.id, 1, 1, False)
        await request_service.fulfill_request(req["id"], user_id=2)
        with pytest.raises(ValueError, match="completed"):
            await request_service.fulfill_request(req["id"], user_id=2)
