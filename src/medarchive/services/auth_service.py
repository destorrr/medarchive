"""Сервис аутентификации: хеширование паролей и сессионные токены."""
import hashlib

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

    def create_session_token(self, user) -> str:
        # SQLite IDs remain integers; target PostgreSQL UUIDs are serialized as strings.
        user_id = user.id
        if not isinstance(user_id, (str, int)):
            user_id = str(user_id)
        payload = {
            "user_id": user_id,
            "username": user.username,
            "role": user.role.value if hasattr(user.role, "value") else user.role,
        }
        password_hash = getattr(user, "password_hash", None)
        if password_hash:
            # A one-way credential fingerprint revokes previously issued sessions
            # after a password reset without exposing the password hash in the cookie.
            payload["credential_version"] = hashlib.sha256(password_hash.encode()).hexdigest()
        return self._serializer.dumps(payload)

    def verify_session_token(self, token: str) -> dict | None:
        try:
            return self._serializer.loads(token, max_age=60 * 60 * 12)  # 12 hours
        except (BadSignature, SignatureExpired):
            return None
