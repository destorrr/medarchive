"""Real Chromium checks for PostgreSQL operational workspaces.

The browser runs against the disposable PostgreSQL service used by CI. Each
role receives a real signed session cookie and only its own pages are opened.
"""
import os
import socket
import subprocess
import sys
import time
import uuid
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright
from sqlalchemy import delete

from medarchive.adapters.postgresql.database import Database as PostgreSQLDatabase
from medarchive.adapters.postgresql.models import (
    ArchiveShelf, Branch, Courier, Location, User,
)
from medarchive.config import Config
from medarchive.services.auth_service import AuthService


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")
SECRET = "ui-operational-browser-test-secret-key"


@pytest.mark.asyncio
async def test_postgresql_operational_pages_are_responsive_and_keyboard_accessible():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    config = Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        environment="test",
        secret_key=SECRET,
    )
    database = PostgreSQLDatabase(config.effective_database_url)
    suffix = uuid.uuid4().hex[:12]
    branch = Branch(
        id=uuid.uuid4(), code=f"UI-{suffix}", name=f"UI Browser Branch {suffix}",
        is_active=True,
    )
    branch_location = Location(
        id=uuid.uuid4(), kind="branch", branch_id=branch.id,
        name=f"UI Browser Branch Location {suffix}", is_active=True,
    )
    shelf = ArchiveShelf(
        id=uuid.uuid4(), section_name=f"UI-{suffix}", shelf_name="Shelf 01",
        qr_code=f"UI-SHELF-{suffix}", is_active=True,
    )
    courier = Courier(
        id=uuid.uuid4(), full_name=f"UI Browser Courier {suffix}", is_active=True,
    )
    actors = [
        User(id=uuid.uuid4(), username=f"ui-branch-{suffix}", password_hash="test",
             full_name="UI Branch Operator", role="branch", branch_id=branch.id,
             is_active=True),
        User(id=uuid.uuid4(), username=f"ui-archive-{suffix}", password_hash="test",
             full_name="UI Archive Operator", role="archive", is_active=True),
        User(id=uuid.uuid4(), username=f"ui-central-{suffix}", password_hash="test",
             full_name="UI Central Operator", role="central_office", is_active=True),
    ]
    process = None
    try:
        async with database.session_factory() as session:
            session.add_all([
                branch, branch_location, shelf, courier, *actors,
            ])
            await session.commit()

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        env.update({
            "MEDARCHIVE_DATABASE_URL": DATABASE_URL,
            "MEDARCHIVE_POSTGRES_ENABLED": "true",
            "MEDARCHIVE_AUTH_BACKEND": "postgresql",
            "MEDARCHIVE_ENVIRONMENT": "test",
            "MEDARCHIVE_COOKIE_SECURE": "false",
            "MEDARCHIVE_SECRET_KEY": SECRET,
        })
        process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "medarchive.main:create_app",
                "--factory", "--host", "127.0.0.1", "--port", str(port),
                "--log-level", "warning",
            ],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Uvicorn завершился до готовности PostgreSQL browser test")
            try:
                with urlopen(f"{base_url}/login", timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError):
                time.sleep(0.25)
        else:
            raise RuntimeError("PostgreSQL-приложение не запустилось за 30 секунд")

        auth = AuthService(secret_key=SECRET)
        role_pages = {
            actors[0]: ("/packing", "/transfer"),
            actors[1]: ("/receive", "/placement", "/requests"),
            actors[2]: (
                "/central/receive", "/central/packing",
                "/central/transfer", "/central/custody",
            ),
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for actor, paths in role_pages.items():
                    context = browser.new_context(viewport={"width": 390, "height": 900})
                    context.add_cookies([{
                        "name": "session",
                        "value": auth.create_session_token(actor),
                        "url": base_url,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }])
                    page = context.new_page()
                    page_errors = []
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    for path in paths:
                        response = page.goto(f"{base_url}{path}", wait_until="networkidle")
                        assert response is not None and response.status == 200, path
                        assert page.locator("#main-content").is_visible(), path
                        assert page.locator("h1").count() == 1, path
                        labels_ok = page.evaluate(
                            "() => Array.from(document.querySelectorAll('label[for]')).every(label => "
                            "document.getElementById(label.htmlFor) !== null)"
                        )
                        assert labels_ok, f"Нарушена связь label/control: {path}"
                        for width in (320, 390, 768, 1024, 1440):
                            page.set_viewport_size({"width": width, "height": 900})
                            page.wait_for_timeout(40)
                            dimensions = page.evaluate(
                                "() => ({viewport: innerWidth, document: document.documentElement.scrollWidth, "
                                "body: document.body.scrollWidth})"
                            )
                            assert dimensions["document"] <= dimensions["viewport"], (path, width, dimensions)
                            assert dimensions["body"] <= dimensions["viewport"], (path, width, dimensions)
                        page.set_viewport_size({"width": 390, "height": 900})
                        page.keyboard.press("Tab")
                        focused = page.evaluate(
                            "() => document.activeElement && (document.activeElement.matches('a,button,input,select,textarea') || "
                            "document.activeElement.getAttribute('role') === 'button')"
                        )
                        assert focused, f"Клавиатурный фокус не переходит на элемент: {path}"
                    assert not page_errors, page_errors
                    context.close()
            finally:
                browser.close()
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        async with database.session_factory() as session:
            for actor in actors:
                await session.execute(delete(User).where(User.id == actor.id))
            await session.execute(delete(Courier).where(Courier.id == courier.id))
            await session.execute(delete(ArchiveShelf).where(ArchiveShelf.id == shelf.id))
            await session.execute(delete(Location).where(
                Location.id == branch_location.id
            ))
            await session.execute(delete(Branch).where(Branch.id == branch.id))
            await session.commit()
        await database.close()
