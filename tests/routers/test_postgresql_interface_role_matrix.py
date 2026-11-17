"""Cross-role smoke matrix for every PostgreSQL HTML interface."""

import os
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import Branch, Box, Location, User
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app
from medarchive.services.postgresql_bootstrap import ensure_system_locations


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_postgresql_html_pages_and_navigation_follow_role_matrix():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="postgres-interface-role-matrix-secret",
    ))
    database = await get_pg_database()
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    async def session_override():
        yield session

    app.dependency_overrides[get_pg_session] = session_override

    suffix = uuid.uuid4().hex
    branch = Branch(
        id=uuid.uuid4(), code=f"UI-{suffix[:10]}", name="UI Matrix Branch",
        is_active=True,
    )
    users = {}
    locations = {}
    boxes = {}
    try:
        async with session.begin():
            await ensure_system_locations(session)
            await session.flush()
            locations["central_office"] = await session.scalar(
                select(Location).where(
                    Location.kind == "central_office", Location.is_active.is_(True)
                )
            )
            locations["archive"] = await session.scalar(
                select(Location).where(
                    Location.kind == "archive", Location.is_active.is_(True)
                )
            )
            assert locations["central_office"] is not None
            assert locations["archive"] is not None

            session.add(branch)
            await session.flush()
            locations["branch"] = Location(
                id=uuid.uuid4(), kind="branch", branch_id=branch.id,
                name=branch.name, is_active=True,
            )
            session.add(locations["branch"])
            await session.flush()

            for role in ("branch", "central_office", "archive", "admin", "viewer"):
                user = User(
                    id=uuid.uuid4(),
                    username=f"ui-matrix-{role}-{suffix}",
                    password_hash="test-only-hash",
                    full_name=f"UI Matrix {role}",
                    role=role,
                    branch_id=branch.id if role == "branch" else None,
                    is_active=True,
                )
                session.add(user)
                users[role] = user
            await session.flush()

            for role in ("branch", "central_office", "archive"):
                box = Box(
                    id=uuid.uuid4(),
                    box_number=f"UI-{role.upper()}-{suffix[:12]}",
                    current_location_id=locations[role].id,
                    current_shipment_id=None,
                    status="active",
                    created_by=users[role].id,
                )
                session.add(box)
                boxes[role] = box
            await session.flush()

        tokens = {
            role: get_auth().create_session_token(user)
            for role, user in users.items()
        }
        allowed_pages = {
            "branch": {
                "/dashboard", "/search", "/search/results", "/history", "/shipments",
                "/packing", "/transfer", "/branch/receive",
            },
            "central_office": {
                "/dashboard", "/search", "/search/results", "/history", "/shipments",
                "/requests/new", "/central/receive", "/central/packing",
                "/central/transfer", "/central/custody",
            },
            "archive": {
                "/dashboard", "/search", "/search/results", "/history", "/shipments",
                "/receive", "/placement", "/requests",
            },
            "admin": {
                "/dashboard", "/search", "/search/results", "/history",
                "/admin", "/admin/branches", "/admin/users", "/admin/couriers",
                "/admin/sections", "/admin/incidents", "/admin/audit",
            },
            "viewer": {
                "/dashboard", "/search", "/search/results", "/history",
            },
        }
        all_pages = set().union(*allowed_pages.values())
        role_label = {
            "branch": "Филиал", "central_office": "Центральный офис",
            "archive": "Архив", "admin": "Администратор", "viewer": "Просмотр",
        }
        expected_nav = {
            "branch": (
                "/dashboard", "/search", "/history", "/shipments",
                "/branch/receive", "/packing", "/transfer",
            ),
            "central_office": (
                "/dashboard", "/search", "/history", "/shipments",
                "/requests/new", "/central/receive", "/central/packing",
                "/central/transfer", "/central/custody",
            ),
            "archive": (
                "/dashboard", "/search", "/history", "/shipments",
                "/receive", "/placement", "/requests",
            ),
            "admin": ("/dashboard", "/search", "/history", "/admin"),
            "viewer": ("/dashboard", "/search", "/history"),
        }
        admin_navigation = (
            "/admin", "/admin/branches", "/admin/users", "/admin/couriers",
            "/admin/sections", "/admin/incidents", "/admin/audit",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for role, token in tokens.items():
                client.cookies.set("session", token)
                for path in sorted(all_pages):
                    response = await client.get(path)
                    expected = 200 if path in allowed_pages[role] else 403
                    assert response.status_code == expected, (
                        role, path, response.status_code, response.text[:300]
                    )
                    if expected == 200 and path != "/search/results":
                        navigation = (
                            admin_navigation if role == "admin" and path.startswith("/admin")
                            else expected_nav[role]
                        )
                        for href in navigation:
                            assert f'href="{href}"' in response.text, (
                                "page navigation", role, path, href
                            )

                dashboard = await client.get("/dashboard")
                assert dashboard.status_code == 200
                assert f">{role_label[role]}<" in dashboard.text
                for href in expected_nav[role]:
                    assert f'href="{href}"' in dashboard.text, (role, href)

            # A box label is visible only to its current operational location
            # or to an administrator; viewer access and cross-location access
            # are explicitly denied.
            for role, token in tokens.items():
                client.cookies.set("session", token)
                for owner_role, box in boxes.items():
                    response = await client.get(f"/boxes/{box.id}/label")
                    expected = 200 if role == "admin" or role == owner_role else 403
                    assert response.status_code == expected, (
                        role, owner_role, response.status_code
                    )
                    if expected == 200:
                        visible_navigation = expected_nav[role]
                        for href in visible_navigation:
                            assert f'href="{href}"' in response.text, (
                                "QR label navigation", role, owner_role, href
                            )

    finally:
        await session.close()
        await outer_transaction.rollback()
        await connection.close()
        await database.close()
