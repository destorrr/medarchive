"""Администрирование целевой PostgreSQL-модели.

Все изменения выполняются в транзакции вызывающего кода. Филиалы и пользователи
не удаляются физически: отключение сохраняет ссылки и историю операций.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    ArchiveShelf, BoxPlacement, Branch, CardPlacement, Courier, Location, Shipment, User,
)
from medarchive.adapters.postgresql.repositories import UserRepository


VALID_ROLES = {"branch", "central_office", "archive", "admin", "viewer"}


class PostgreSQLAdminService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.users = UserRepository(session)

    async def list_branches(self) -> list[Branch]:
        result = await self.session.execute(
            select(Branch).order_by(Branch.code, Branch.name)
        )
        return list(result.scalars().all())

    async def create_branch(self, *, code: str, name: str) -> Branch:
        code, name = code.strip(), name.strip()
        if not code or not name:
            raise ValueError("Код и название филиала обязательны")
        branch = Branch(code=code, name=name, is_active=True)
        self.session.add(branch)
        await self.session.flush()
        self.session.add(Location(
            kind="branch", branch_id=branch.id, name=name, is_active=True
        ))
        await self.session.flush()
        return branch

    async def update_branch(
        self, branch_id: uuid.UUID, *, code: str, name: str
    ) -> Branch:
        branch = await self.session.get(Branch, branch_id, with_for_update=True)
        if branch is None:
            raise LookupError("Филиал не найден")
        code, name = code.strip(), name.strip()
        if not code or not name:
            raise ValueError("Код и название филиала обязательны")
        branch.code, branch.name = code, name
        result = await self.session.execute(
            select(Location).where(
                Location.kind == "branch", Location.branch_id == branch.id
            ).with_for_update()
        )
        location = result.scalar_one_or_none()
        if location is None:
            raise RuntimeError("Для филиала отсутствует логистическая локация")
        location.name = name
        await self.session.flush()
        return branch

    async def set_branch_active(self, branch_id: uuid.UUID, active: bool) -> Branch:
        branch = await self.session.get(Branch, branch_id, with_for_update=True)
        if branch is None:
            raise LookupError("Филиал не найден")
        if not active:
            active_users = await self.session.scalar(
                select(func.count()).select_from(User).where(
                    User.branch_id == branch.id, User.is_active.is_(True)
                )
            )
            if active_users:
                raise ValueError("Сначала отключите пользователей этого филиала")
        branch.is_active = active
        result = await self.session.execute(
            select(Location).where(
                Location.kind == "branch", Location.branch_id == branch.id
            ).with_for_update()
        )
        location = result.scalar_one_or_none()
        if location is None:
            raise RuntimeError("Для филиала отсутствует логистическая локация")
        location.is_active = active
        await self.session.flush()
        return branch

    async def list_couriers(self) -> list[Courier]:
        result = await self.session.execute(
            select(Courier).order_by(Courier.full_name, Courier.phone)
        )
        return list(result.scalars().all())

    async def create_courier(
        self, *, full_name: str, phone: str | None = None
    ) -> Courier:
        full_name = full_name.strip()
        phone = (phone or "").strip() or None
        if not full_name:
            raise ValueError("ФИО курьера обязательно")
        courier = Courier(full_name=full_name, phone=phone, is_active=True)
        self.session.add(courier)
        await self.session.flush()
        return courier

    async def set_courier_active(
        self, courier_id: uuid.UUID, active: bool
    ) -> Courier:
        courier = await self.session.get(Courier, courier_id, with_for_update=True)
        if courier is None:
            raise LookupError("Курьер не найден")
        if not active:
            active_shipments = await self.session.scalar(
                select(func.count()).select_from(Shipment).where(
                    Shipment.courier_id == courier.id,
                    Shipment.status.in_(("draft", "in_transit")),
                )
            )
            if active_shipments:
                raise ValueError("Нельзя отключить курьера с активными отправлениями")
        courier.is_active = active
        await self.session.flush()
        return courier

    async def list_shelves(self) -> list[ArchiveShelf]:
        result = await self.session.execute(
            select(ArchiveShelf).order_by(
                ArchiveShelf.section_name, ArchiveShelf.shelf_name
            )
        )
        return list(result.scalars().all())

    async def create_shelf(
        self, *, section_name: str, shelf_name: str
    ) -> ArchiveShelf:
        section_name, shelf_name = section_name.strip(), shelf_name.strip()
        if not section_name or not shelf_name:
            raise ValueError("Секция и номер полки обязательны")
        shelf = ArchiveShelf(
            section_name=section_name, shelf_name=shelf_name, is_active=True
        )
        self.session.add(shelf)
        await self.session.flush()
        return shelf

    async def set_shelf_active(
        self, shelf_id: uuid.UUID, active: bool
    ) -> ArchiveShelf:
        shelf = await self.session.get(ArchiveShelf, shelf_id, with_for_update=True)
        if shelf is None:
            raise LookupError("Архивная полка не найдена")
        if not active:
            box_placements = await self.session.scalar(
                select(func.count()).select_from(BoxPlacement).where(
                    BoxPlacement.shelf_id == shelf.id,
                    BoxPlacement.removed_at.is_(None),
                )
            )
            card_placements = await self.session.scalar(
                select(func.count()).select_from(CardPlacement).where(
                    CardPlacement.shelf_id == shelf.id,
                    CardPlacement.removed_at.is_(None),
                )
            )
            if box_placements or card_placements:
                raise ValueError("Сначала снимите все коробки и карты с этой полки")
        shelf.is_active = active
        await self.session.flush()
        return shelf

    async def list_users(self) -> list[User]:
        result = await self.session.execute(
            select(User).order_by(User.username)
        )
        return list(result.scalars().all())

    async def create_user(
        self, *, username: str, full_name: str, password_hash: str,
        role: str, branch_id: uuid.UUID | None,
    ) -> User:
        await self.session.execute(text("SELECT pg_advisory_xact_lock(741928363)"))
        username, full_name = username.strip().lower(), full_name.strip()
        self._validate_user_fields(username, full_name, role, branch_id)
        if await self.users.get_by_username(username):
            raise ValueError("Пользователь с таким логином уже существует")
        if role == "branch":
            branch = await self.session.get(Branch, branch_id, with_for_update=True)
            if branch is None or not branch.is_active:
                raise ValueError("Выберите действующий филиал")
        return await self.users.create(
            username=username, full_name=full_name, password_hash=password_hash,
            role=role, branch_id=branch_id,
        )

    async def update_user(
        self, user_id: uuid.UUID, *, full_name: str, role: str,
        branch_id: uuid.UUID | None, password_hash: str | None = None,
        is_active: bool | None = None,
    ) -> User:
        # Acquire the shared user-mutation lock before any row lock. This gives
        # concurrent administrator role changes a consistent lock order and
        # prevents deadlocks while checking that one active admin remains.
        await self.session.execute(text("SELECT pg_advisory_xact_lock(741928363)"))
        user = await self.session.get(User, user_id, with_for_update=True)
        if user is None:
            raise LookupError("Пользователь не найден")
        full_name = full_name.strip()
        self._validate_user_fields(user.username, full_name, role, branch_id)
        if role == "branch":
            branch = await self.session.get(Branch, branch_id, with_for_update=True)
            if branch is None or not branch.is_active:
                raise ValueError("Выберите действующий филиал")
        next_active = user.is_active if is_active is None else is_active
        if user.role == "admin" and user.is_active and (
            role != "admin" or not next_active
        ):
            await self._ensure_another_active_admin(user.id)
        user.full_name, user.role, user.branch_id = full_name, role, branch_id
        user.is_active = next_active
        if password_hash:
            user.password_hash = password_hash
        await self.session.flush()
        return user

    async def _ensure_another_active_admin(self, excluded_id: uuid.UUID) -> None:
        admins = await self.session.execute(
            select(User.id).where(
                User.role == "admin", User.is_active.is_(True)
            ).with_for_update()
        )
        if len([admin_id for admin_id in admins.scalars() if admin_id != excluded_id]) == 0:
            raise ValueError("Нельзя отключить или лишить роли последнего активного администратора")

    @staticmethod
    def _validate_user_fields(
        username: str, full_name: str, role: str, branch_id: uuid.UUID | None
    ) -> None:
        if not username or not full_name:
            raise ValueError("Логин и ФИО обязательны")
        if role not in VALID_ROLES:
            raise ValueError("Недопустимая роль")
        if (role == "branch") != (branch_id is not None):
            raise ValueError("Для роли филиала обязателен филиал; для остальных ролей филиал не задаётся")
