import pytest
from medarchive.services.receive_service import ReceiveService
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl
from medarchive.domain.statuses import BoxStatus, CardStatus


@pytest.fixture
async def receive_service(db):
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    return ReceiveService(db, box_repo, card_repo)


async def _seed_transferred_box(db):
    """Create environment with a box that has been transferred to courier (in_transit)."""
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'u1', 'h', 'branch', 1)"
    )
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role) "
        "VALUES (2, 'archivist', 'h2', 'archive')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO couriers (id, name) VALUES (1, 'K1')"
    )
    await db.conn.commit()

    card_repo = CardRepositoryImpl(db)
    card = await card_repo.create("Пациент В Пути")
    card.status = CardStatus.in_transit
    await card_repo.update_status(card.id, CardStatus.in_transit)

    box_repo = BoxRepositoryImpl(db)
    box = await box_repo.create("B-0500", origin_branch_id=1, created_by=1)
    await box_repo.add_card(box.id, card.id)
    await box_repo.update_status(box.id, BoxStatus.in_transit)

    # Create shipment
    await db.execute(
        """INSERT INTO shipments (id, kind, box_id, from_branch_id, courier_id, issued_by, status)
           VALUES (100, 'box', ?, 1, 1, 1, 'issued')""",
        (box.id,),
    )
    await db.conn.commit()
    return box, card


async def _seed_archive_location(db):
    """Create section + shelf."""
    await db.execute("INSERT OR REPLACE INTO archive_sections (id, name) VALUES (1, 'Секция 1')")
    await db.execute(
        "INSERT OR REPLACE INTO archive_shelves (id, section_id, name, qr_code) VALUES (1, 1, 'Полка 3', 'S1-P3')"
    )
    await db.conn.commit()


class TestReceiveService:
    async def test_receive_box_updates_statuses(self, receive_service, db):
        box, card = await _seed_transferred_box(db)
        await receive_service.receive_box(box.id, user_id=2)

        updated_box = await receive_service.box_repo.get_by_id(box.id)
        assert updated_box.status == BoxStatus.received

        updated_card = await receive_service.card_repo.get_by_id(card.id)
        assert updated_card.status == CardStatus.in_archive

        # Shipment should be updated
        shipment = await db.fetch_one("SELECT * FROM shipments WHERE box_id = ?", (box.id,))
        assert shipment["status"] == "received"
        assert shipment["received_by"] == 2

    async def test_place_box_creates_placement(self, receive_service, db):
        box, card = await _seed_transferred_box(db)
        await receive_service.receive_box(box.id, user_id=2)
        await _seed_archive_location(db)

        await receive_service.place_box(box.id, shelf_id=1)

        placement = await db.fetch_one("SELECT * FROM box_placements WHERE box_id = ?", (box.id,))
        assert placement is not None
        assert placement["shelf_id"] == 1

    async def test_place_box_updates_card_statuses(self, receive_service, db):
        box, card = await _seed_transferred_box(db)
        await receive_service.receive_box(box.id, user_id=2)
        await _seed_archive_location(db)

        await receive_service.place_box(box.id, shelf_id=1)

        updated_card = await receive_service.card_repo.get_by_id(card.id)
        assert updated_card.status == CardStatus.placed

        updated_box = await receive_service.box_repo.get_by_id(box.id)
        assert updated_box.status == BoxStatus.placed

    async def test_get_pending_boxes(self, receive_service, db):
        box, _ = await _seed_transferred_box(db)
        pending = await receive_service.get_pending_boxes()
        assert len(pending) >= 1
        assert any(b["id"] == box.id for b in pending)

    async def test_cannot_place_unreceived_box(self, receive_service, db):
        box, _ = await _seed_transferred_box(db)
        await _seed_archive_location(db)
        # Box still in_transit, not received yet
        with pytest.raises(ValueError, match="received"):
            await receive_service.place_box(box.id, shelf_id=1)
