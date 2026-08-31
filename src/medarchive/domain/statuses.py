"""Статусы сущностей и допустимые переходы."""
from enum import Enum


class CardStatus(str, Enum):
    in_branch = "in_branch"
    in_box = "in_box"
    in_transit = "in_transit"
    in_archive = "in_archive"
    placed = "placed"
    pending_return = "pending_return"


CARD_STATUS_TRANSITIONS: dict[CardStatus, set[CardStatus]] = {
    CardStatus.in_branch: {CardStatus.in_box},
    CardStatus.in_box: {CardStatus.in_transit},
    CardStatus.in_transit: {CardStatus.in_archive, CardStatus.in_branch},
    CardStatus.in_archive: {CardStatus.placed, CardStatus.pending_return},
    CardStatus.placed: {CardStatus.pending_return},
    CardStatus.pending_return: {CardStatus.in_transit},
}


CARD_STATUS_LABELS: dict[CardStatus, str] = {
    CardStatus.in_branch: "В филиале",
    CardStatus.in_box: "В коробке",
    CardStatus.in_transit: "В пути",
    CardStatus.in_archive: "В архиве",
    CardStatus.placed: "Размещена",
    CardStatus.pending_return: "Возврат",
}


class BoxStatus(str, Enum):
    packing = "packing"
    ready = "ready"
    in_transit = "in_transit"
    received = "received"
    placed = "placed"
    lost = "lost"


BOX_STATUS_TRANSITIONS: dict[BoxStatus, set[BoxStatus]] = {
    BoxStatus.packing: {BoxStatus.ready, BoxStatus.lost},
    BoxStatus.ready: {BoxStatus.in_transit, BoxStatus.packing},
    BoxStatus.in_transit: {BoxStatus.received, BoxStatus.lost},
    BoxStatus.received: {BoxStatus.placed},
    BoxStatus.placed: set(),
    BoxStatus.lost: set(),
}


class ShipmentStatus(str, Enum):
    issued = "issued"
    received = "received"
    lost = "lost"
    damaged = "damaged"


class RequestStatus(str, Enum):
    new = "new"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"
    not_found = "not_found"


class UserRole(str, Enum):
    branch = "branch"
    archive = "archive"
    viewer = "viewer"
    admin = "admin"
