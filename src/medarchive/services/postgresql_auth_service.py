"""Аутентификация PostgreSQL-пользователей целевой модели."""
from medarchive.adapters.postgresql.repositories import UserRepository
from medarchive.adapters.postgresql.models import User
from medarchive.services.auth_service import AuthService


class PostgreSQLAuthService:
    """Проверяет пароль и активность пользователя в PostgreSQL."""

    def __init__(self, users: UserRepository, credentials: AuthService):
        self.users = users
        self.credentials = credentials

    async def authenticate(self, username: str, password: str) -> User | None:
        user = await self.users.get_by_username(username)
        if user is None or not user.is_active:
            return None
        if not self.credentials.verify_password(password, user.password_hash):
            return None
        return user
