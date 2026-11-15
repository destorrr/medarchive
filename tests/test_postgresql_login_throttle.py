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
