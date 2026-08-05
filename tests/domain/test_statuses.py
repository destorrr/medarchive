# tests/domain/test_statuses.py
import pytest
from medarchive.domain.statuses import (
    CardStatus,
    BoxStatus,
    ShipmentStatus,
    RequestStatus,
    UserRole,
    CARD_STATUS_TRANSITIONS,
    BOX_STATUS_TRANSITIONS,
)


class TestCardStatus:
    def test_all_statuses_defined(self):
        expected = {"in_branch", "in_box", "in_transit", "in_archive", "placed", "pending_return"}
        assert {s.value for s in CardStatus} == expected

    @pytest.mark.parametrize("from_s,to_s", [
        (CardStatus.in_branch, CardStatus.in_box),
        (CardStatus.in_box, CardStatus.in_transit),
        (CardStatus.in_transit, CardStatus.in_archive),
        (CardStatus.in_archive, CardStatus.placed),
        (CardStatus.placed, CardStatus.pending_return),
        (CardStatus.pending_return, CardStatus.in_transit),
        (CardStatus.in_transit, CardStatus.in_branch),
    ])
    def test_valid_transitions(self, from_s, to_s):
        assert to_s in CARD_STATUS_TRANSITIONS[from_s]

    @pytest.mark.parametrize("from_s,to_s", [
        (CardStatus.in_branch, CardStatus.placed),
        (CardStatus.placed, CardStatus.in_branch),
        (CardStatus.in_box, CardStatus.placed),
    ])
    def test_invalid_transitions(self, from_s, to_s):
        assert to_s not in CARD_STATUS_TRANSITIONS[from_s]


class TestBoxStatus:
    def test_all_statuses_defined(self):
        expected = {"packing", "ready", "in_transit", "received", "placed", "lost"}
        assert {s.value for s in BoxStatus} == expected

    @pytest.mark.parametrize("from_s,to_s", [
        (BoxStatus.packing, BoxStatus.ready),
        (BoxStatus.ready, BoxStatus.in_transit),
        (BoxStatus.in_transit, BoxStatus.received),
        (BoxStatus.received, BoxStatus.placed),
    ])
    def test_valid_transitions(self, from_s, to_s):
        assert to_s in BOX_STATUS_TRANSITIONS[from_s]


class TestShipmentStatus:
    def test_all_statuses_defined(self):
        expected = {"issued", "received", "lost", "damaged"}
        assert {s.value for s in ShipmentStatus} == expected


class TestRequestStatus:
    def test_all_statuses_defined(self):
        expected = {"new", "in_progress", "completed", "cancelled", "not_found"}
        assert {s.value for s in RequestStatus} == expected


class TestUserRole:
    def test_roles(self):
        expected = {"branch", "archive", "viewer", "admin"}
        assert {r.value for r in UserRole} == expected
