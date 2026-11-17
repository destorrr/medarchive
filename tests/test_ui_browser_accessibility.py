"""Real-browser responsive and keyboard smoke checks for the shared UI shell."""
import os
import socket
import subprocess
import sys

import pytest
import time
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

from medarchive.services.auth_service import AuthService


@pytest.fixture
async def browser_server(test_config, db):
    """Start the real ASGI app against the isolated SQLite test database."""
    auth = AuthService(secret_key=test_config.effective_secret_key)
    await db.execute(
        "INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'Тестовый филиал', 'B1')"
    )
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, full_name, password_hash, role, branch_id) "
        "VALUES (1, 'admin', 'Администратор Тест', ?, 'admin', NULL)",
        (auth.hash_password("admin123"),),
    )
    await db.conn.commit()

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    env = os.environ.copy()
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
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Uvicorn завершился до готовности браузерного теста")
            try:
                with urlopen(f"{base_url}/login", timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError):
                time.sleep(0.2)
        else:
            raise RuntimeError("Приложение не запустилось за 20 секунд")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def chromium():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def _assert_no_document_overflow(page):
    dimensions = page.evaluate(
        "() => ({viewport: window.innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth})"
    )
    assert dimensions["document"] <= dimensions["viewport"], dimensions
    assert dimensions["body"] <= dimensions["viewport"], dimensions


def test_login_and_dashboard_fit_common_viewports(browser_server, chromium):
    page = chromium.new_page()
    for width in (320, 360, 390, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{browser_server}/login")
        assert page.locator("main.login-layout").is_visible()
        _assert_no_document_overflow(page)

    page.locator("#username").fill("admin")
    page.locator("#password").fill("admin123")
    page.get_by_role("button", name="Войти").click()
    page.wait_for_url("**/dashboard")
    assert page.locator("#main-content").is_visible()

    for width in (320, 360, 390, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.reload()
        assert page.locator("#main-content").is_visible()
        _assert_no_document_overflow(page)

    page.close()


def test_login_labels_and_keyboard_focus_are_visible(browser_server, chromium):
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.goto(f"{browser_server}/login")

    labels_are_associated = page.evaluate(
        "() => Array.from(document.querySelectorAll('label[for]')).every(label => "
        "document.getElementById(label.htmlFor) !== null)"
    )
    assert labels_are_associated

    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    assert page.locator("#username").evaluate("(el) => el === document.activeElement")
    focus = page.locator("#username").evaluate(
        "(el) => ({style: getComputedStyle(el).outlineStyle, width: getComputedStyle(el).outlineWidth})"
    )
    assert focus == {"style": "solid", "width": "3px"}

    page.close()


def test_dashboard_skip_link_is_reachable_by_keyboard(browser_server, chromium):
    page = chromium.new_page(viewport={"width": 1280, "height": 900})
    page.goto(f"{browser_server}/login")
    page.locator("#username").fill("admin")
    page.locator("#password").fill("admin123")
    page.get_by_role("button", name="Войти").click()
    page.wait_for_url("**/dashboard")

    page.keyboard.press("Tab")
    skip_link = page.locator("a.skip-link")
    assert skip_link.evaluate("(el) => el === document.activeElement")
    assert skip_link.evaluate("(el) => el.getBoundingClientRect().top >= 0")
    assert skip_link.get_attribute("href") == "#main-content"
    page.close()
