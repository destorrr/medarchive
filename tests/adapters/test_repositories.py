# tests/adapters/test_repositories.py
"""Тесты расширенных репозиториев: Branch + полный CRUD User/Courier."""
import pytest
from medarchive.adapters.sqlite.repositories import (
    BranchRepositoryImpl,
    UserRepositoryImpl,
    CourierRepositoryImpl,
)
from medarchive.domain.statuses import UserRole


@pytest.fixture
async def branch_repo(db):
    return BranchRepositoryImpl(db)


@pytest.fixture
async def user_repo(db):
    return UserRepositoryImpl(db)


@pytest.fixture
async def courier_repo(db):
    return CourierRepositoryImpl(db)


class TestBranchRepository:
    async def test_branch_create(self, branch_repo):
        branch = await branch_repo.create("Филиал А", "A01")
        assert branch.id is not None
        assert branch.name == "Филиал А"
        assert branch.code == "A01"

    async def test_branch_list_all(self, branch_repo):
        await branch_repo.create("Филиал Б", "B01")
        await branch_repo.create("Филиал А", "A01")
        branches = await branch_repo.list_all()
        assert len(branches) == 2
        assert [b.name for b in branches] == ["Филиал А", "Филиал Б"]

    async def test_branch_get_by_id(self, branch_repo):
        branch = await branch_repo.create("Филиал В", "C01")
        found = await branch_repo.get_by_id(branch.id)
        assert found is not None
        assert found.name == "Филиал В"
        assert found.code == "C01"

    async def test_branch_update(self, branch_repo):
        branch = await branch_repo.create("Филиал Г", "D01")
        await branch_repo.update(branch.id, name="Филиал Г-обновлённый")
        found = await branch_repo.get_by_id(branch.id)
        assert found.name == "Филиал Г-обновлённый"
        assert found.code == "D01"

        await branch_repo.update(branch.id, code="D02")
        found = await branch_repo.get_by_id(branch.id)
        assert found.code == "D02"
        assert found.name == "Филиал Г-обновлённый"

    async def test_branch_delete(self, branch_repo):
        branch = await branch_repo.create("Филиал Д", "E01")
        await branch_repo.delete(branch.id)
        assert await branch_repo.get_by_id(branch.id) is None


class TestUserRepository:
    async def test_user_list_all(self, user_repo):
        await user_repo.create("u1", "h1", "branch", None)
        await user_repo.create("u2", "h2", "admin", None)
        users = await user_repo.list_all()
        assert len(users) == 2
        assert {u.username for u in users} == {"u1", "u2"}

    async def test_user_update(self, user_repo):
        user = await user_repo.create("u1", "h1", "branch", None)
        await user_repo.update(user.id, username="u1-new", role=UserRole.admin)
        updated = await user_repo.get_by_id(user.id)
        assert updated.username == "u1-new"
        assert updated.role == UserRole.admin
        assert updated.password_hash == "h1"

    async def test_user_delete(self, user_repo):
        user = await user_repo.create("u1", "h1", "branch", None)
        await user_repo.delete(user.id)
        assert await user_repo.get_by_id(user.id) is None


class TestCourierRepository:
    async def test_courier_update(self, courier_repo):
        courier = await courier_repo.create("Иванов", "+79990000000")
        await courier_repo.update(courier.id, name="Иванов И.И.")
        updated = await courier_repo.get_by_id(courier.id)
        assert updated.name == "Иванов И.И."
        assert updated.phone == "+79990000000"

        await courier_repo.update(courier.id, phone="+71112223344")
        updated = await courier_repo.get_by_id(courier.id)
        assert updated.phone == "+71112223344"
        assert updated.name == "Иванов И.И."

    async def test_courier_delete(self, courier_repo):
        courier = await courier_repo.create("Петров")
        await courier_repo.delete(courier.id)
        assert await courier_repo.get_by_id(courier.id) is None
