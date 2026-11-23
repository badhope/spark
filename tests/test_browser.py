from __future__ import annotations

from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from spark.config import SparkConfig  # noqa: E402
from spark.sandbox import WorkdirSandbox  # noqa: E402
from spark.tools import browser  # noqa: E402
from spark.tools.registry import ToolContext, ToolRegistry  # noqa: E402


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(sandbox=WorkdirSandbox(tmp_path), config=SparkConfig())


def test_browser_local_page_click_type_screenshot(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text(
        "<!doctype html><html><body>"
        "<button id='b' onclick=\"document.getElementById('out').textContent='clicked'\">Go</button>"
        "<input id='i'><div id='out'>start</div></body></html>"
    )
    ctx = _ctx(tmp_path)
    opened = browser.browser_open(
        ctx, browser.BrowserArgs(url=page.as_uri())
    )
    assert opened.ok, opened.payload
    clicked = browser.browser_click(ctx, browser.BrowserClickArgs(selector="#b"))
    assert clicked.ok, clicked.payload
    assert "clicked" in str(clicked.payload["text"])
    typed = browser.browser_type(
        ctx, browser.BrowserTypeArgs(selector="#i", text="hello")
    )
    assert typed.ok, typed.payload
    shot = browser.browser_screenshot(ctx, browser.BrowserScreenshotArgs(name="test"))
    assert shot.ok, shot.payload
    assert (tmp_path / shot.payload["path"]).is_file()
    viewport = browser.browser_viewport(
        ctx, browser.BrowserViewportArgs(width=800, height=600)
    )
    assert viewport.ok, viewport.payload


def test_browser_rejects_file_outside_workdir(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.html"
    outside.write_text("<html><body>no</body></html>")
    ctx = _ctx(tmp_path)
    result = browser.browser_open(
        ctx, browser.BrowserArgs(url=outside.as_uri())
    )
    assert not result.ok
    assert "工作目录" in str(result.payload.get("error", ""))


def test_browser_tools_registered(tmp_path: Path) -> None:
    names = {
        item["function"]["name"]
        for item in ToolRegistry(_ctx(tmp_path)).schemas()
    }
    assert {
        "browser_open",
        "browser_search",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_hover",
        "browser_select",
        "browser_scroll",
        "browser_history",
        "browser_tabs",
        "browser_wait",
        "browser_screenshot",
        "browser_viewport",
        "browser_eval",
        "browser_file_upload",
        "browser_download",
        "browser_pdf",
        "browser_console",
        "browser_network",
        "browser_dialog",
        "browser_trace_start",
        "browser_trace_stop",
        "browser_metrics",
    } <= names


def test_project_config_cannot_set_browser_rules(tmp_path: Path) -> None:
    from spark.config import load_config

    project = tmp_path / ".spark.toml"
    project.write_text(
        "[agent]\n"
        'browser_site_rules = ["*.bank.example=allow"]\n'
        'browser_profile = "stolen"\n'
        'browser_cdp_endpoint = "http://127.0.0.1:9222"\n',
        encoding="utf-8",
    )
    cfg = load_config(workdir=tmp_path)
    assert cfg.agent.browser_site_rules == []
    assert cfg.agent.browser_profile == ""
    assert cfg.agent.browser_cdp_endpoint == ""


def test_browser_doctor_reports_engine_health() -> None:
    cfg = SparkConfig()
    cfg.agent.browser_engine = "webkit"
    hint = browser.browser_install_hint(
        "webkit",
        "error while loading shared libraries: libgstcodecparsers-1.0.so.0",
    )
    assert "install-deps webkit" in hint
    assert "install webkit" in browser.browser_install_hint(
        "webkit", "Executable doesn't exist at /tmp/webkit"
    )


def test_navigation_target_defaults_to_bing(tmp_path: Path) -> None:
    assert browser.normalize_navigation_target("") == browser.DEFAULT_BROWSER_HOME
    searched = browser.normalize_navigation_target("Playwright 教程")
    assert searched.startswith(browser.DEFAULT_BROWSER_SEARCH)
    assert "Playwright+%E6%95%99%E7%A8%8B" in searched
    assert browser.normalize_navigation_target("github.com/badhope/spark") == (
        "https://github.com/badhope/spark"
    )
    assert browser.normalize_navigation_target("https://example.com/x") == (
        "https://example.com/x"
    )


def test_browser_snapshot_refs_and_advanced_actions(tmp_path: Path) -> None:
    page = tmp_path / "controls.html"
    page.write_text(
        "<!doctype html><html><body>"
        "<button id='b' onclick=\"document.getElementById('out').textContent='clicked'\">Go</button>"
        "<input id='i' placeholder='Name'>"
        "<select id='s'><option value='a'>Alpha</option><option value='b'>Beta</option></select>"
        "<div id='h' onmouseenter=\"document.getElementById('out').textContent='hovered'\">Hover</div>"
        "<div id='out'>start</div>"
        "<input id='f' type='file'>"
        "</body></html>"
    )
    ctx = _ctx(tmp_path)
    opened = browser.browser_open(ctx, browser.BrowserArgs(url=page.as_uri()))
    assert opened.ok, opened.payload
    controls = {row["id"]: row for row in opened.payload["interactive"] if row.get("id")}
    assert "b" in controls and "i" in controls and "s" in controls
    assert controls["b"]["ref"].startswith("r")
    assert "-e" in controls["b"]["ref"]

    clicked = browser.browser_click(
        ctx, browser.BrowserClickArgs(ref=controls["b"]["ref"])
    )
    assert clicked.ok, clicked.payload
    assert "clicked" in str(clicked.payload["text"])

    typed = browser.browser_type(
        ctx,
        browser.BrowserTypeArgs(ref=controls["i"]["ref"], text="hello"),
    )
    assert typed.ok, typed.payload
    assert any(row.get("id") == "i" and row.get("value") == "hello" for row in typed.payload["interactive"])

    selected = browser.browser_select(
        ctx, browser.BrowserSelectArgs(ref=controls["s"]["ref"], value="b")
    )
    assert selected.ok, selected.payload
    assert any(row.get("id") == "s" and row.get("value") == "b" for row in selected.payload["interactive"])

    hovered = browser.browser_hover(ctx, browser.BrowserHoverArgs(selector="#h"))
    assert hovered.ok, hovered.payload
    assert "hovered" in str(hovered.payload["text"])

    pressed = browser.browser_press(ctx, browser.BrowserPressArgs(key="Tab", repeat=2))
    assert pressed.ok, pressed.payload

    evaluated = browser.browser_eval(
        ctx, browser.BrowserEvalArgs(expression="document.querySelector('#s').value")
    )
    assert evaluated.ok, evaluated.payload
    assert "b" in str(evaluated.payload["result"])

    uploaded = browser.browser_file_upload(
        ctx, browser.BrowserFileUploadArgs(ref=controls["f"]["ref"], path=str(page))
    )
    assert uploaded.ok, uploaded.payload

    shot = browser.browser_screenshot(
        ctx, browser.BrowserScreenshotArgs(name="full", full_page=True)
    )
    assert shot.ok, shot.payload
    assert shot.payload["full_page"] is True

    elem_shot = browser.browser_screenshot(
        ctx, browser.BrowserScreenshotArgs(name="button", ref=controls["b"]["ref"])
    )
    assert elem_shot.ok, elem_shot.payload
    assert elem_shot.payload["element"] is True


def test_browser_tabs_and_history(tmp_path: Path) -> None:
    page1 = tmp_path / "one.html"
    page1.write_text("<html><body><h1>one</h1></body></html>")
    page2 = tmp_path / "two.html"
    page2.write_text("<html><body><h1>two</h1></body></html>")
    ctx = _ctx(tmp_path)
    opened = browser.browser_open(ctx, browser.BrowserArgs(url=page1.as_uri()))
    assert opened.ok, opened.payload

    opened_tab = browser.browser_tabs(
        ctx, browser.BrowserTabsArgs(action="new", url=page2.as_uri())
    )
    assert opened_tab.ok, opened_tab.payload
    assert len(opened_tab.payload["tabs"]) == 2
    assert opened_tab.payload["url"].endswith("two.html")

    listed = browser.browser_tabs(ctx, browser.BrowserTabsArgs(action="list"))
    assert listed.ok, listed.payload
    assert len(listed.payload["tabs"]) == 2

    selected = browser.browser_tabs(ctx, browser.BrowserTabsArgs(action="select", index=0))
    assert selected.ok, selected.payload
    assert selected.payload["url"].endswith("one.html")

    moved = browser.browser_open(ctx, browser.BrowserArgs(url=page2.as_uri()))
    assert moved.ok, moved.payload
    back = browser.browser_history(ctx, browser.BrowserHistoryArgs(action="back"))
    assert back.ok, back.payload
    assert back.payload["url"].endswith("one.html")

    closed = browser.browser_tabs(ctx, browser.BrowserTabsArgs(action="close", index=1))
    assert closed.ok, closed.payload
    assert len(closed.payload["tabs"]) == 1


def test_stale_ref_after_navigation_has_actionable_error(tmp_path: Path) -> None:
    page1 = tmp_path / "one.html"
    page1.write_text(
        "<html><body><button id='same'>One</button>"
        "<div id='out'>one</div></body></html>"
    )
    page2 = tmp_path / "two.html"
    page2.write_text(
        "<html><body><button id='same'>Two</button>"
        "<div id='out'>two</div></body></html>"
    )
    ctx = _ctx(tmp_path)
    opened = browser.browser_open(ctx, browser.BrowserArgs(url=page1.as_uri()))
    assert opened.ok, opened.payload
    old_ref = {row["id"]: row for row in opened.payload["interactive"] if row.get("id")}["same"]["ref"]

    moved = browser.browser_open(ctx, browser.BrowserArgs(url=page2.as_uri()))
    assert moved.ok, moved.payload
    new_ref = {row["id"]: row for row in moved.payload["interactive"] if row.get("id")}["same"]["ref"]
    assert new_ref != old_ref, "a new document must not reuse the old ref"

    stale = browser.browser_click(
        ctx, browser.BrowserClickArgs(ref=old_ref, timeout_ms=2000)
    )
    assert not stale.ok
    assert stale.payload.get("error_type") == "stale_or_missing_ref"
    assert "browser_snapshot" in stale.payload.get("hint", "")

def test_browser_frame_snapshot_and_click(tmp_path: Path) -> None:
    child = tmp_path / "child.html"
    child.write_text(
        '<html><body><button id="inner" onclick="document.getElementById(\'out\').textContent=\'clicked\'">Inner</button>'
        "<div id='out'>start</div></body></html>"
    )
    parent = tmp_path / "parent.html"
    parent.write_text(
        "<html><body><h1>parent</h1><iframe src='child.html' width='400' height='200'></iframe></body></html>"
    )
    ctx = _ctx(tmp_path)
    opened = browser.browser_open(ctx, browser.BrowserArgs(url=parent.as_uri()))
    assert opened.ok, opened.payload
    assert len(opened.payload["frames"]) >= 2
    child_snap = browser.browser_snapshot(ctx, browser.BrowserSnapshotArgs(frame_index=1))
    assert child_snap.ok, child_snap.payload
    assert child_snap.payload["frame_index"] == 1
    assert "Inner" in str(child_snap.payload["text"])
    inner = {row["id"]: row for row in child_snap.payload["interactive"] if row.get("id")}
    assert "inner" in inner
    clicked = browser.browser_click(
        ctx, browser.BrowserClickArgs(ref=inner["inner"]["ref"], frame_index=1)
    )
    assert clicked.ok, clicked.payload
    assert "clicked" in str(clicked.payload["text"])


def test_browser_console_dialog_and_pdf(tmp_path: Path) -> None:
    page = tmp_path / "logs.html"
    page.write_text(
        '<html><body><button id="alert" onclick="alert(\'confirm me\')">Alert</button>'
        "<script>console.log('hello-log')</script></body></html>"
    )
    ctx = _ctx(tmp_path)
    opened = browser.browser_open(ctx, browser.BrowserArgs(url=page.as_uri()))
    assert opened.ok, opened.payload
    logs = browser.browser_console(ctx, browser.BrowserConsoleArgs(contains="hello-log"))
    assert logs.ok, logs.payload
    assert logs.payload["count"] >= 1

    armed = browser.browser_dialog(ctx, browser.BrowserDialogArgs(action="accept"))
    assert armed.ok, armed.payload
    assert armed.payload["armed"] == "accept"
    target = {row["id"]: row for row in opened.payload["interactive"] if row.get("id")}
    clicked = browser.browser_click(
        ctx, browser.BrowserClickArgs(ref=target["alert"]["ref"], timeout_ms=5000)
    )
    assert clicked.ok, clicked.payload
    status = browser.browser_dialog(ctx, browser.BrowserDialogArgs(action="status"))
    assert status.ok, status.payload
    assert status.payload["count"] >= 1
    assert status.payload["dialogs"][-1]["handled"] == "accept"

    pdf = browser.browser_pdf(ctx, browser.BrowserPDFArgs(name="logs"))
    assert pdf.ok, pdf.payload
    assert (tmp_path / pdf.payload["path"]).read_bytes().startswith(b"%PDF")

def test_browser_click_download(tmp_path: Path) -> None:
    page = tmp_path / "download.html"
    page.write_text(
        '<html><body><a id="d" href="data:text/plain;base64,aGVsbG8=" download="hello.txt">Download</a></body></html>'
    )
    ctx = _ctx(tmp_path)
    opened = browser.browser_open(ctx, browser.BrowserArgs(url=page.as_uri()))
    assert opened.ok, opened.payload
    ref = {row["id"]: row for row in opened.payload["interactive"] if row.get("id")}["d"]["ref"]
    result = browser.browser_download(
        ctx, browser.BrowserDownloadArgs(ref=ref, name="hello.txt")
    )
    assert result.ok, result.payload
    saved = tmp_path / result.payload["path"]
    assert saved.read_bytes() == b"hello"


def test_browser_engine_options_and_profile_path_validation(tmp_path: Path) -> None:
    cfg = SparkConfig()
    assert browser.browser_options(cfg) == browser.BrowserOptions()
    cfg.agent.browser_engine = "firefox"
    cfg.agent.browser_profile = "work"
    cfg.agent.browser_cdp_endpoint = ""
    cfg.agent.browser_headless = False
    options = browser.browser_options(cfg)
    assert options.engine == "firefox"
    assert options.profile == "work"
    assert options.headless is False
    assert browser._engine_family("chrome") == "chromium"
    assert browser._engine_family("msedge") == "chromium"
    assert browser._engine_family("firefox") == "firefox"
    assert browser._engine_family("webkit") == "webkit"
    with pytest.raises(browser.BrowserError):
        browser._engine_family("netscape")
    with pytest.raises(browser.BrowserError):
        browser._profile_dir(tmp_path, "../escape")
    assert browser._profile_dir(tmp_path, "") is None


def test_browser_profile_config_roundtrip(tmp_path: Path) -> None:
    from spark.config import load_config, save_config

    cfg = SparkConfig()
    cfg.agent.browser_engine = "msedge"
    cfg.agent.browser_profile = "work"
    cfg.agent.browser_cdp_endpoint = "http://127.0.0.1:9222"
    cfg.agent.browser_headless = False
    target = tmp_path / "config.toml"
    save_config(cfg, target)
    loaded = load_config(config_path=target, workdir=tmp_path)
    assert loaded.agent.browser_engine == "msedge"
    assert loaded.agent.browser_profile == "work"
    assert loaded.agent.browser_cdp_endpoint == "http://127.0.0.1:9222"
    assert loaded.agent.browser_headless is False


def test_project_config_cannot_redirect_browser() -> None:
    from spark.config import _sanitize_project_config

    clean = _sanitize_project_config(
        {
            "agent": {
                "browser_engine": "webkit",
                "browser_profile": "saved-login",
                "browser_cdp_endpoint": "http://evil.example:9222",
            }
        }
    )
    agent = clean["agent"]
    assert agent["browser_engine"] == "webkit"
    assert "browser_profile" not in agent
    assert "browser_cdp_endpoint" not in agent


def test_browser_persistent_profile_keeps_login_state(tmp_path: Path) -> None:
    page = tmp_path / "profile.html"
    page.write_text("<html><body>profile</body></html>")
    cfg = SparkConfig()
    cfg.agent.browser_profile = "persistent"
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)

    opened = browser.browser_open(ctx, browser.BrowserArgs(url=page.as_uri()))
    assert opened.ok, opened.payload
    assert (tmp_path / ".spark" / "browser" / "profiles" / "persistent").is_dir()
    saved = browser.browser_eval(
        ctx,
        browser.BrowserEvalArgs(
            expression="localStorage.setItem('spark-profile', 'kept')"
        ),
    )
    assert saved.ok, saved.payload
    worker = browser._get_worker(tmp_path, cfg)
    browser._close_all()
    worker._thread.join(timeout=10)

    reopened = browser.browser_open(ctx, browser.BrowserArgs(url=page.as_uri()))
    assert reopened.ok, reopened.payload
    restored = browser.browser_eval(
        ctx,
        browser.BrowserEvalArgs(expression="localStorage.getItem('spark-profile')"),
    )
    assert restored.ok, restored.payload
    assert "kept" in str(restored.payload["result"])
    state = browser.live_state(tmp_path, cfg)
    assert state["engine"] == "chromium"
    assert state["profile"] == "persistent"
    browser._close_all()


def test_cli_browser_site_rules_list_parsing() -> None:
    from spark.config import SparkConfig
    from spark.onboard import parse_value, set_value

    assert parse_value(
        "agent.browser_site_rules",
        "*.bank.example=deny, docs.example=read",
    ) == ["*.bank.example=deny", "docs.example=read"]
    assert parse_value(
        "agent.browser_site_rules",
        "*.bank.example=deny\n docs.example=read",
    ) == ["*.bank.example=deny", "docs.example=read"]
    cfg = SparkConfig()
    set_value(cfg, "agent.browser_site_rules", "*.bank.example=deny,docs.example=read")
    assert cfg.agent.browser_site_rules == [
        "*.bank.example=deny",
        "docs.example=read",
    ]


def test_browser_site_rules_strongest_match_and_actions() -> None:
    from spark.browser_policy import (
        action_kind,
        guard,
        parse_site_rules,
        strongest_action,
    )

    rules = parse_site_rules(
        [
            "*.bank.example=deny",
            "*.bank.example=allow",  # weaker duplicate must not win
            "docs.example=read",
            "mail.example=prompt",
            "allow.example=allow",
            "# comment",
        ]
    )
    assert strongest_action(rules, "www.bank.example") == "deny"
    assert strongest_action(rules, "docs.example") == "read"
    assert strongest_action(rules, "allow.example") == "allow"
    assert strongest_action(rules, "unknown.example") is None
    assert guard(rules, "browser_snapshot", "docs.example")[0] == "allow"
    assert guard(rules, "browser_click", "docs.example")[0] == "deny"
    assert guard(rules, "browser_click", "mail.example")[0] == "prompt"
    assert guard(rules, "browser_snapshot", "www.bank.example")[0] == "deny"
    assert action_kind("browser_click", {}) == "write"
    assert action_kind("browser_snapshot", {}) == "read"
    assert action_kind("browser_dialog", {"action": "status"}) == "read"
    assert action_kind("browser_dialog", {"action": "accept"}) == "write"
    assert action_kind("browser_tabs", {"action": "list"}) == "read"
    assert action_kind("browser_tabs", {"action": "select"}) == "read"
    assert action_kind("browser_tabs", {"action": "close"}) == "read"
    assert action_kind("browser_future_tool", {}) == "write"
    from spark.browser_policy import DEFAULT_SENSITIVE_RULES, SiteRule

    assert guard(
        [], "browser_click", "accounts.google.com", fallback_rules=DEFAULT_SENSITIVE_RULES
    )[0] == "prompt"
    assert guard(
        [], "browser_snapshot", "accounts.google.com", fallback_rules=DEFAULT_SENSITIVE_RULES
    )[0] == "allow"
    assert guard(
        [SiteRule(pattern="accounts.google.com", action="allow")],
        "browser_click",
        "accounts.google.com",
        fallback_rules=DEFAULT_SENSITIVE_RULES,
    )[0] == "allow"
    # Broad wildcards must not unlock sensitive writes; concrete subdomains may.
    assert guard(
        [SiteRule(pattern="*", action="allow")],
        "browser_click",
        "paypal.com",
        fallback_rules=DEFAULT_SENSITIVE_RULES,
    )[0] == "prompt"
    assert guard(
        [SiteRule(pattern="*.paypal.com", action="allow")],
        "browser_click",
        "www.paypal.com",
        fallback_rules=DEFAULT_SENSITIVE_RULES,
    )[0] == "allow"
    assert guard(
        [SiteRule(pattern="*.com", action="allow")],
        "browser_click",
        "paypal.com",
        fallback_rules=DEFAULT_SENSITIVE_RULES,
    )[0] == "prompt"
    with pytest.raises(ValueError):
        parse_site_rules(["https://bad host=allow"])


def test_policy_honours_browser_site_guard() -> None:
    from spark.config import PermissionRule
    from spark.models import ToolCall
    from spark.policy import BROWSER_TOOLS, call_signature, decide

    call = ToolCall(id="b", name="browser_click", arguments={"ref": "r1"})
    assert "browser_click" in BROWSER_TOOLS
    assert (
        decide("full-auto", call, allow_always=set(), readonly_mcp=set(), browser_guard="deny")
        == "deny"
    )
    assert (
        decide("full-auto", call, allow_always=set(), readonly_mcp=set(), browser_guard="prompt")
        == "prompt"
    )
    assert (
        decide("suggest", call, allow_always=set(), readonly_mcp=set(), browser_guard="allow")
        == "prompt"
    )
    assert (
        decide(
            "full-auto",
            call,
            allow_always={call_signature(call)},
            readonly_mcp=set(),
            browser_guard="deny",
        )
        == "deny"
    )
    # A browser prompt must not downgrade an explicit deny rule.
    deny_rule = PermissionRule(action="deny", resource="**", tool="browser_*")
    assert (
        decide(
            "full-auto",
            call,
            allow_always=set(),
            readonly_mcp=set(),
            rules=[deny_rule],
            browser_guard="prompt",
        )
        == "deny"
    )
    assert (
        decide(
            "full-auto",
            call,
            allow_always=set(),
            readonly_mcp=set(),
            scopes={"local": [deny_rule]},
            browser_guard="prompt",
        )
        == "deny"
    )
    # A sensitive-site prompt still beats a legacy permanent allow.
    assert (
        decide(
            "full-auto",
            call,
            allow_always={call_signature(call)},
            readonly_mcp=set(),
            browser_guard="prompt",
        )
        == "prompt"
    )


def test_browser_trace_and_metrics(tmp_path: Path) -> None:
    page = tmp_path / "trace.html"
    page.write_text("<html><body><button id='b'>Go</button><script>console.log('trace')</script></body></html>")
    ctx = _ctx(tmp_path)
    opened = browser.browser_open(ctx, browser.BrowserArgs(url=page.as_uri()))
    assert opened.ok, opened.payload
    started = browser.browser_trace_start(ctx, browser.BrowserTraceArgs(name="check"))
    assert started.ok, started.payload
    clicked = browser.browser_click(
        ctx, browser.BrowserClickArgs(ref={x["id"]: x for x in opened.payload["interactive"] if x.get("id")}["b"]["ref"])
    )
    assert clicked.ok, clicked.payload
    stopped = browser.browser_trace_stop(ctx, browser.BrowserTraceArgs(name="check"))
    assert stopped.ok, stopped.payload
    trace = tmp_path / stopped.payload["path"]
    assert trace.is_file() and trace.stat().st_size > 0
    assert trace.read_bytes()[:2] == b"PK"
    metrics = browser.browser_metrics(ctx, browser.BrowserMetricsArgs())
    assert metrics.ok, metrics.payload
    assert {"Documents", "LayoutCount"} <= set(metrics.payload["metrics"])
