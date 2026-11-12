"""Read-only contract checks against the PostgreSQL schema created by Alembic."""
import os
import uuid

from sqlalchemy.exc import IntegrityError

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



async def _seed_transit_card(connection):
    branch_id, branch_location_id, central_location_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    user_id, courier_id, card_id, shipment_id = (uuid.uuid4() for _ in range(4))

    await connection.execute(
        text("INSERT INTO branches (id, code, name) VALUES (:id, :code, 'Test branch')"),
        {"id": branch_id, "code": f"IT-{branch_id.hex[:10]}"},
    )
    await connection.execute(
        text(
            """
            INSERT INTO locations (id, kind, branch_id, name)
            VALUES (:branch_location, 'branch', :branch_id, 'Test branch'),
                   (:central_location, 'central_office', NULL, 'Test central office')
            """
        ),
        {
            "branch_location": branch_location_id,
            "branch_id": branch_id,
            "central_location": central_location_id,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO users (id, username, password_hash, full_name, role)
            VALUES (:id, :username, 'test-hash', 'Integration Test', 'admin')
            """
        ),
        {"id": user_id, "username": f"it-{user_id.hex}"},
    )
    await connection.execute(
        text("INSERT INTO couriers (id, full_name) VALUES (:id, 'Test courier')"),
        {"id": courier_id},
    )
    await connection.execute(
        text(
            """
            INSERT INTO cards
                (id, card_number, full_name, date_of_birth, origin_branch_id, current_location_id)
            VALUES (:id, :number, 'Test Patient', DATE '1980-01-01', :branch_id, :location_id)
            """
        ),
        {
            "id": card_id,
            "number": f"IT-{card_id.hex}",
            "branch_id": branch_id,
            "location_id": branch_location_id,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO shipments
                (id, from_location_id, to_location_id, courier_id, status, issued_by, handed_over_at)
            VALUES (:id, :source, :target, :courier, 'in_transit', :actor, now())
            """
        ),
        {
            "id": shipment_id,
            "source": branch_location_id,
            "target": central_location_id,
            "courier": courier_id,
            "actor": user_id,
        },
    )
    return card_id, shipment_id


@pytest.mark.asyncio
async def test_postgresql_accepts_transit_card_only_with_matching_manifest_row():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                card_id, shipment_id = await _seed_transit_card(connection)
                await connection.execute(
                    text(
                        """
                        INSERT INTO shipment_cards (shipment_id, card_id, box_id)
                        VALUES (:shipment_id, :card_id, NULL)
                        """
                    ),
                    {"shipment_id": shipment_id, "card_id": card_id},
                )
                await connection.execute(
                    text(
                        """
                        UPDATE cards
                        SET current_location_id = NULL, current_shipment_id = :shipment_id
                        WHERE id = :card_id
                        """
                    ),
                    {"shipment_id": shipment_id, "card_id": card_id},
                )
                await connection.execute(
                    text("SET CONSTRAINTS fk_card_current_shipment, fk_card_shipment_manifest IMMEDIATE")
                )
                result = await connection.execute(
                    text("SELECT current_shipment_id FROM cards WHERE id = :id"),
                    {"id": card_id},
                )
                assert result.scalar_one() == shipment_id
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_rejects_transit_card_without_manifest_row():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                card_id, shipment_id = await _seed_transit_card(connection)
                await connection.execute(
                    text(
                        """
                        UPDATE cards
                        SET current_location_id = NULL, current_shipment_id = :shipment_id
                        WHERE id = :card_id
                        """
                    ),
                    {"shipment_id": shipment_id, "card_id": card_id},
                )
                with pytest.raises(IntegrityError):
                    await connection.execute(
                        text("SET CONSTRAINTS fk_card_shipment_manifest IMMEDIATE")
                    )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_workflow_guard_triggers_and_request_fulfillment_link_exist():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            triggers = await connection.execute(
                text(
                    """
                    SELECT tgname
                    FROM pg_trigger
                    WHERE NOT tgisinternal
                      AND tgrelid IN (
                        'shipments'::regclass, 'shipment_boxes'::regclass,
                        'shipment_cards'::regclass, 'cards'::regclass,
                        'boxes'::regclass, 'box_card_memberships'::regclass,
                        'box_cycles'::regclass, 'archive_requests'::regclass,
                        'box_placements'::regclass, 'domain_events'::regclass
                      )
                    """
                )
            )
            trigger_names = set(triggers.scalars())
            columns = await connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'archive_requests'
                    """
                )
            )
            archive_request_columns = set(columns.scalars())
            checks = await connection.execute(
                text(
                    """
                    SELECT conname FROM pg_constraint
                    WHERE conrelid = 'archive_requests'::regclass
                    """
                )
            )
            constraint_names = set(checks.scalars())

        assert {
            "trg_shipment_route_immutable",
            "trg_shipment_boxes_draft_only",
            "trg_shipment_cards_draft_only",
            "trg_box_membership_mutation_guard",
            "trg_cards_box_card_consistency",
            "trg_boxes_box_card_consistency",
            "trg_box_card_memberships_box_card_consistency",
            "trg_box_cycles_box_card_consistency",
            "trg_shipments_consistency",
            "trg_shipment_boxes_consistency",
            "trg_shipment_cards_consistency",
            "trg_cards_shipment_consistency",
            "trg_boxes_shipment_consistency",
            "trg_archive_request_guard",
            "trg_archive_placement_guard",
            "trg_box_leaving_shelf_guard",
            "trg_shipment_box_not_placed",
            "trg_domain_events_append_only",
        } <= trigger_names
        assert "fulfillment_shipment_id" in archive_request_columns
        assert "ck_archive_request_fulfillment" in constraint_names
    finally:
        await engine.dispose()
