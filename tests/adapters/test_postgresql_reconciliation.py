"""E3 inventory regressions in the existing rollback-only Foundation fixture."""
from dataclasses import asdict
from uuid import uuid4

import pytest
from sqlalchemy import text

from medarchive.adapters.postgresql.reconciliation import inspect_reconciliation
from tests.adapters.test_postgresql_shipment_workflow import pg_session, seed_locations


@pytest.mark.asyncio
async def test_e3_inventory_returns_counts_without_changes(pg_session):
    connection = await pg_session.connection()
    before = await inspect_reconciliation(connection)
    assert all(type(value) is int and value >= 0 for value in asdict(before).values())
    assert before == await inspect_reconciliation(connection)
    assert before.requires_review == any(asdict(before).values())


@pytest.mark.asyncio
async def test_e3_inventory_detects_legacy_roles_and_duplicate_couriers(pg_session):
    connection = await pg_session.connection()
    before = await inspect_reconciliation(connection)
    marker = uuid4().hex
    await connection.execute(text("""
        INSERT INTO public.users (username, full_name, password_hash, role)
        VALUES (:name, 'Synthetic viewer', 'test-only', 'viewer')
    """), {"name": marker})
    await connection.execute(text("""
        INSERT INTO public.couriers (full_name, is_active)
        VALUES (:first, true), (:second, false)
    """), {"first": marker + " Test", "second": " " + marker.upper() + "  TEST "})
    after = await inspect_reconciliation(connection)
    expected = asdict(before)
    expected["viewer_accounts"] += 1
    expected["courier_duplicate_groups"] += 1
    assert asdict(after) == expected
    assert after.requires_review
    assert after == await inspect_reconciliation(connection)


@pytest.mark.asyncio
async def test_e3_inventory_detects_unmapped_request_and_individual_placement(pg_session):
    from medarchive.adapters.postgresql.models import ArchiveRequest, ArchiveShelf, Card, CardPlacement
    from datetime import date

    branch, _, archive, _, _, archivist, central_user, _ = await seed_locations(pg_session)
    connection = await pg_session.connection()
    before = await inspect_reconciliation(connection)
    card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                current_location_id=archive.id, status="active")
    shelf = ArchiveShelf(section_name="Synthetic", shelf_name=uuid4().hex)
    pg_session.add_all([card, shelf])
    await pg_session.flush()
    pg_session.add_all([
        ArchiveRequest(card_id=card.id, requested_by=central_user.id, status="new"),
        CardPlacement(card_id=card.id, shelf_id=shelf.id, placed_by=archivist.id),
    ])
    await pg_session.flush()
    after = await inspect_reconciliation(connection)
    expected = asdict(before)
    expected["legacy_requests"] += 1
    expected["active_individual_placements"] += 1
    assert asdict(after) == expected
    assert after.requires_review
