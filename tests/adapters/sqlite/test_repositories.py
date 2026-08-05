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

        await box_repo.add_card(box.id, card.id)
        # Second add should be ignored (no duplicate rows in box_cards)
        await box_repo.add_card(box.id, card.id)

        cards = await box_repo.get_cards(box.id)
        assert len(cards) == 1

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


class TestCourierRepository:
    async def test_create_and_list_couriers(self, courier_repo):
        await courier_repo.create("Иванов А.А.", "+79991234567")
        await courier_repo.create("Петров Б.Б.")

        couriers = await courier_repo.list_all()
        assert len(couriers) == 2
        assert couriers[0].name == "Иванов А.А."
