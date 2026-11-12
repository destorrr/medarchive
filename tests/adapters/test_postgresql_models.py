"""Structural tests for the PostgreSQL SQLAlchemy mappings."""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from medarchive.adapters.postgresql.models import Base


def test_target_metadata_contains_core_and_logistics_tables():
    expected = {
        "branches", "locations", "users", "couriers", "cards", "boxes",
        "box_cycles", "box_card_memberships", "shipments", "shipment_boxes",
        "shipment_cards", "archive_requests", "archive_shelves",
        "box_placements", "domain_events",
    }
    assert expected <= set(Base.metadata.tables)


def test_card_requires_exactly_one_current_location_representation():
    table = Base.metadata.tables["cards"]
    constraint = next(c for c in table.constraints if c.name == "ck_card_exactly_one_location")
    sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "current_location_id IS NOT NULL AND current_shipment_id IS NULL" in sql
    assert constraint.name == "ck_card_exactly_one_location"


def test_transit_card_must_reference_shipment_manifest():
    table = Base.metadata.tables["cards"]
    fk = next(c for c in table.foreign_key_constraints if c.name == "fk_card_shipment_manifest")
    assert [element.parent.name for element in fk.elements] == ["id", "current_shipment_id"]
    assert [element.target_fullname for element in fk.elements] == [
        "shipment_cards.card_id", "shipment_cards.shipment_id"
    ]
    assert fk.deferrable is True
    assert fk.initially == "DEFERRED"


def test_boxed_card_manifest_requires_matching_shipment_box():
    table = Base.metadata.tables["shipment_cards"]
    fk = next(c for c in table.foreign_key_constraints if c.name == "fk_shipment_card_containing_box")
    assert [element.parent.name for element in fk.elements] == ["shipment_id", "box_id"]
    assert [element.target_fullname for element in fk.elements] == [
        "shipment_boxes.shipment_id", "shipment_boxes.box_id"
    ]


def test_active_box_membership_is_unique_per_card():
    indexes = Base.metadata.tables["box_card_memberships"].indexes
    index = next(i for i in indexes if i.name == "uq_card_one_active_box")
    assert index.unique is True
    assert str(index.dialect_options["postgresql"]["where"]) == "removed_at IS NULL"
