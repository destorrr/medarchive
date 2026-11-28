"""Verify the installed trigger execution boundary without widening grants."""
import pytest
from sqlalchemy import text

from tests.adapters.test_postgresql_shipment_workflow import pg_session


@pytest.mark.asyncio
async def test_request_guard_execution_boundary(pg_session):
    c = await pg_session.connection()
    rows = (await c.execute(text("""
        SELECT p.proname, p.prosecdef, p.proconfig,
               EXISTS (
                   SELECT 1 FROM aclexplode(coalesce(p.proacl,
                       acldefault('f', p.proowner))) a
                   WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'
               ) AS public_execute
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname IN (
            'medarchive_guard_target_request_composition',
            'medarchive_check_target_request_deferred'
        )
    """))).all()
    assert len(rows) == 2
    for row in rows:
        assert row.prosecdef
        assert "search_path=pg_catalog, pg_temp" in row.proconfig
        assert not row.public_execute
