# tests/domain/test_models.py
from medarchive.domain.models import Card, Box, User, Branch, Courier
from medarchive.domain.statuses import CardStatus, BoxStatus, UserRole


class TestCard:
    def test_create_card_defaults(self):
        card = Card(id=1, full_name="Иванов Иван Иванович")
        assert card.id == 1
        assert card.full_name == "Иванов Иван Иванович"
        assert card.status == CardStatus.in_branch
        assert card.created_at is not None

    def test_create_card_explicit_status(self):
        card = Card(id=2, full_name="Петров Пётр Петрович", status=CardStatus.placed)
        assert card.status == CardStatus.placed


class TestBox:
    def test_create_box_defaults(self):
        box = Box(id=1, box_number="B-0001", origin_branch_id=1, created_by=1)
        assert box.box_number == "B-0001"
        assert box.status == BoxStatus.packing

    def test_create_box_explicit_status(self):
        box = Box(id=2, box_number="B-0002", origin_branch_id=2, created_by=1, status=BoxStatus.ready)
        assert box.status == BoxStatus.ready


class TestUser:
    def test_create_user_with_branch(self):
        user = User(id=1, username="operator", password_hash="hash", role=UserRole.branch, branch_id=1)
        assert user.role == UserRole.branch
        assert user.branch_id == 1

    def test_create_user_without_branch(self):
        user = User(id=2, username="admin", password_hash="hash", role=UserRole.admin)
        assert user.branch_id is None

    def test_create_user_full_name_default(self):
        """full_name по умолчанию — пустая строка (совместимость со старыми вызовами)."""
        user = User(id=3, username="op", password_hash="hash", role=UserRole.branch)
        assert user.full_name == ""

    def test_create_user_explicit_full_name(self):
        user = User(id=4, username="op", password_hash="hash", role=UserRole.branch, full_name="Иванов Иван")
        assert user.full_name == "Иванов Иван"

    def test_user_from_row_without_full_name(self):
        """from_row с dict без ключа full_name не падает и даёт full_name="" (старые вызовы)."""
        user = User.from_row({
            "id": 5,
            "username": "legacy",
            "password_hash": "h",
            "role": "branch",
            "branch_id": None,
            "active_box_id": None,
        })
        assert user.username == "legacy"
        assert user.full_name == ""

    def test_user_from_row_with_full_name(self):
        """from_row читает full_name из строки БД."""
        user = User.from_row({
            "id": 6,
            "username": "modern",
            "password_hash": "h",
            "role": "branch",
            "branch_id": None,
            "active_box_id": None,
            "full_name": "Петров Пётр",
        })
        assert user.full_name == "Петров Пётр"


class TestBranch:
    def test_create_branch(self):
        branch = Branch(id=1, name="Филиал №1", code="F01")
        assert branch.code == "F01"


class TestCourier:
    def test_create_courier(self):
        courier = Courier(id=1, name="Иванов А.А.", phone="+79991234567")
        assert courier.phone == "+79991234567"

    def test_create_courier_without_phone(self):
        courier = Courier(id=2, name="Петров Б.Б.")
        assert courier.phone is None
