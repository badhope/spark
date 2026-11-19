"""Accessibility smoke checks for the real Web UI.

It combines a fast critical smoke pass (unnamed controls, unlabeled inputs,
missing alt text, duplicate ids, positive tabindex, missing page lang) with a
real axe-core WCAG A/AA audit in light and dark themes. The smoke pass runs on
six screens; the axe pass fails on serious or critical violations.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._ui_browser import SKIP_REASON, browser_available, launch
from tests.test_ui_browser import _live_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.skipif(not browser_available(), reason=SKIP_REASON)

_SCAN = r"""
() => {
  const issues = [];
  const text = (el) => (el.getAttribute('aria-label') || '').trim();
  const labelFor = (el) => {
    const id = el.getAttribute('id');
    if (!id) return '';
    const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
    return label ? label.textContent.trim() : '';
  };
  const accessibleName = (el) => {
    const aria = text(el);
    if (aria) return aria;
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const parts = labelledBy.split(/\s+/).map((id) => document.getElementById(id));
      const joined = parts.map((p) => p ? p.textContent.trim() : '').join(' ').trim();
      if (joined) return joined;
    }
    const own = (el.textContent || '').trim();
    if (own) return own;
    return (el.getAttribute('title') || '').trim();
  };
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('button, [role="button"], a[href]').forEach((el) => {
    if (isVisible(el) && !accessibleName(el)) {
      issues.push(`unnamed ${el.tagName.toLowerCase()}.${el.className || ''}`.slice(0, 120));
    }
  });
  document.querySelectorAll('input, textarea, select').forEach((el) => {
    if (!isVisible(el)) return;
    const wrapped = (el.closest('label')?.textContent || '').trim();
    const name = text(el) || labelFor(el) || wrapped || (el.getAttribute('placeholder') || '').trim() || (el.getAttribute('title') || '').trim();
    if (!name) issues.push(`unlabeled ${el.tagName.toLowerCase()}`);
  });
  document.querySelectorAll('img').forEach((el) => {
    if (isVisible(el) && !el.hasAttribute('alt')) issues.push(`img without alt: ${el.getAttribute('src') || ''}`.slice(0, 120));
  });
  const seen = new Map();
  document.querySelectorAll('[id]').forEach((el) => {
    const id = el.id;
    if (seen.has(id)) issues.push(`duplicate id: ${id}`);
    else seen.set(id, true);
  });
  document.querySelectorAll('[tabindex]').forEach((el) => {
    const value = Number(el.getAttribute('tabindex'));
    if (Number.isFinite(value) && value > 0) issues.push(`positive tabindex: ${value}`);
  });
  if (!document.documentElement.getAttribute('lang')) issues.push('html missing lang');
  return issues;
}
"""

_CASES = [
    ("app", "/?onboarding=skip"),
    ("settings-model", "/?panel=settings&tab=model&onboarding=skip"),
    ("settings-queue", "/?panel=settings&tab=queue&onboarding=skip"),
    ("settings-remote", "/?panel=settings&tab=remote&onboarding=skip"),
    ("settings-security", "/?panel=settings&tab=security&onboarding=skip"),
    ("workbench-files", "/?panel=files&onboarding=skip"),
]


@pytest.mark.parametrize("name,path", _CASES)
def test_ui_a11y_critical_smoke(tmp_path: Path, name: str, path: str) -> None:
    with _live_server(tmp_path) as base:
        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            try:
                page.goto(base + path, wait_until="domcontentloaded")
                page.wait_for_timeout(700)
                issues = page.evaluate(_SCAN)
                assert issues == [], f"{name}: {issues}"
            finally:
                page.close()
                browser.close()


_LAYOUT_CASES = [
    ("app", "/?onboarding=skip"),
    ("settings-model", "/?panel=settings&tab=model&onboarding=skip"),
    ("settings-display", "/?panel=settings&tab=display&onboarding=skip"),
    ("settings-queue", "/?panel=settings&tab=queue&onboarding=skip"),
    ("settings-remote", "/?panel=settings&tab=remote&onboarding=skip"),
    ("settings-security", "/?panel=settings&tab=security&onboarding=skip"),
    ("workbench-files", "/?panel=files&onboarding=skip"),
    ("palette", "/?panel=palette&onboarding=skip"),
]


def test_ui_layout_has_no_horizontal_overflow(tmp_path: Path) -> None:
    """Desktop and phone widths must not produce a page-level horizontal scroll."""
    with _live_server(tmp_path) as base:
        with sync_playwright() as p:
            browser = launch(p)
            try:
                for width, height in ((1440, 900), (390, 844)):
                    page = browser.new_page(viewport={"width": width, "height": height})
                    try:
                        for name, path in _LAYOUT_CASES:
                            page.goto(base + path, wait_until="domcontentloaded")
                            page.wait_for_timeout(350)
                            overflow = page.evaluate(
                                "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                            )
                            assert not overflow, f"{name} overflows at {width}x{height}"
                    finally:
                        page.close()
            finally:
                browser.close()


_AXE_CASES = [
    ("app", "/?onboarding=skip"),
    ("settings-model", "/?panel=settings&tab=model&onboarding=skip"),
    ("settings-queue", "/?panel=settings&tab=queue&onboarding=skip"),
    ("settings-remote", "/?panel=settings&tab=remote&onboarding=skip"),
    ("settings-security", "/?panel=settings&tab=security&onboarding=skip"),
    ("workbench-files", "/?panel=files&onboarding=skip"),
]


def _axe_path() -> Path:
    return Path(__file__).resolve().parents[1] / "web" / "node_modules" / "axe-core" / "axe.min.js"


def test_axe_wcag_aa_has_no_serious_or_critical(tmp_path: Path) -> None:
    """Real axe-core audit: WCAG A/AA serious+critical must be zero in both themes."""
    axe = _axe_path()
    if not axe.is_file():
        pytest.skip("axe-core is not installed (run npm install in web/)")
    with _live_server(tmp_path) as base:
        with sync_playwright() as p:
            browser = launch(p)
            try:
                for theme in ("light", "dark"):
                    context = browser.new_context(
                        viewport={"width": 1440, "height": 900}, bypass_csp=True
                    )
                    context.add_init_script(
                        f"localStorage.setItem('spark-theme','{theme}')"
                    )
                    page = context.new_page()
                    try:
                        for name, path in _AXE_CASES:
                            page.goto(base + path, wait_until="domcontentloaded")
                            # The theme bootstrap applies the stored palette in
                            # the HTML shell, then the app adds a 220ms color
                            # transition. Auditing at 500ms can catch an
                            # in-flight blend (axe reports a lower contrast than
                            # the settled colors); wait for the transition to
                            # finish so the check reflects the real UI.
                            page.wait_for_timeout(1000)
                            page.add_script_tag(path=str(axe))
                            violations = page.evaluate(
                                """async () => {
                                  const result = await axe.run(document, {
                                    runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa']}
                                  });
                                  return result.violations.map((v) => ({
                                    id: v.id, impact: v.impact, nodes: v.nodes.length,
                                    targets: v.nodes.slice(0, 3).map((n) => n.target)
                                  }));
                                }"""
                            )
                            blocking = [
                                v
                                for v in violations
                                if v.get("impact") in {"critical", "serious"}
                            ]
                            assert not blocking, f"{theme}/{name}: {blocking}"
                    finally:
                        page.close()
                        context.close()
            finally:
                browser.close()
