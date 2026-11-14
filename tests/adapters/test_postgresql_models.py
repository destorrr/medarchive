"""Structural tests for the PostgreSQL SQLAlchemy mappings."""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from medarchive.adapters.postgresql.models import Base


def test_target_metadata_contains_core_and_logistics_tables():
    expected = {
        "branches", "locations", "users", "couriers", "cards", "boxes",
        "box_cycles", "box_card_memberships", "shipments", "shipment_boxes",
        "shipment_cards", "archive_requests", "archive_shelves",
        "box_placements", "card_placements", "domain_events",
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



def test_orm_maps_all_value_checks_and_full_text_index():
    expected_checks = {
        "locations_kind_check",
        "ck_location_branch_binding",
        "users_role_check",
        "ck_user_branch_binding",
        "cards_status_check",
        "ck_card_exactly_one_location",
        "boxes_status_check",
        "ck_box_exactly_one_location",
        "shipments_status_check",
        "box_cycles_cycle_number_check",
        "archive_requests_status_check",
        "ck_archive_request_fulfillment",
        "ck_card_placement_dates",
    }
    actual_checks = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert expected_checks <= actual_checks

    indexes = Base.metadata.tables["cards"].indexes
    full_text = next(index for index in indexes if index.name == "ix_cards_full_name")
    assert full_text.dialect_options["postgresql"]["using"] == "gin"
    assert "to_tsvector('simple', full_name)" in str(full_text.expressions[0])



def test_box_maps_partial_index_for_current_shipment_lookups():
    indexes = Base.metadata.tables["boxes"].indexes
    current_shipment = next(index for index in indexes if index.name == "ix_boxes_current_shipment")
    assert current_shipment.unique is False
    assert [column.name for column in current_shipment.columns] == ["current_shipment_id"]
    assert str(current_shipment.dialect_options["postgresql"]["where"]) == "current_shipment_id IS NOT NULL"


def test_card_custody_check_matches_the_current_status_vocabulary():
    table = Base.metadata.tables["cards"]
    constraint = next(c for c in table.constraints if c.name == "cards_custody_status_check")
    sqltext = str(constraint.sqltext)
    assert "'available'" in sqltext
    assert "'at_court'" in sqltext
    assert "'at_insurance'" in sqltext
    assert "'at_external_organization'" in sqltext
    assert "'at_other'" not in sqltext


def test_individual_card_placement_has_one_active_shelf_location():
    table = Base.metadata.tables["card_placements"]
    index = next(i for i in table.indexes if i.name == "uq_card_one_active_placement")
    assert index.unique is True
    assert [column.name for column in index.columns] == ["card_id"]
    assert str(index.dialect_options["postgresql"]["where"]) == "removed_at IS NULL"
