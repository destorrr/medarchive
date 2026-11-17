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
        ("browser-viewer", "viewer", None),
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
    for width in (320, 390, 768, 1024, 1440):
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
    unlabeled_controls = page.evaluate("""() => Array.from(document.querySelectorAll(
        'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]), select, textarea'
    )).filter((el) => {
        const hasLabel = el.labels && el.labels.length > 0;
        const hasAriaName = Boolean(el.getAttribute('aria-label') || el.getAttribute('aria-labelledby'));
        return !hasLabel && !hasAriaName;
    }).map((el) => ({tag: el.tagName, type: el.type || '', name: el.name || '', id: el.id || ''}))""")
    assert not unlabeled_controls, (path, "form controls without accessible names", unlabeled_controls)
    # Start keyboard navigation from the document, not from an autofocus
    # search field. Otherwise Tab advances from a page-specific initial focus
    # and makes this shared accessibility check depend on the previous route.
    page.evaluate(
        "() => { const active = document.activeElement; "
        "if (active && active !== document.body) active.blur(); }"
    )
    page.keyboard.press("Tab")
    keyboard_focus_is_visible = page.evaluate(
        "() => { const el = document.activeElement; if (!el) return false; "
        "const style = getComputedStyle(el); "
        "return el.matches('a,button,input,select,textarea,[tabindex]') && "
        "el.matches(':focus-visible') && style.outlineStyle !== 'none' && "
        "parseFloat(style.outlineWidth) >= 2; }"
    )
    assert keyboard_focus_is_visible, (path, "first keyboard focus target has no visible focus indicator")


def _assert_mobile_tables_are_scrollable_and_keyboard_reachable(page, path):
    page.set_viewport_size({"width": 320, "height": 900})
    tables = page.locator("table")
    for index in range(tables.count()):
        table = tables.nth(index)
        wrapper = table.locator("xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' table-wrap ')][1]")
        assert wrapper.count() == 1, (path, "table has no dedicated scroll region")
        assert wrapper.get_attribute("role") == "region", path
        assert wrapper.get_attribute("tabindex") == "0", path
        overflow = wrapper.evaluate("(el) => getComputedStyle(el).overflowX")
        assert overflow in ("auto", "scroll"), (path, overflow)

        dimensions = wrapper.evaluate("(el) => ({client: el.clientWidth, scroll: el.scrollWidth})")
        if dimensions["scroll"] <= dimensions["client"]:
            continue

        wrapper.evaluate("(el) => { el.scrollLeft = 0; el.focus(); }")
        assert wrapper.evaluate("(el) => document.activeElement === el"), path
        page.keyboard.press("End")
        assert wrapper.evaluate("(el) => el.scrollLeft > 0"), (path, "End did not scroll table to the right")
        page.keyboard.press("Home")
        assert wrapper.evaluate("(el) => el.scrollLeft === 0"), (path, "Home did not return table to the left")
        page.keyboard.press("ArrowRight")
        assert wrapper.evaluate("(el) => el.scrollLeft > 0"), (path, "ArrowRight did not scroll table")
        page.keyboard.press("ArrowLeft")
        assert wrapper.evaluate("(el) => el.scrollLeft === 0"), (path, "ArrowLeft did not scroll table back")

        # Arrow keys inside an editor must retain their native caret/navigation behavior.
        wrapper.evaluate("""(el) => {
          const input = document.createElement("input");
          input.type = "text";
          input.value = "keyboard";
          input.setAttribute("aria-label", "Temporary keyboard interaction check");
          input.style.position = "absolute";
          el.appendChild(input);
          input.focus();
          el.scrollLeft = 0;
        }""")
        page.keyboard.press("ArrowRight")
        assert wrapper.evaluate("(el) => el.scrollLeft === 0"), (path, "form control key was intercepted")
        wrapper.locator("input[aria-label='Temporary keyboard interaction check']").evaluate("(el) => el.remove()")


def _assert_admin_filters_and_forms(page, path):
    expected = {
        "/admin/users": ("#new-username", "#new-full-name", "#new-password", "#new-role"),
        "/admin/branches": ("#branch-code", "#branch-name"),
        "/admin/couriers": ("#courier-full-name", "#courier-phone"),
        "/admin/sections": ("#shelf-section-name", "#shelf-name"),
        "/admin/audit": ("#event_type", "#aggregate_type", "#actor_id", "#date_from", "#date_to"),
    }
    if path not in expected:
        return
    for selector in expected[path]:
        assert page.locator(selector).count() == 1, (path, selector)
        assert page.locator(selector).is_visible(), (path, selector)
    form = page.locator("form").filter(has=page.locator(expected[path][0]))
    assert form.count() == 1, (path, "expected form is missing")
    assert form.locator("button[type=submit]").count() >= 1, path


def _assert_status_contrast(page, path):
    classes = [
        "badge-in_branch", "badge-in_box", "badge-ready",
        "badge-in_transit", "badge-in_progress",
        "badge-in_archive", "badge-placed", "badge-completed",
        "badge-pending_return", "badge-lost", "badge-new", "badge-received",
        "badge-cancelled", "badge-not_found", "badge-packing", "badge-neutral",
        "badge-success", "badge-warning", "badge-info",
        "status-pill-success", "status-pill-muted", "status-pill-warning",
    ]
    results = page.evaluate("""(classes) => {
      const luminance = (value) => {
        const channels = value.match(/[\\d.]+/g).slice(0, 3).map(Number).map((v) => {
          v /= 255;
          return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
      };
      return classes.map((name) => {
        const node = document.createElement("span");
        node.className = name;
        node.textContent = "Статус";
        node.style.cssText = "position:fixed;left:-10000px;top:0";
        document.body.appendChild(node);
        const style = getComputedStyle(node);
        const foreground = style.color;
        const background = style.backgroundColor;
        const values = [luminance(foreground), luminance(background)].sort((a,b) => b-a);
        const ratio = (values[0] + 0.05) / (values[1] + 0.05);
        node.remove();
        return {name, foreground, background, ratio};
      });
    }""", classes)
    failures = [item for item in results if item["ratio"] < 4.5]
    assert not failures, (path, failures)


def test_postgresql_role_workspaces_render_responsively_in_chromium(
    postgres_browser_server, postgres_chromium
):
    workspaces = {
        "browser-branch": (
            "/dashboard", "/search", "/history", "/shipments",
            "/packing", "/transfer", "/branch/receive",
        ),
        "browser-archive": (
            "/dashboard", "/search", "/history", "/shipments",
            "/receive", "/placement", "/requests",
        ),
        "browser-central": (
            "/dashboard", "/search", "/history", "/shipments",
            "/central/packing", "/central/transfer",
            "/requests/new", "/central/custody",
        ),
        "browser-admin": (
            "/dashboard", "/search", "/history", "/admin", "/admin/users", "/admin/branches", "/admin/couriers",
            "/admin/sections", "/admin/incidents", "/admin/audit",
        ),
        "browser-viewer": ("/dashboard", "/search", "/history"),
    }
    for username, paths in workspaces.items():
        context = postgres_chromium.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        _login(page, postgres_browser_server, username)
        for path in paths:
            _assert_page_responsive_and_labeled(page, postgres_browser_server, path)
            if path.startswith("/admin/"):
                _assert_mobile_tables_are_scrollable_and_keyboard_reachable(page, path)
                _assert_admin_filters_and_forms(page, path)
            _assert_status_contrast(page, path)
        assert not page_errors, page_errors
        context.close()
