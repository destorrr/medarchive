"""Сервис аутентификации: хеширование паролей и сессионные токены."""
from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from medarchive.domain.models import User


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    def __init__(self, secret_key: str):
        self._serializer = URLSafeTimedSerializer(secret_key)

    def hash_password(self, password: str) -> str:
        return pwd_context.hash(password)

    def verify_password(self, password: str, password_hash: str) -> bool:
        return pwd_context.verify(password, password_hash)

    def create_session_token(self, user: User) -> str:
        payload = {
            "user_id": user.id,
            "username": user.username,
            "role": user.role.value,
        }
        return self._serializer.dumps(payload)

    def verify_session_token(self, token: str) -> dict | None:
        try:
            return self._serializer.loads(token, max_age=86400 * 7)  # 7 days
        except (BadSignature, SignatureExpired):
            return None
