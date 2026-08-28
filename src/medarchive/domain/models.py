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
