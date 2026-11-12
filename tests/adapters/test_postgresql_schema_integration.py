"""Read-only contract checks against the PostgreSQL schema created by Alembic."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_migrated_schema_contains_required_checks_and_logistics_indexes():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            checks = await connection.execute(
                text(
                    """
                    SELECT conname
                    FROM pg_constraint
                    WHERE contype = 'c'
                      AND connamespace = current_schema()::regnamespace
                    """
                )
            )
            check_names = set(checks.scalars())

            indexes = await connection.execute(
                text(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                    """
                )
            )
            index_names = set(indexes.scalars())

        assert {
            "locations_kind_check",
            "ck_location_branch_binding",
            "users_role_check",
            "ck_user_branch_binding",
            "cards_status_check",
            "ck_card_exactly_one_location",
            "boxes_status_check",
            "ck_box_exactly_one_location",
            "shipments_status_check",
            "ck_shipment_distinct_locations",
            "ck_shipment_handover_time",
            "ck_shipment_received_fields",
            "ck_shipment_exception_fields",
            "ck_shipment_cancelled_fields",
            "box_cycles_cycle_number_check",
            "archive_requests_status_check",
            "ck_box_cycle_dates",
            "ck_box_membership_dates",
            "ck_placement_dates",
        } <= check_names

        assert {
            "ix_cards_full_name",
            "ix_cards_current_location",
            "ix_cards_current_shipment",
            "ix_boxes_current_location",
            "uq_box_one_active_shipment",
            "ix_shipments_route_status",
            "uq_card_one_active_box",
            "uq_card_one_active_archive_request",
            "uq_box_one_active_placement",
            "ix_domain_events_aggregate",
            "ix_domain_events_occurred_at",
        } <= index_names
    finally:
        await engine.dispose()
