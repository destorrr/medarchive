"""Rehearse transactional DDL and rollback without changing the Alembic head."""
import pytest
from sqlalchemy import text

from medarchive.adapters.postgresql.target_transport_cutover import install_transport_constraints
from tests.adapters.test_postgresql_shipment_workflow import pg_session


@pytest.mark.asyncio
async def test_transport_ddl_rehearsal_rolls_back(pg_session):
    c = await pg_session.connection()
    async def has_fk():
        return await c.scalar(text("""
            SELECT EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid='public.cards'::regclass
                AND conname='fk_card_shipment_manifest')
        """))
    async def target_triggers():
        return await c.scalar(text("""
            SELECT count(*) FROM pg_trigger
            WHERE tgname IN ('trg_cards_target_transport_pointer',
                             'trg_boxes_target_transport_pointer')
              AND tgdeferrable AND tginitdeferred AND tgenabled='O'
        """))
    assert await has_fk()
    assert await target_triggers() == 0
    savepoint = await c.begin_nested()
    try:
        await install_transport_constraints(c)
        assert not await has_fk()
        assert await target_triggers() == 2
        assert await c.scalar(text("""
            SELECT pg_get_constraintdef(oid) LIKE '%partially_received%'
            FROM pg_constraint WHERE conrelid='public.shipments'::regclass
            AND conname='shipments_status_check'
        """))
    finally:
        await savepoint.rollback()
    assert await has_fk()
    assert await target_triggers() == 0
