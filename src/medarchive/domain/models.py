"""Доменные сущности (чистые dataclass'ы, без ORM)."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from medarchive.domain.statuses import (
    CardStatus, BoxStatus, UserRole
)


@dataclass
class Card:
    id: int
    full_name: str
    status: CardStatus = CardStatus.in_branch
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Box:
    id: int
    box_number: str
    origin_branch_id: int
    created_by: int
    status: BoxStatus = BoxStatus.packing
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Branch:
    id: int
    name: str
    code: str


@dataclass
class Courier:
    id: int
    name: str
    phone: str | None = None


@dataclass
class User:
    id: int
    username: str
    password_hash: str
    role: UserRole
    branch_id: int | None = None
    active_box_id: int | None = None
    full_name: str = ""

    @classmethod
    def from_row(cls, row) -> "User":
        # full_name может отсутствовать в старых dict-строках (вызовы до миграции) —
        # безопасно читаем только при наличии ключа.
        full_name = row["full_name"] if "full_name" in row.keys() else ""
        return cls(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            role=UserRole(row["role"]),
            branch_id=row["branch_id"],
            active_box_id=row["active_box_id"],
            full_name=full_name,
        )
