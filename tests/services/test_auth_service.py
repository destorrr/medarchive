# tests/services/test_auth_service.py
import pytest
from medarchive.services.auth_service import AuthService
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole


@pytest.fixture
def auth_service():
    return AuthService(secret_key="test-secret")


class TestAuthService:
    def test_hash_and_verify_password(self, auth_service):
        hashed = auth_service.hash_password("mypassword123")
        assert hashed != "mypassword123"
        assert auth_service.verify_password("mypassword123", hashed)
        assert not auth_service.verify_password("wrongpassword", hashed)

    def test_create_session_token(self, auth_service):
        user = User(id=1, username="operator", password_hash="...", role=UserRole.branch)
        token = auth_service.create_session_token(user)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_verify_session_token(self, auth_service):
        user = User(id=1, username="operator", password_hash="...", role=UserRole.branch)
        token = auth_service.create_session_token(user)
        payload = auth_service.verify_session_token(token)
        assert payload is not None
        assert payload["user_id"] == 1
        assert payload["username"] == "operator"
        assert payload["role"] == "branch"

    def test_verify_invalid_token(self, auth_service):
        assert auth_service.verify_session_token("garbage-token") is None

    def test_verify_tampered_token(self, auth_service):
        user = User(id=1, username="operator", password_hash="...", role=UserRole.branch)
        token = auth_service.create_session_token(user)
        # Tamper: add a character
        assert auth_service.verify_session_token(token + "x") is None
