import pytest
from medarchive.services.incident_service import IncidentService
from medarchive.adapters.sqlite.repositories import (
    CardRepositoryImpl, BoxRepositoryImpl
)
from medarchive.domain.statuses import BoxStatus, CardStatus


async def _seed_env(db):
    """Филиал, пользователь, курьер, потерянная коробка с двумя картами."""
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
    box_repo = BoxRepositoryImpl(db)

    box = await box_repo.create("B-LOST-1", origin_branch_id=1, created_by=1)
    c1 = await card_repo.create("Потерянный Пациент 1")
    c2 = await card_repo.create("Потерянный Пациент 2")
    await box_repo.add_card(box.id, c1.id)
    await box_repo.add_card(box.id, c2.id)
    # Карты были упакованы в коробку
    await card_repo.update_status(c1.id, CardStatus.in_box)
    await card_repo.update_status(c2.id, CardStatus.in_box)
    await box_repo.update_status(box.id, BoxStatus.lost)

    return box, [c1, c2]


class TestIncidentService:
    async def test_get_lost_boxes(self, db):
        box, cards = await _seed_env(db)

        # Ещё одна коробка, НЕ потерянная — не должна попасть в список
        box_repo = BoxRepositoryImpl(db)
        await box_repo.create("B-OK-1", origin_branch_id=1, created_by=1)

        service = IncidentService()
        lost = await service.get_lost_boxes(db)
        assert len(lost) == 1
        assert lost[0]["id"] == box.id
        assert lost[0]["status"] == "lost"
        assert len(lost[0]["cards"]) == 2
        names = {c["full_name"] for c in lost[0]["cards"]}
        assert names == {cards[0].full_name, cards[1].full_name}

    async def test_get_damaged_shipments(self, db):
        box, _ = await _seed_env(db)
        # Потерянная и повреждённая отправки
        await db.execute(
            """INSERT INTO shipments (kind, box_id, from_branch_id, courier_id, issued_by, status)
               VALUES ('box', ?, 1, 1, 1, 'lost')""",
            (box.id,),
        )
        await db.execute(
            """INSERT INTO shipments (kind, box_id, from_branch_id, courier_id, issued_by, status)
               VALUES ('box', ?, 1, 1, 1, 'damaged')""",
            (box.id,),
        )
        # Обычная отправка — не входит в список
        await db.execute(
            """INSERT INTO shipments (kind, box_id, from_branch_id, courier_id, issued_by, status)
               VALUES ('box', ?, 1, 1, 1, 'received')""",
            (box.id,),
        )
        await db.conn.commit()

        service = IncidentService()
        shipments = await service.get_damaged_shipments(db)
        assert len(shipments) == 2
        statuses = {s["status"] for s in shipments}
        assert statuses == {"lost", "damaged"}
        assert shipments[0]["box_number"] == box.box_number

    async def test_resolve_lost_box_return_cards_to_branch(self, db):
        box, cards = await _seed_env(db)

        service = IncidentService()
        await service.resolve_lost_box(db, box.id, "return_cards_to_branch")

        card_repo = CardRepositoryImpl(db)
        for card in cards:
            updated = await card_repo.get_by_id(card.id)
            assert updated.status == CardStatus.in_branch

    async def test_resolve_lost_box_mark_cards_lost_keeps_status(self, db):
        box, cards = await _seed_env(db)

        service = IncidentService()
        await service.resolve_lost_box(db, box.id, "mark_cards_lost")

        card_repo = CardRepositoryImpl(db)
        for card in cards:
            updated = await card_repo.get_by_id(card.id)
            assert updated.status == CardStatus.in_box  # статус не меняется

    async def test_resolve_card_status(self, db):
        box, cards = await _seed_env(db)

        service = IncidentService()
        await service.resolve_card_status(db, cards[0].id, "placed")

        card_repo = CardRepositoryImpl(db)
        updated = await card_repo.get_by_id(cards[0].id)
        assert updated.status == CardStatus.placed

    async def test_resolve_card_status_invalid_raises(self, db):
        box, cards = await _seed_env(db)

        service = IncidentService()
        with pytest.raises(ValueError):
            await service.resolve_card_status(db, cards[0].id, "not-a-status")
