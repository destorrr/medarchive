"""Зависимости FastAPI: БД, текущий пользователь, проверка ролей."""
import uuid
from collections.abc import AsyncIterator

from fastapi import Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.config import Config
from medarchive.adapters.sqlite.database import Database as SQLiteDatabase
from medarchive.adapters.postgresql.database import Database as PostgreSQLDatabase
from medarchive.adapters.postgresql.repositories import UserRepository
from medarchive.adapters.postgresql.models import User as PostgreSQLUser
from medarchive.services.auth_service import AuthService
from medarchive.services.postgresql_auth_service import PostgreSQLAuthService
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole

_db: SQLiteDatabase | None = None
_pg_db: PostgreSQLDatabase | None = None
_auth: AuthService | None = None


def init_dependencies(config: Config) -> None:
    global _db, _pg_db, _auth
    _db = SQLiteDatabase(config)
    _pg_db = PostgreSQLDatabase(config.effective_database_url) if config.postgres_enabled else None
    _auth = AuthService(secret_key=config.effective_secret_key)


async def get_db() -> SQLiteDatabase:
    if _db is None:
        raise RuntimeError("Dependencies not initialized. Call init_dependencies first.")
    await _db.init()
    return _db


async def get_pg_database() -> PostgreSQLDatabase:
    if _pg_db is None:
        raise RuntimeError(
            "PostgreSQL adapter is disabled. Set MEDARCHIVE_POSTGRES_ENABLED=true."
        )
    return _pg_db


async def get_pg_session() -> AsyncIterator[AsyncSession]:
    """Provide a PostgreSQL session; business services own transaction boundaries."""
    db = await get_pg_database()
    async with db.session_factory() as session:
        yield session


def get_auth() -> AuthService:
    if _auth is None:
        raise RuntimeError("Dependencies not initialized. Call init_dependencies first.")
    return _auth


def get_pg_auth(
    session: AsyncSession = Depends(get_pg_session),
    credentials: AuthService = Depends(get_auth),
) -> PostgreSQLAuthService:
    return PostgreSQLAuthService(UserRepository(session), credentials)


async def get_current_user(request: Request) -> User:
    """Legacy SQLite principal; retained for routers not yet migrated."""
    auth = get_auth()
    token = request.cookies.get("session")
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = auth.verify_session_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid session")

    db = await get_db()
    row = await db.fetch_one(
        """SELECT u.*, b.name AS branch_name
           FROM users u
           LEFT JOIN branches b ON b.id = u.branch_id
           WHERE u.id = ?""",
        (payload["user_id"],),
    )
    if row is None:
        raise HTTPException(status_code=401, detail="User not found")

    user = User.from_row(row)
    user.branch_name = row["branch_name"]
    if user.role == UserRole.archive:
        cnt = await db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM requests WHERE status IN ('new','in_progress')"
        )
        user.active_requests_count = cnt["cnt"] if cnt else 0
    return user


async def get_pg_current_user(
    request: Request,
    session: AsyncSession = Depends(get_pg_session),
) -> PostgreSQLUser:
    """Resolve a UUID-based PostgreSQL principal and reject disabled accounts."""
    auth = get_auth()
    token = request.cookies.get("session")
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = auth.verify_session_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid session")
    try:
        user_id = uuid.UUID(str(payload["user_id"]))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid session") from None

    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def require_pg_role(*roles: str):
    """Authorize against target PostgreSQL role names, not legacy SQLite enums."""
    allowed = frozenset(roles)

    async def dependency(user: PostgreSQLUser = Depends(get_pg_current_user)):
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return dependency


def require_role(*roles: UserRole):
    """Legacy SQLite role guard."""
    async def dependency(user: User = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return dependency
