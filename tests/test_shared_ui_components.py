"""Shared presentation primitives: isolated Jinja rendering and browser checks."""
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "src" / "medarchive"


@pytest.fixture
def component_env():
    return Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(["html"]),
    )


def render_panel(env, message="Готово", tone="success"):
    return env.from_string(
        '{% from "components/presentation.html" import panel, feedback %}'
        '{% call panel("Проверка", "probe-title") %}'
        '{{ feedback(message, tone) }}{% endcall %}'
    ).render(message=message, tone=tone)


@pytest.mark.parametrize("tone,role", [
    ("success", "status"), ("warning", "status"), ("error", "alert"),
    ("neutral", "status"), ("unsupported", "status"),
])
def test_feedback_semantics_and_escaping(component_env, tone, role):
    html = render_panel(component_env, "<script>alert(1)</script>", tone)
    assert f'role="{role}"' in html
    expected = "neutral" if tone == "unsupported" else tone
    assert f'data-tone="{expected}"' in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'aria-labelledby="probe-title"' in html
    assert 'id="probe-title"' in html


@pytest.mark.parametrize("workspace,width,sidebar", [
    ("branch", "1200px", "215px"),
    ("central_office", "1320px", "240px"),
    ("archive", "1250px", "205px"),
    ("admin", "1320px", "240px"),
    ("analytics", "1320px", "240px"),
])
def test_shared_tokens_and_panel_in_chromium(component_env, workspace, width, sidebar):
    css = (ROOT / "static" / "css" / "design-system.css").read_text(encoding="utf-8")
    html = render_panel(component_env, "ДлинноеСообщение" * 30)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 320, "height": 800})
            page.set_content(
                '<!doctype html><style>* {box-sizing:border-box} body {margin:0}'
                + css + '</style><main class="ma-design" data-workspace="'
                + workspace + '">' + html + '</main>'
            )
            main = page.locator("main")
            assert main.evaluate(
                "(el) => getComputedStyle(el).getPropertyValue('--ma-workspace-width').trim()"
            ) == width
            assert main.evaluate(
                "(el) => getComputedStyle(el).getPropertyValue('--ma-sidebar-width').trim()"
            ) == sidebar
            assert page.get_by_role("region", name="Проверка").is_visible()
            assert page.locator(".ma-panel").evaluate(
                "(el) => getComputedStyle(el).borderRadius"
            ) == "13px"
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        finally:
            browser.close()
