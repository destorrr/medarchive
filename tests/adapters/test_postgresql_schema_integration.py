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
            "cards_custody_status_check",
            "ck_card_external_custody_fields",
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
            "ck_card_placement_dates",
        } <= check_names

        assert {
            "ix_cards_full_name",
            "ix_cards_current_location",
            "ix_cards_current_shipment",
            "ix_boxes_current_location",
            "ix_boxes_current_shipment",
            "ix_shipments_route_status",
            "uq_card_one_active_box",
            "uq_card_one_active_archive_request",
            "uq_box_one_active_placement",
            "ix_domain_events_aggregate",
            "ix_domain_events_occurred_at",
            "uq_card_one_active_placement",
            "ix_card_placements_shelf",
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
                (id, from_location_id, to_location_id, courier_id, status, issued_by)
            VALUES (:id, :source, :target, :courier, 'draft', :actor)
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
                    text("UPDATE shipments SET status = 'in_transit', handed_over_at = now() WHERE id = :id"),
                    {"id": shipment_id},
                )
                await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
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
                        'box_placements'::regclass, 'card_placements'::regclass,
                        'domain_events'::regclass
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
            "trg_box_cycle_history_guard",
            "trg_cards_no_delete",
            "trg_boxes_no_delete",
            "trg_shipments_no_delete",
            "trg_archive_requests_no_delete",
            "trg_card_placement_guard",
            "trg_card_leaving_shelf_guard",
            "trg_shipment_card_not_placed",
            "trg_box_membership_card_not_placed",
            "trg_box_placement_cards_not_placed",
            "trg_shipments_received_manifest",
            "trg_shipment_boxes_received_manifest",
            "trg_shipment_cards_received_manifest",
            "trg_cards_received_manifest",
            "trg_boxes_received_manifest",
            "trg_shipment_transition_metadata_immutable",
            "trg_archive_request_transition_metadata_immutable",
            "trg_cards_previous_shipment_pointer",
            "trg_boxes_previous_shipment_pointer",
            "trg_cards_movement_guard",
            "trg_boxes_movement_guard",
            "trg_cards_transit_insert_guard",
            "trg_boxes_transit_insert_guard",
            "trg_cards_current_shipment_status",
            "trg_boxes_current_shipment_status",
        } <= trigger_names
        assert "fulfillment_shipment_id" in archive_request_columns
        assert "ck_archive_request_fulfillment" in constraint_names
    finally:
        await engine.dispose()



@pytest.mark.asyncio
async def test_postgresql_preserves_exception_metadata_when_shipment_is_received():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                card_id, shipment_id = await _seed_transit_card(connection)
                shipment = (
                    await connection.execute(
                        text("SELECT issued_by, to_location_id FROM shipments WHERE id = :id"),
                        {"id": shipment_id},
                    )
                ).one()
                actor_id, target_location_id = shipment
                await connection.execute(
                    text("""
                        INSERT INTO shipment_cards (shipment_id, card_id, box_id)
                        VALUES (:shipment_id, :card_id, NULL)
                    """),
                    {"shipment_id": shipment_id, "card_id": card_id},
                )
                await connection.execute(
                    text("""
                        UPDATE cards
                        SET current_location_id = NULL, current_shipment_id = :shipment_id
                        WHERE id = :card_id
                    """),
                    {"shipment_id": shipment_id, "card_id": card_id},
                )
                await connection.execute(
                    text("""
                        UPDATE shipments
                        SET status = 'in_transit', handed_over_at = now()
                        WHERE id = :id
                    """),
                    {"id": shipment_id},
                )
                await connection.execute(
                    text("""
                        UPDATE shipments
                        SET status = 'exception', exception_by = :actor_id,
                            exception_at = now(), exception_note = 'Тестовый инцидент'
                        WHERE id = :id
                    """),
                    {"id": shipment_id, "actor_id": actor_id},
                )
                await connection.execute(
                    text("""
                        UPDATE cards
                        SET current_location_id = :target_id, current_shipment_id = NULL
                        WHERE id = :card_id
                    """),
                    {"target_id": target_location_id, "card_id": card_id},
                )
                await connection.execute(
                    text("""
                        UPDATE shipments
                        SET status = 'received', received_by = :actor_id, received_at = now()
                        WHERE id = :id
                    """),
                    {"id": shipment_id, "actor_id": actor_id},
                )
                await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                result = await connection.execute(
                    text("""
                        SELECT status, exception_by, exception_note
                        FROM shipments WHERE id = :id
                    """),
                    {"id": shipment_id},
                )
                status, exception_by, exception_note = result.one()
                assert status == "received"
                assert exception_by == actor_id
                assert exception_note == "Тестовый инцидент"
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_rejects_clearing_transit_pointer_before_receipt():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                card_id, shipment_id = await _seed_transit_card(connection)
                target_location_id = (
                    await connection.execute(
                        text("SELECT to_location_id FROM shipments WHERE id = :id"),
                        {"id": shipment_id},
                    )
                ).scalar_one()
                await connection.execute(
                    text("""
                        INSERT INTO shipment_cards (shipment_id, card_id, box_id)
                        VALUES (:shipment_id, :card_id, NULL)
                    """),
                    {"shipment_id": shipment_id, "card_id": card_id},
                )
                await connection.execute(
                    text("""
                        UPDATE cards
                        SET current_location_id = NULL, current_shipment_id = :shipment_id
                        WHERE id = :card_id
                    """),
                    {"shipment_id": shipment_id, "card_id": card_id},
                )
                await connection.execute(
                    text("""
                        UPDATE shipments
                        SET status = 'in_transit', handed_over_at = now()
                        WHERE id = :id
                    """),
                    {"id": shipment_id},
                )
                await connection.execute(
                    text("""
                        UPDATE cards
                        SET current_location_id = :target_id, current_shipment_id = NULL
                        WHERE id = :card_id
                    """),
                    {"target_id": target_location_id, "card_id": card_id},
                )
                with pytest.raises(IntegrityError):
                    await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_rejects_changing_card_location_without_a_shipment():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                card_id, shipment_id = await _seed_transit_card(connection)
                target_location_id = (
                    await connection.execute(
                        text("SELECT to_location_id FROM shipments WHERE id = :id"),
                        {"id": shipment_id},
                    )
                ).scalar_one()
                with pytest.raises(IntegrityError):
                    await connection.execute(
                        text("""
                            UPDATE cards SET current_location_id = :target_id
                            WHERE id = :card_id
                        """),
                        {"target_id": target_location_id, "card_id": card_id},
                    )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_rejects_handover_from_a_location_other_than_the_object_location():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                card_id, existing_shipment_id = await _seed_transit_card(connection)
                existing = (
                    await connection.execute(
                        text("""
                            SELECT from_location_id, to_location_id, courier_id, issued_by
                            FROM shipments WHERE id = :id
                        """),
                        {"id": existing_shipment_id},
                    )
                ).one()
                wrong_route_shipment_id = uuid.uuid4()
                await connection.execute(
                    text("""
                        INSERT INTO shipments
                            (id, from_location_id, to_location_id, courier_id, issued_by)
                        VALUES (:id, :source, :target, :courier, :actor)
                    """),
                    {
                        "id": wrong_route_shipment_id,
                        "source": existing.to_location_id,
                        "target": existing.from_location_id,
                        "courier": existing.courier_id,
                        "actor": existing.issued_by,
                    },
                )
                await connection.execute(
                    text("""
                        INSERT INTO shipment_cards (shipment_id, card_id, box_id)
                        VALUES (:shipment_id, :card_id, NULL)
                    """),
                    {"shipment_id": wrong_route_shipment_id, "card_id": card_id},
                )
                with pytest.raises(IntegrityError):
                    await connection.execute(
                        text("""
                            UPDATE cards
                            SET current_location_id = NULL, current_shipment_id = :shipment_id
                            WHERE id = :card_id
                        """),
                        {"shipment_id": wrong_route_shipment_id, "card_id": card_id},
                    )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()



@pytest.mark.asyncio
async def test_postgresql_rejects_transit_pointer_to_a_draft_shipment():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                card_id, shipment_id = await _seed_transit_card(connection)
                await connection.execute(
                    text("""
                        INSERT INTO shipment_cards (shipment_id, card_id, box_id)
                        VALUES (:shipment_id, :card_id, NULL)
                    """),
                    {"shipment_id": shipment_id, "card_id": card_id},
                )
                await connection.execute(
                    text("""
                        UPDATE cards
                        SET current_location_id = NULL, current_shipment_id = :shipment_id
                        WHERE id = :card_id
                    """),
                    {"shipment_id": shipment_id, "card_id": card_id},
                )
                with pytest.raises(IntegrityError):
                    await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
