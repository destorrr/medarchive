"""Зависимости FastAPI: БД, текущий пользователь, проверка ролей."""
from fastapi import Request, HTTPException, Depends
from medarchive.config import Config
from medarchive.adapters.sqlite.database import Database as SQLiteDatabase
from medarchive.adapters.postgresql.database import Database as PostgreSQLDatabase
from medarchive.services.auth_service import AuthService
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
    # Идемпотентная инициализация: подключаемся при первом обращении,
    # даже если startup-событие ещё не отработало (например, в тестах).
    await _db.init()
    return _db


async def get_pg_database() -> PostgreSQLDatabase:
    """Return the PostgreSQL pool when the staged adapter is enabled."""
    if _pg_db is None:
        raise RuntimeError(
            "PostgreSQL adapter is disabled. Set MEDARCHIVE_POSTGRES_ENABLED=true."
        )
    return _pg_db


async def get_pg_session():
    """Provide a PostgreSQL session; business services own transaction boundaries."""
    db = await get_pg_database()
    async with db.session_factory() as session:
        yield session


def get_auth() -> AuthService:
    if _auth is None:
        raise RuntimeError("Dependencies not initialized. Call init_dependencies first.")
    return _auth


async def get_current_user(request: Request) -> User:
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
    # Имя филиала из LEFT JOIN branches (None, если филиал не привязан).
    # Кладём динамическим атрибутом — dataclass User не меняем (совместимость).
    user.branch_name = row["branch_name"]
    # Счётчик активных заявок для сайдбара (раунд 2): только роль Архив,
    # пункт «Очередь заявок»; 0, если активных (new/in_progress) нет —
    # бейдж в шаблоне тогда не выводится.
    if user.role == UserRole.archive:
        cnt = await db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM requests WHERE status IN ('new','in_progress')"
        )
        user.active_requests_count = cnt["cnt"] if cnt else 0
    return user


def require_role(*roles: UserRole):
    """Декоратор для проверки роли пользователя."""
    async def dependency(user: User = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return dependency
