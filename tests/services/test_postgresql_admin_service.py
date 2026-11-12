"""Tests for PostgreSQL administration validation and route registration."""
import pytest
from pydantic import ValidationError

from medarchive.main import create_app
from medarchive.config import Config
from medarchive.routers.postgresql_admin import BranchInput, UserCreateInput
from medarchive.services.postgresql_admin_service import PostgreSQLAdminService


def test_branch_input_rejects_blank_values():
    with pytest.raises(ValidationError):
        BranchInput(code=" ", name="Филиал")


def test_user_input_requires_long_password():
    with pytest.raises(ValidationError):
        UserCreateInput(
            username="operator", full_name="Иванов Иван",
            password="short", role="branch",
        )


@pytest.mark.parametrize(
    ("role", "branch_id", "valid"),
    [
        ("branch", None, False),
        ("branch", "b3b6d9c0-9d2e-4a2b-8f87-8d20a5f94a33", True),
        ("admin", None, True),
        ("archive", None, True),
        ("central_office", None, True),
        ("viewer", None, True),
        ("unknown", None, False),
        ("admin", "b3b6d9c0-9d2e-4a2b-8f87-8d20a5f94a33", False),
    ],
)
def test_user_role_branch_binding_validation(role, branch_id, valid):
    import uuid

    parsed_branch_id = uuid.UUID(branch_id) if branch_id else None
    if valid:
        PostgreSQLAdminService._validate_user_fields(
            "login", "Фамилия Имя", role, parsed_branch_id
        )
    else:
        with pytest.raises(ValueError):
            PostgreSQLAdminService._validate_user_fields(
                "login", "Фамилия Имя", role, parsed_branch_id
            )


def test_postgresql_admin_routes_are_registered():
    app = create_app(Config(postgres_enabled=True, auth_backend="postgresql", secret_key="test-secret-key"))
    paths = set(app.openapi()["paths"])
    assert "/api/admin/branches" in paths
    assert "/api/admin/users" in paths
    assert "/admin" in paths
    assert "/admin/branches" in paths
    assert "/admin/users" in paths
    assert "/admin/couriers" in paths
    assert "/admin/sections" in paths


def test_user_update_preserves_active_status_when_omitted():
    from medarchive.routers.postgresql_admin import UserUpdateInput

    payload = UserUpdateInput(full_name="Иванов Иван", role="admin")
    assert payload.is_active is None
