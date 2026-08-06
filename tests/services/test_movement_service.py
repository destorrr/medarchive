import pytest
from medarchive.services.movement_service import MovementService


@pytest.fixture
async def movement_service(db):
    return MovementService(db)


async def _seed(db):
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'u1', 'h', 'branch', 1)"
    )
    await db.execute(
        "INSERT OR REPLACE INTO cards (id, full_name, status) VALUES (1, 'Пациент Иванов', 'placed')"
    )
    await db.conn.commit()


class TestMovementService:
    async def test_log_movement_creates_record(self, movement_service, db):
        await _seed(db)
        await movement_service.log_movement(1, "issued", "Секция А / Полка 1", "Филиал 1", 1)
        row = await db.fetch_one("SELECT * FROM card_movements WHERE card_id = 1")
        assert row is not None
        assert row["action"] == "issued"
        assert row["from_location"] == "Секция А / Полка 1"
        assert row["to_location"] == "Филиал 1"
        assert row["user_id"] == 1

    async def test_get_card_history(self, movement_service, db):
        await _seed(db)
        await movement_service.log_movement(1, "issued", None, "Филиал 1", 1)
        await movement_service.log_movement(1, "received", "Филиал 1", None, 1)
        history = await movement_service.get_card_history(1)
        assert len(history) == 2
        actions = {h["action"] for h in history}
        assert actions == {"issued", "received"}
        assert all(h["card_id"] == 1 for h in history)

    async def test_get_recent_movements_with_card_name(self, movement_service, db):
        await _seed(db)
        await movement_service.log_movement(1, "issued", None, "Филиал 1", 1)
        recent = await movement_service.get_recent_movements(limit=10)
        assert len(recent) >= 1
        assert recent[0]["card_name"] == "Пациент Иванов"
        assert recent[0]["action"] == "issued"

    async def test_history_empty_for_new_card(self, movement_service, db):
        await _seed(db)
        history = await movement_service.get_card_history(1)
        assert history == []
