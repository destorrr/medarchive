from datetime import datetime

import pytest
from medarchive.services.shipment_service import ShipmentService
from medarchive.adapters.sqlite.repositories import (
    CardRepositoryImpl, BoxRepositoryImpl, CourierRepositoryImpl
)
from medarchive.domain.statuses import BoxStatus, CardStatus


@pytest.fixture
async def shipment_service(db):
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    courier_repo = CourierRepositoryImpl(db)
    return ShipmentService(db, box_repo, courier_repo, card_repo)


async def _seed_env(db):
    """Create branch, user, courier, box with cards."""
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'u1', 'h', 'branch', 1)"
    )
    await db.execute(
        "INSERT OR REPLACE INTO couriers (id, name, phone) VALUES (1, 'Курьер 1', '+79990000001')"
    )
    await db.conn.commit()

    card_repo = CardRepositoryImpl(db)
    card = await card_repo.create("Пациент Для Отправки")

    box_repo = BoxRepositoryImpl(db)
    box = await box_repo.create("B-0099", origin_branch_id=1, created_by=1)
    await box_repo.add_card(box.id, card.id)
    await box_repo.update_status(box.id, BoxStatus.ready)

    return box, card


class TestShipmentService:
    async def test_transfer_box_creates_shipment(self, shipment_service, db):
        box, _ = await _seed_env(db)
        shipment = await shipment_service.transfer_box(
            box_id=box.id, courier_id=1, issued_by=1
        )
        assert shipment is not None
        assert shipment["kind"] == "box"
        assert shipment["box_id"] == box.id
        assert shipment["courier_id"] == 1
        assert shipment["issued_by"] == 1
        assert shipment["status"] == "issued"

    async def test_transfer_box_issued_at_is_local_time(self, shipment_service, db):
        """Регрессия: issued_at должен быть в локальном времени (не UTC).

        SQLite DEFAULT (datetime('now')) возвращает UTC — раньше время
        отправки отставало на часовой пояс (16:36 вместо 23:36).
        """
        box, _ = await _seed_env(db)
        shipment = await shipment_service.transfer_box(
            box_id=box.id, courier_id=1, issued_by=1
        )
        issued = datetime.strptime(shipment["issued_at"], "%Y-%m-%d %H:%M:%S")
        diff = abs((datetime.now() - issued).total_seconds())
        assert diff < 60, (
            f"issued_at={shipment['issued_at']} не совпадает с локальным "
            f"временем {datetime.now()}: разница {diff:.0f}с "
            "(вероятно, записано UTC)"
        )

    async def test_transfer_updates_box_and_card_statuses(self, shipment_service, db):
        box, card = await _seed_env(db)
        await shipment_service.transfer_box(box_id=box.id, courier_id=1, issued_by=1)

        # Box should be in_transit
        updated_box = await shipment_service.box_repo.get_by_id(box.id)
        assert updated_box.status == BoxStatus.in_transit

        # Card should be in_transit
        updated_card = await shipment_service.card_repo.get_by_id(card.id)
        assert updated_card.status == CardStatus.in_transit

    async def test_cannot_transfer_box_not_ready(self, shipment_service, db):
        await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
        await db.execute(
            "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
            "VALUES (1, 'u1', 'h', 'branch', 1)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO couriers (id, name, phone) VALUES (1, 'K1', 'p')"
        )
        await db.conn.commit()

        box_repo = BoxRepositoryImpl(db)
        box = await box_repo.create("B-0100", origin_branch_id=1, created_by=1)
        # Box is still "packing" — not ready

        with pytest.raises(ValueError, match="ready"):
            await shipment_service.transfer_box(box_id=box.id, courier_id=1, issued_by=1)

    async def test_get_shipments_for_branch(self, shipment_service, db):
        box, _ = await _seed_env(db)
        await shipment_service.transfer_box(box_id=box.id, courier_id=1, issued_by=1)

        shipments = await shipment_service.get_shipments_for_branch(branch_id=1)
        assert len(shipments) == 1
        assert shipments[0]["box_id"] == box.id

    async def test_list_couriers(self, shipment_service, db):
        await db.execute(
            "INSERT OR REPLACE INTO couriers (id, name, phone) VALUES (1, 'C1', 'p1')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO couriers (id, name, phone) VALUES (2, 'C2', 'p2')"
        )
        await db.conn.commit()

        couriers = await shipment_service.list_couriers()
        assert len(couriers) == 2
        assert couriers[0].name == "C1"
