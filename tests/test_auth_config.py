import pytest

from medarchive.config import Config


def test_sqlite_remains_the_default_authentication_backend():
    config = Config(secret_key="test-secret")
    assert config.auth_backend == "sqlite"
    assert config.cookie_secure is False


def test_postgresql_auth_requires_postgresql_adapter():
    with pytest.raises(ValueError, match="MEDARCHIVE_POSTGRES_ENABLED"):
        Config(auth_backend="postgresql", secret_key="test-secret")


def test_postgresql_auth_can_be_selected_explicitly():
    config = Config(
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="test-secret-key-with-at-least-32-characters",
        cookie_secure=True,
        environment="production",
    )
    assert config.auth_backend == "postgresql"
    assert config.cookie_secure is True


def test_unknown_authentication_backend_is_rejected():
    with pytest.raises(ValueError, match="MEDARCHIVE_AUTH_BACKEND"):
        Config(auth_backend="mysql", secret_key="test-secret")


def test_postgresql_auth_rejects_insecure_session_cookie():
    with pytest.raises(ValueError, match="MEDARCHIVE_COOKIE_SECURE"):
        Config(
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="test-secret-key-with-at-least-32-characters",
            environment="production",
        )


def test_postgresql_auth_rejects_short_session_secret():
    with pytest.raises(ValueError, match="MEDARCHIVE_SECRET_KEY"):
        Config(
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="short",
            cookie_secure=True,
            environment="production",
        )

def test_postgresql_auth_allows_non_production_test_configuration():
    config = Config(
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="integration-test-secret",
        environment="test",
    )
    assert config.cookie_secure is False


def test_unknown_environment_is_rejected():
    with pytest.raises(ValueError, match="MEDARCHIVE_ENVIRONMENT"):
        Config(environment="staging")
