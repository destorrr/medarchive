"""Зависимости FastAPI: БД, текущий пользователь, проверка ролей."""
from fastapi import Request, HTTPException, Depends
from medarchive.config import Config
from medarchive.adapters.sqlite.database import Database
from medarchive.services.auth_service import AuthService
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole

_db: Database | None = None
_auth: AuthService | None = None


def init_dependencies(config: Config) -> None:
    global _db, _auth
    _db = Database(config)
    _auth = AuthService(secret_key=config.effective_secret_key)


async def get_db() -> Database:
    if _db is None:
        raise RuntimeError("Dependencies not initialized. Call init_dependencies first.")
    # Идемпотентная инициализация: подключаемся при первом обращении,
    # даже если startup-событие ещё не отработало (например, в тестах).
    await _db.init()
    return _db


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
    row = await db.fetch_one("SELECT * FROM users WHERE id = ?", (payload["user_id"],))
    if row is None:
        raise HTTPException(status_code=401, detail="User not found")

    return User(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        role=UserRole(row["role"]),
        branch_id=row["branch_id"],
        active_box_id=row["active_box_id"],
    )


def require_role(*roles: UserRole):
    """Декоратор для проверки роли пользователя."""
    async def dependency(user: User = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return dependency
