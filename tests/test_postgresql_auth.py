import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from medarchive.config import Config
from medarchive.dependencies import get_pg_current_user, init_dependencies
from medarchive.services.auth_service import AuthService
from medarchive.services.postgresql_auth_service import PostgreSQLAuthService


class FakeUsers:
    def __init__(self, user):
        self.user = user

    async def get_by_username(self, username):
        return self.user if self.user and self.user.username == username else None


@pytest.mark.asyncio
async def test_postgresql_auth_service_accepts_active_user_with_valid_password():
    credentials = AuthService("test-secret")
    user = SimpleNamespace(
        id=uuid.uuid4(),
        username="central",
        password_hash=credentials.hash_password("correct horse"),
        role="central_office",
        is_active=True,
    )
    service = PostgreSQLAuthService(FakeUsers(user), credentials)

    assert await service.authenticate("central", "correct horse") is user
    assert await service.authenticate("central", "wrong") is None
    assert await service.authenticate("missing", "correct horse") is None


@pytest.mark.asyncio
async def test_postgresql_auth_service_rejects_inactive_user():
    credentials = AuthService("test-secret")
    user = SimpleNamespace(
        id=uuid.uuid4(),
        username="disabled",
        password_hash=credentials.hash_password("password"),
        role="archive",
        is_active=False,
    )
    service = PostgreSQLAuthService(FakeUsers(user), credentials)

    assert await service.authenticate("disabled", "password") is None


def make_request(token=None):
    headers = []
    if token:
        headers.append((b"cookie", f"session={token}".encode()))
    return Request({"type": "http", "headers": headers, "method": "GET", "path": "/"})


@pytest.mark.asyncio
async def test_postgresql_current_user_resolves_uuid_and_rejects_inactive():
    config = Config(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/medarchive",
        postgres_enabled=True,
        secret_key="test-secret",
    )
    init_dependencies(config)
    user_id = uuid.uuid4()
    user = SimpleNamespace(id=user_id, username="archive", role="archive", is_active=True)
    credentials = AuthService("test-secret")
    token = credentials.create_session_token(user)
    session = AsyncMock()
    session.get.return_value = user

    assert await get_pg_current_user(make_request(token), session) is user
    session.get.assert_awaited_once()

    session.get.return_value = SimpleNamespace(
        id=user_id, username="disabled", role="archive", is_active=False
    )
    with pytest.raises(HTTPException) as error:
        await get_pg_current_user(make_request(token), session)
    assert error.value.status_code == 401
    await (await __import__(
        "medarchive.dependencies", fromlist=["get_pg_database"]
    ).get_pg_database()).close()


@pytest.mark.asyncio
async def test_postgresql_current_user_rejects_legacy_integer_session_id():
    config = Config(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/medarchive",
        postgres_enabled=True,
        secret_key="test-secret",
    )
    init_dependencies(config)
    credentials = AuthService("test-secret")
    token = credentials._serializer.dumps({"user_id": 42, "username": "old", "role": "branch"})
    with pytest.raises(HTTPException) as error:
        await get_pg_current_user(make_request(token), AsyncMock())
    assert error.value.status_code == 401
    from medarchive.dependencies import get_pg_database
    await (await get_pg_database()).close()
