"""Интеграционные тесты: MovementService в существующих сервисах."""
import pytest
from medarchive.services.box_service import BoxService
from medarchive.services.receive_service import ReceiveService
from medarchive.services.request_service import RequestService
from medarchive.services.return_service import ReturnService
from medarchive.services.shipment_service import ShipmentService
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl, CourierRepositoryImpl
from medarchive.domain.statuses import CardStatus, BoxStatus


@pytest.fixture
async def card_repo(db):
    return CardRepositoryImpl(db)


@pytest.fixture
async def box_repo(db):
    return BoxRepositoryImpl(db)


async def _seed_branch_users_courier(db):
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role) VALUES (0, 'system', 'h', 'admin')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'branch1', 'h', 'branch', 1)"
    )
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role) "
        "VALUES (2, 'arch1', 'h', 'archive')"
    )
    await db.execute("INSERT OR REPLACE INTO couriers (id, name) VALUES (1, 'K1')")
    await db.execute("INSERT OR REPLACE INTO archive_sections (id, name) VALUES (1, 'S1')")
    await db.execute("INSERT OR REPLACE INTO archive_shelves (id, section_id, name) VALUES (1, 1, 'Полка 1')")
    await db.conn.commit()


class TestMovementIntegration:
    async def test_movement_logged_on_pack(self, db, card_repo, box_repo):
        """Упаковать карту → запись 'packed' в card_movements."""
        await _seed_branch_users_courier(db)
        service = BoxService(db, card_repo, box_repo)
        box = await service.create_box(branch_id=1, user_id=1)

        await service.scan_card(box.id, "Пациент Упаковка")

        rows = await db.fetch_all("SELECT * FROM card_movements")
        assert len(rows) == 1
        assert rows[0]["action"] == "packed"
        assert rows[0]["to_location"] == f"box {box.box_number}"

    async def test_movement_logged_on_receive(self, db, card_repo, box_repo):
        """Принять коробку → запись 'received'."""
        await _seed_branch_users_courier(db)

        # Pack and transfer
        box_service = BoxService(db, card_repo, box_repo)
        box = await box_service.create_box(branch_id=1, user_id=1)
        card = await box_service.scan_card(box.id, "Пациент Приёмка")
        await box_service.close_box(box.id)

        courier_repo = CourierRepositoryImpl(db)
        shipment_service = ShipmentService(db, box_repo, courier_repo, card_repo)
        await shipment_service.transfer_box(box.id, courier_id=1, issued_by=1)

        # Receive
        receive_service = ReceiveService(db, box_repo, card_repo)
        await receive_service.receive_box(box.id, user_id=2)

        rows = await db.fetch_all(
            "SELECT * FROM card_movements WHERE action = 'received'"
        )
        assert len(rows) == 1
        assert rows[0]["action"] == "received"
        assert rows[0]["to_location"] == "archive"
        assert rows[0]["card_id"] == card.id

    async def test_full_chain_has_complete_history(self, db, card_repo, box_repo):
        """pack → receive → place → retrieve → return → 5 записей."""
        await _seed_branch_users_courier(db)

        # 1. Pack
        box_service = BoxService(db, card_repo, box_repo)
        box = await box_service.create_box(branch_id=1, user_id=1)
        card = await box_service.scan_card(box.id, "Пациент Полный Цикл")
        await box_service.close_box(box.id)

        # 2. Transfer to courier
        courier_repo = CourierRepositoryImpl(db)
        shipment_service = ShipmentService(db, box_repo, courier_repo, card_repo)
        await shipment_service.transfer_box(box.id, courier_id=1, issued_by=1)

        # 3. Receive
        receive_service = ReceiveService(db, box_repo, card_repo)
        await receive_service.receive_box(box.id, user_id=2)

        # 4. Place (with user_id)
        await receive_service.place_box(box.id, shelf_id=1, user_id=2)

        # 5. Create and fulfill request
        request_service = RequestService(db, card_repo, box_repo)
        await request_service.create_request(
            card_id=card.id, requested_by=1, target_branch_id=1, target_department=False
        )
        reqs = await request_service.get_queue()
        await request_service.fulfill_request(reqs[0]["id"], user_id=2)

        # 6. Return (via Return service)
        return_service = ReturnService(db, card_repo, box_repo, courier_repo)
        await return_service.return_single_card(
            card_id=card.id, courier_id=1, to_branch_id=1, issued_by=2
        )
        # Card is now in_transit, receive_return needs it
        await return_service.receive_return(card.id, user_id=1)

        # Verify: 5 movements
        rows = await db.fetch_all(
            "SELECT * FROM card_movements WHERE card_id = ? ORDER BY id",
            (card.id,),
        )
        actions = [r["action"] for r in rows]
        assert "packed" in actions
        assert "received" in actions
        assert "placed" in actions
        assert "retrieved_for_return" in actions
        assert "returned" in actions
        assert len(rows) >= 5
