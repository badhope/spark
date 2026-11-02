"""Frontend source invariants that a headless screenshot pass already caught once.

These are cheap "won't regress silently" checks for bugs that TS cannot see and
that only appear at runtime in a real browser (React #310 white screen, hidden
buttons still occupying the mobile header).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "src"


def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def test_settings_hooks_come_before_the_cfg_early_return() -> None:
    """Hooks after `if (!cfg) return` change hook count → React #310 white screen."""
    src = _read("components/SettingsModal.tsx")
    early = src.find("if (!cfg) {")
    assert early != -1, "expected the loading early-return in SettingsModal"
    for hook in ("useState(() => notificationsEnabled())", "useState(() => notificationPermission())"):
        pos = src.find(hook)
        assert pos != -1, f"missing {hook}"
        assert pos < early, f"{hook} must be declared before the `if (!cfg)` early return"


def test_header_responsive_hiding_wraps_icon_buttons() -> None:
    """`hidden` on IconButton loses to its own `inline-flex` (Tailwind order)."""
    src = _read("components/Header.tsx")
    assert 'className="hidden sm:inline-flex"' in src or 'className="hidden md:inline-flex"' in src
    for line in src.splitlines():
        if "<IconButton" in line and "hidden " in line:
            raise AssertionError(f"responsive `hidden` must wrap IconButton, not be passed into it: {line.strip()}")


def test_shell_card_escapes_output() -> None:
    """`!cmd` output is rendered as text, never as HTML."""
    src = _read("components/MessageItem.tsx")
    start = src.find("const ShellMessage")
    assert start != -1
    body = src[start : src.find("export default function MessageItem", start)]
    assert "{shell.stdout}" in body
    assert "dangerouslySetInnerHTML" not in body


def test_composer_routes_bang_commands_to_shell() -> None:
    src = _read("components/Composer.tsx")
    assert 'input.startsWith("!")' in src
    assert "runShell(input.slice(1).trim())" in src


def test_share_command_is_in_the_palette() -> None:
    src = _read("components/CommandPalette.tsx")
    assert "downloadShareHtml" in src
    assert 'exportSession("html")' in src


def test_settings_save_refreshes_redacted_mcp_rows() -> None:
    src = _read("components/SettingsModal.tsx")
    save = src[src.index("async function handleSave()"):src.index("async function handleTest()")]
    assert "env_replace: true" in save
    assert "setMcpRows(data.config.mcp_servers" in save
    assert 'disabled={busy === "save"}' in src[src.index('{tab === "mcp"'):src.index('{tab === "project"')]


def test_resilience_fields_allow_explicit_empty_values() -> None:
    src = _read("components/SettingsModal.tsx")
    save = src[src.index("async function handleSave()"):src.index("async function handleTest()")]
    for key in ("api_keys", "fallback_models", "small_model"):
        assert f"resilienceDirty.current.{key}" in save
        assert f"resilienceDirty.current.{key} = true" in src


def test_dev_proxy_keeps_backend_origin_for_csrf_guard() -> None:
    src = (ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")
    assert "const backendOrigin = new URL(backendTarget).origin" in src
    assert "req.headers.origin" in src
    assert "req.headers.host" in src
    assert "403" in src
    assert "proxyReq.setHeader('origin', backendOrigin)" in src


def test_profile_form_explicitly_binds_environment_variable() -> None:
    src = _read("components/SettingsModal.tsx")
    save = src[src.index("async function handleSaveProfile()"):src.index("async function handleActivate(")]
    form = src[src.index("{showProfileForm && ("):src.index("{profiles.length === 0")]
    assert 'api_key_env: profileForm.api_key_env.trim()' in save
    assert 'value={profileForm.api_key_env}' in form
    assert 'api_key_env: e.target.value' in form
    assert 'api_key_env: ""' in src


def test_advanced_settings_controls_reach_the_payload() -> None:
    """Every advanced control must be wired into SettingsPayload, not just drawn."""
    src = _read("components/SettingsModal.tsx")
    save = src[src.index("async function handleSave()"):src.index("async function handleTest()")]
    for key in (
        "repo_map_max_chars",
        "knowledge_enabled",
        "knowledge_top_k",
        "knowledge_max_chars",
        "agents_md",
        "embedding_enabled",
        "embedding_base_url",
        "embedding_api_key",
        "rerank_model",
        "api_key_env",
        "replay",
        "updater",
    ):
        assert f"{key}:" in save, f"{key} missing from the settings save payload"
    # The embedding key must follow the same "blank = keep stored" contract as
    # provider.api_key; sending an empty string would clear it server-side.
    assert "embeddingKeyInput.trim() ? { embedding_api_key:" in save
