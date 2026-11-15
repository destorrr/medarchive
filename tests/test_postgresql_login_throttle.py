import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from medarchive.services.postgresql_login_throttle import PostgreSQLLoginThrottle


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.asyncio
async def test_login_throttle_uses_hmac_keys_and_returns_retry_after():
    session = AsyncMock()
    session.execute.return_value = ScalarResult(73)
    throttle = PostgreSQLLoginThrottle(session, "a-test-secret")

    retry_after = await throttle.retry_after("Archive.User", "192.0.2.10")

    assert retry_after == 73
    params = session.execute.await_args.args[1]
    assert params["ip_key"] == hmac.new(
        b"a-test-secret", b"ip:192.0.2.10", hashlib.sha256
    ).hexdigest()
    assert params["identity_key"] == hmac.new(
        b"a-test-secret", b"identity-ip:archive.user\x00192.0.2.10", hashlib.sha256
    ).hexdigest()
    assert "Archive.User" not in repr(params)
    assert "192.0.2.10" not in repr(params)


@pytest.mark.asyncio
async def test_login_throttle_returns_none_when_not_blocked():
    session = AsyncMock()
    session.execute.return_value = ScalarResult(None)
    throttle = PostgreSQLLoginThrottle(session, "a-test-secret")

    assert await throttle.retry_after("user", "192.0.2.10") is None


@pytest.mark.asyncio
async def test_login_throttle_records_ip_and_identity_failures_atomically():
    session = AsyncMock()
    throttle = PostgreSQLLoginThrottle(session, "a-test-secret")

    await throttle.record_failure("user", "192.0.2.10")

    assert session.execute.await_count == 3
    first_params = session.execute.await_args_list[0].args[1]
    second_params = session.execute.await_args_list[1].args[1]
    assert first_params["failure_limit"] == throttle.IP_FAILURE_LIMIT
    assert second_params["failure_limit"] == throttle.IDENTITY_FAILURE_LIMIT
    assert first_params["throttle_key"] != second_params["throttle_key"]


@pytest.mark.asyncio
async def test_success_clears_only_username_ip_bucket():
    session = AsyncMock()
    throttle = PostgreSQLLoginThrottle(session, "a-test-secret")

    await throttle.clear_identity_failures("user", "192.0.2.10")

    params = session.execute.await_args.args[1]
    assert params["throttle_key"] == throttle._keys("user", "192.0.2.10")[1]

@pytest.mark.asyncio
async def test_login_throttle_locks_buckets_in_deterministic_order():
    session = AsyncMock()
    throttle = PostgreSQLLoginThrottle(session, "a-test-secret")

    await throttle.lock_buckets("user", "192.0.2.10")

    assert session.execute.await_count == 2
    lock_ids = [
        call.args[1]["lock_id"] for call in session.execute.await_args_list
    ]
    assert lock_ids == sorted(lock_ids)
    assert all(-(2**63) <= value < 2**63 for value in lock_ids)

@pytest.mark.asyncio
async def test_postgresql_login_throttle_persists_and_blocks_when_database_is_available():
    import asyncio
    import os
    import uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    database_url = os.environ.get("MEDARCHIVE_DATABASE_URL")
    if not database_url:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("GitHub Actions must provide MEDARCHIVE_DATABASE_URL")
        pytest.skip("PostgreSQL integration URL is not configured")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    username = f"throttle-test-{uuid.uuid4()}"
    client_ip = f"203.0.113.{uuid.uuid4().int % 254 + 1}"

    try:
        async with session_factory() as session:
            async with session.begin():
                throttle = PostgreSQLLoginThrottle(session, "integration-test-secret")
                await asyncio.wait_for(
                    throttle.lock_buckets(username, client_ip), timeout=10
                )
                assert await asyncio.wait_for(
                    throttle.retry_after(username, client_ip), timeout=10
                ) is None
                for _ in range(throttle.IDENTITY_FAILURE_LIMIT):
                    await asyncio.wait_for(
                        throttle.record_failure(username, client_ip), timeout=10
                    )

                retry_after = await asyncio.wait_for(
                    throttle.retry_after(username, client_ip), timeout=10
                )
                assert retry_after is not None
                assert 1 <= retry_after <= throttle.BLOCK_MINUTES * 60

                await asyncio.wait_for(
                    throttle.clear_identity_failures(username, client_ip), timeout=10
                )
                assert await asyncio.wait_for(
                    throttle.retry_after(username, client_ip), timeout=10
                ) is None
                ip_key, identity_key = throttle._keys(username, client_ip)
                from sqlalchemy import text
                await session.execute(
                    text("DELETE FROM auth_login_throttles WHERE throttle_key IN (:ip_key, :identity_key)"),
                    {"ip_key": ip_key, "identity_key": identity_key},
                )
    finally:
        await engine.dispose()
