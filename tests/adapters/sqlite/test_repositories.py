import pytest
from medarchive.adapters.sqlite.repositories import (
    CardRepositoryImpl,
    BoxRepositoryImpl,
    UserRepositoryImpl,
    CourierRepositoryImpl,
)
from medarchive.domain.statuses import CardStatus, BoxStatus, UserRole


@pytest.fixture
async def card_repo(db):
    return CardRepositoryImpl(db)


@pytest.fixture
async def box_repo(db):
    return BoxRepositoryImpl(db)


@pytest.fixture
async def user_repo(db):
    return UserRepositoryImpl(db)


@pytest.fixture
async def courier_repo(db):
    return CourierRepositoryImpl(db)


class TestCardRepository:
    async def test_create_and_find_card(self, card_repo):
        card = await card_repo.create("Иванов Иван Иванович")
        assert card.id is not None
        assert card.full_name == "Иванов Иван Иванович"
        assert card.status == CardStatus.in_branch

        found = await card_repo.get_by_id(card.id)
        assert found is not None
        assert found.full_name == card.full_name

    async def test_find_cards_by_name(self, card_repo):
        await card_repo.create("Петров Пётр Петрович")
        await card_repo.create("Петров Пётр Петрович")  # duplicate name
        cards = await card_repo.find_by_name("Петров Пётр Петрович")
        assert len(cards) == 2

    async def test_update_card_status(self, card_repo):
        card = await card_repo.create("Сидоров Сидор Сидорович")
        await card_repo.update_status(card.id, CardStatus.placed)
        updated = await card_repo.get_by_id(card.id)
        assert updated.status == CardStatus.placed


class TestBoxRepository:
    async def test_create_box_and_add_cards(self, box_repo, card_repo, db):
        # Need a branch and user first
        await db.execute("INSERT INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
        )
        await db.conn.commit()

        box = await box_repo.create("B-0001", origin_branch_id=1, created_by=1)
        assert box.box_number == "B-0001"
        assert box.status == BoxStatus.packing

        card1 = await card_repo.create("Карта 1")
        card2 = await card_repo.create("Карта 2")

        await box_repo.add_card(box.id, card1.id)
        await box_repo.add_card(box.id, card2.id)

        cards = await box_repo.get_cards(box.id)
        assert len(cards) == 2

    async def test_add_duplicate_card_to_box_ignored(self, box_repo, card_repo, db):
        await db.execute("INSERT INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
        )
        await db.conn.commit()

        box = await box_repo.create("B-0002", origin_branch_id=1, created_by=1)
        card = await card_repo.create("Дубль Карта")

        added1 = await box_repo.add_card(box.id, card.id)
        added2 = await box_repo.add_card(box.id, card.id)
        assert added1 is True
        assert added2 is False

        cards = await box_repo.get_cards(box.id)
        assert len(cards) == 1

    async def test_set_active_box_and_priority(self, box_repo, db):
        await db.execute("INSERT INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
        )
        await db.conn.commit()
        box1 = await box_repo.create("B-0101", origin_branch_id=1, created_by=1)
        box2 = await box_repo.create("B-0102", origin_branch_id=1, created_by=1)

        # Без выбора — fallback на последнюю открытую
        active = await box_repo.get_active_for_user(1)
        assert active.id == box2.id

        # Явный выбор имеет приоритет
        await box_repo.set_active_box(1, box1.id)
        active = await box_repo.get_active_for_user(1)
        assert active.id == box1.id

        # Выбранная закрыта → fallback на последнюю открытую
        await box_repo.update_status(box1.id, BoxStatus.ready)
        active = await box_repo.get_active_for_user(1)
        assert active.id == box2.id

        # Сброс выбора
        await box_repo.set_active_box(1, None)
        active = await box_repo.get_active_for_user(1)
        assert active.id == box2.id

    async def test_list_open_for_user(self, box_repo, card_repo, db):
        await db.execute("INSERT INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
        )
        await db.conn.commit()
        box1 = await box_repo.create("B-0201", origin_branch_id=1, created_by=1)
        box2 = await box_repo.create("B-0202", origin_branch_id=1, created_by=1)
        await box_repo.update_status(box2.id, BoxStatus.ready)  # закрыта — не в списке
        card = await card_repo.create("Карта")
        await box_repo.add_card(box1.id, card.id)

        boxes = await box_repo.list_open_for_user(1)
        assert len(boxes) == 1
        assert boxes[0]["box_number"] == "B-0201"
        assert boxes[0]["cards_count"] == 1
        assert "created_at" in boxes[0]

    async def test_update_box_status(self, box_repo, db):
        await db.execute("INSERT INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
        )
        await db.conn.commit()

        box = await box_repo.create("B-0003", origin_branch_id=1, created_by=1)
        await box_repo.update_status(box.id, BoxStatus.ready)
        updated = await box_repo.get_by_id(box.id)
        assert updated.status == BoxStatus.ready


class TestUserRepository:
    async def test_get_user_by_username(self, user_repo, db):
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (10, 'admin1', 'hash123', 'admin')"
        )
        await db.conn.commit()

        user = await user_repo.get_by_username("admin1")
        assert user is not None
        assert user.username == "admin1"
        assert user.role == UserRole.admin

    async def test_get_nonexistent_user(self, user_repo):
        user = await user_repo.get_by_username("nobody")
        assert user is None

    async def test_user_active_box_id_round_trip(self, user_repo, db):
        await db.execute("INSERT INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
        )
        await db.execute(
            "INSERT INTO boxes (box_number, origin_branch_id, created_by) VALUES ('B-9001', 1, 1)"
        )
        await db.execute(
            "UPDATE users SET active_box_id = (SELECT id FROM boxes WHERE box_number = 'B-9001') WHERE id = 1"
        )
        await db.conn.commit()
        row = await db.fetch_one("SELECT id FROM boxes WHERE box_number = 'B-9001'")

        user = await user_repo.get_by_username("u1")
        assert user.active_box_id == row["id"]
        user2 = await user_repo.get_by_id(1)
        assert user2.active_box_id == row["id"]

    async def test_user_list_all_keeps_active_box_id(self, user_repo, db):
        await db.execute("INSERT INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
        )
        await db.execute(
            "INSERT INTO boxes (box_number, origin_branch_id, created_by) VALUES ('B-9001', 1, 1)"
        )
        await db.execute(
            "UPDATE users SET active_box_id = (SELECT id FROM boxes WHERE box_number = 'B-9001') WHERE id = 1"
        )
        await db.conn.commit()
        users = await user_repo.list_all()
        assert any(u.id == 1 and u.active_box_id is not None for u in users)

    async def test_user_create_with_full_name_round_trip(self, user_repo):
        """create с full_name возвращает User с ФИО; round-trip через get_by_username."""
        user = await user_repo.create("u_fn", "h", "branch", None, full_name="Иванов Иван Иванович")
        assert user.full_name == "Иванов Иван Иванович"
        got = await user_repo.get_by_username("u_fn")
        assert got is not None
        assert got.full_name == "Иванов Иван Иванович"

    async def test_user_create_without_full_name_defaults_empty(self, user_repo):
        """create без full_name (старые вызовы) даёт пустую строку."""
        user = await user_repo.create("u_fn_empty", "h", "branch", None)
        assert user.full_name == ""

    async def test_user_update_full_name(self, user_repo):
        """update с full_name сохраняет ФИО; None не трогает колонку."""
        user = await user_repo.create("u_fn_upd", "h", "branch", None)
        assert user.full_name == ""
        await user_repo.update(user.id, full_name="Петров Пётр")
        got = await user_repo.get_by_id(user.id)
        assert got is not None
        assert got.full_name == "Петров Пётр"
        # None (не передаём) → колонка не меняется
        await user_repo.update(user.id, role="admin")
        got2 = await user_repo.get_by_id(user.id)
        assert got2 is not None
        assert got2.full_name == "Петров Пётр"


class TestCourierRepository:
    async def test_create_and_list_couriers(self, courier_repo):
        await courier_repo.create("Иванов А.А.", "+79991234567")
        await courier_repo.create("Петров Б.Б.")

        couriers = await courier_repo.list_all()
        assert len(couriers) == 2
        assert couriers[0].name == "Иванов А.А."
