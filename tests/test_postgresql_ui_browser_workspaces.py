"""Real Chromium checks for PostgreSQL operational workspaces.

The test uses the disposable PostgreSQL service configured by CI and logs in
through the same browser flow as an employee. It deliberately checks each role
against its own page set rather than rendering templates in isolation.
"""
import os
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import uuid

import pytest
from playwright.sync_api import sync_playwright
from sqlalchemy import delete

from medarchive.adapters.postgresql.models import Branch, Location, User
from medarchive.config import Config
from medarchive.dependencies import get_pg_database
from medarchive.main import create_app
from medarchive.services.auth_service import AuthService


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")
PASSWORD = "Browser-UI-2026"


@pytest.fixture
async def postgres_browser_server():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    secret = "postgres-browser-ui-test-secret-key"
    config = Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key=secret,
    )
    # Initialize the process-local database/auth dependencies before seeding.
    create_app(config)
    database = await get_pg_database()
    branch_id = uuid.uuid4()
    branch = Branch(
        id=branch_id,
        code=f"BR-{uuid.uuid4().hex[:12]}",
        name="Browser Test Branch",
        is_active=True,
    )
    branch_location = Location(
        id=uuid.uuid4(),
        kind="branch",
        branch_id=branch_id,
        name="Browser Test Branch",
        is_active=True,
    )
    auth = AuthService(secret_key=secret)
    credentials = [
        ("browser-admin", "admin", None),
        ("browser-branch", "branch", branch_id),
        ("browser-archive", "archive", None),
        ("browser-central", "central_office", None),
    ]
    users = [
        User(
            id=uuid.uuid4(),
            username=username,
            password_hash=auth.hash_password(PASSWORD),
            full_name=f"Browser {role}",
            role=role,
            branch_id=user_branch_id,
            is_active=True,
        )
        for username, role, user_branch_id in credentials
    ]
    async with database.session_factory() as session:
        session.add_all([branch, branch_location, *users])
        await session.commit()

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update({
        "MEDARCHIVE_DATABASE_URL": DATABASE_URL,
        "MEDARCHIVE_POSTGRES_ENABLED": "true",
        "MEDARCHIVE_AUTH_BACKEND": "postgresql",
        "MEDARCHIVE_SECRET_KEY": secret,
        "MEDARCHIVE_ENVIRONMENT": "test",
        "MEDARCHIVE_COOKIE_SECURE": "false",
    })
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "medarchive.main:create_app",
            "--factory", "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("PostgreSQL browser app exited before startup")
            try:
                with urlopen(f"{base_url}/login", timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError):
                time.sleep(0.2)
        else:
            raise RuntimeError("PostgreSQL browser app did not start in 30 seconds")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        async with database.session_factory() as session:
            await session.execute(delete(User).where(User.id.in_([u.id for u in users])))
            await session.execute(delete(Location).where(Location.id == branch_location.id))
            await session.execute(delete(Branch).where(Branch.id == branch_id))
            await session.commit()


@pytest.fixture
def postgres_chromium():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def _login(page, base_url, username):
    page.goto(f"{base_url}/login")
    page.locator("#username").fill(username)
    page.locator("#password").fill(PASSWORD)
    page.get_by_role("button", name="Войти").click()
    page.wait_for_url(lambda url: "/login" not in url)


def _assert_page_responsive_and_labeled(page, base_url, path):
    response = page.goto(f"{base_url}{path}")
    assert response is not None and response.status == 200, path
    assert page.locator("main").count() == 1, path
    for width in (390, 768, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(50)
        dimensions = page.evaluate(
            "() => ({viewport: innerWidth, document: document.documentElement.scrollWidth})"
        )
        assert dimensions["document"] <= dimensions["viewport"], (path, width, dimensions)
    labels_are_associated = page.evaluate(
        "() => Array.from(document.querySelectorAll('label[for]')).every(label => "
        "document.getElementById(label.htmlFor) !== null)"
    )
    assert labels_are_associated, path


def test_postgresql_role_workspaces_render_responsively_in_chromium(
    postgres_browser_server, postgres_chromium
):
    workspaces = {
        "browser-branch": (
            "/dashboard", "/packing", "/transfer", "/branch/receive",
        ),
        "browser-archive": (
            "/dashboard", "/receive", "/placement", "/requests",
        ),
        "browser-central": (
            "/dashboard", "/central/packing", "/central/transfer",
            "/central/requests", "/central/custody",
        ),
        "browser-admin": (
            "/admin", "/admin/users", "/admin/branches", "/admin/couriers",
            "/admin/sections", "/admin/incidents", "/admin/audit",
        ),
    }
    for username, paths in workspaces.items():
        context = postgres_chromium.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        _login(page, postgres_browser_server, username)
        for path in paths:
            _assert_page_responsive_and_labeled(page, postgres_browser_server, path)
        context.close()
