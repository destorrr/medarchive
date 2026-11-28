"""E4.4 installed reservation validator boundary on the legacy schema."""
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.adapters.test_request_item_history_guard import pg_session


@pytest.mark.asyncio
async def test_target_reservation_validator_missing_parent(pg_session):
    connection = await pg_session.connection()
    with pytest.raises(DBAPIError) as error:
        async with connection.begin_nested():
            await connection.execute(
                text("SELECT public.medarchive_validate_request_reservation(:id)"),
                {"id": uuid4()},
            )
    assert error.value.orig.sqlstate == "23514"
