"""PostgreSQL repositories.

Repositories share the caller's AsyncSession and never commit. A service/use-case
owns the transaction boundary through Database.transaction().
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import Card, Location, User


class CardRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, card_id: uuid.UUID) -> Card | None:
        return await self.session.get(Card, card_id)

    async def get_by_number(self, card_number: str) -> Card | None:
        result = await self.session.execute(
            select(Card).where(Card.card_number == card_number)
        )
        return result.scalar_one_or_none()

    async def search(self, query: str, *, limit: int = 50) -> list[Card]:
        """Search by card number or name; callers must still disambiguate results."""
        statement = (
            select(Card)
            .where(Card.card_number.ilike(f"%{query}%") | Card.full_name.ilike(f"%{query}%"))
            .order_by(Card.card_number)
            .limit(max(1, min(limit, 200)))
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def create(
        self,
        *,
        card_number: str,
        full_name: str,
        date_of_birth: date,
        origin_branch_id: uuid.UUID,
        current_location_id: uuid.UUID,
        phone: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> Card:
        card = Card(
            card_number=card_number,
            full_name=full_name,
            date_of_birth=date_of_birth,
            phone=phone,
            origin_branch_id=origin_branch_id,
            current_location_id=current_location_id,
            current_shipment_id=None,
            created_by=created_by,
        )
        self.session.add(card)
        await self.session.flush()
        return card


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_username(self, username: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def list_active(self) -> list[User]:
        result = await self.session.execute(
            select(User).where(User.is_active.is_(True)).order_by(User.username)
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        username: str,
        password_hash: str,
        full_name: str,
        role: str,
        branch_id: uuid.UUID | None = None,
    ) -> User:
        user = User(
            username=username,
            password_hash=password_hash,
            full_name=full_name,
            role=role,
            branch_id=branch_id,
        )
        self.session.add(user)
        await self.session.flush()
        return user


class LocationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, location_id: uuid.UUID) -> Location | None:
        return await self.session.get(Location, location_id)

    async def get_branch_location(self, branch_id: uuid.UUID) -> Location | None:
        result = await self.session.execute(
            select(Location).where(
                Location.kind == "branch",
                Location.branch_id == branch_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_singleton(self, kind: str) -> Location | None:
        if kind not in {"central_office", "archive"}:
            raise ValueError("Singleton location kind must be central_office or archive")
        result = await self.session.execute(
            select(Location).where(Location.kind == kind)
        )
        return result.scalar_one_or_none()

    async def list_active(self) -> list[Location]:
        result = await self.session.execute(
            select(Location)
            .where(Location.is_active.is_(True))
            .order_by(Location.kind, Location.name)
        )
        return list(result.scalars().all())
