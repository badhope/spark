"""Tool-call approval policy and wildcard permission rules."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from spark.config import ApprovalMode, PermissionAction, PermissionRule
from spark.models import ToolCall

try:  # optional: minimal installs keep the in-tree matcher below
    from wcmatch import glob as _wc_glob
except Exception:  # pragma: no cover - optional dependency
    _wc_glob = None  # type: ignore[assignment]

_WC_FLAGS: int | None = None
if _wc_glob is not None:
    _WC_FLAGS = _wc_glob.GLOBSTAR | _wc_glob.DOTMATCH

DecisionKind = Literal["allow", "prompt", "deny"]

READONLY_TOOLS = {
    "read_file",
    "list_dir",
    "update_plan",
    # Asking the user changes nothing on disk and must never need approval:
    # gating it meant the question could not be shown until it was approved,
    # which is a deadlock on the approval channel itself.
    "ask_user",
    "grep",
    "diagnostics",
    # 符号定位只读且不执行任何子进程（tree-sitter + 文本扫描），
    # 因此与 grep 同级免审批。
    "find_symbol",
    "mcp_resources",
    "mcp_prompts",
    "glob",
    "web_search",
    "read_notebook",
    "bg_output",
    "bg_list",
    "git_status",
    "git_diff",
    "git_log",
    "git_branch",
    "worktree_list",
    "affected_tests",
    # One-shot read-only orientation: filesystem scan + read-only git queries.
    # No writes, no arbitrary command execution, bounded by max_files/timeout.
    "repo_context",
    "pr_context",
    "pr_view",
    "pr_diff",
    "pr_checks",
    "pr_review",
}
# These run code, spawn agents, or reach the network on behalf of the repository
# being edited, so they are not read-only: ``run_tests`` executes whatever the
# cloned repo declares as its test command, ``task`` fans out sub-agents that
# inherit the caller's grants, and ``web_fetch`` can be aimed at internal
# endpoints. They prompt unless the user opted into ``full-auto``.
EXECUTE_TOOLS = {
    "run_tests",
    "task",
    "web_fetch",
    # LSP tools start a trusted language-server child process; they are not
    # read-only even when the request itself only asks for a definition.
    "lsp_definition",
    "lsp_references",
    "lsp_hover",
    "lsp_symbols",
    "lsp_diagnostics",
    "lsp_code_action",
    "lsp_rename",
    "rename_symbol",
}
WRITE_TOOLS = {
    "write_file",
    "apply_patch",
    "apply_edit",
    "notebook_edit",
    "git_add",
    "git_commit",
    "task_step_commit",
    "task_finish",
}
SHELL_TOOLS = {"run_shell", "bg_start", "bg_kill"}
# Playwright 浏览器动作走同一套审批门；full-auto 下必须放行，否则
# approver 被刻意置空后浏览器工具会直接报 "no approver"。
BROWSER_TOOLS = {
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
}
# Shell and worktree-mutating tools can have unbounded blast radius, so they may
# never be granted a permanent "always allow".
NEVER_PERSIST_ALLOW = SHELL_TOOLS | BROWSER_TOOLS | {
    "worktree_create",
    "worktree_remove",
    "task_begin",
    "task_finish",
    "lsp_rename",
    "rename_symbol",
}


# ---------------------------------------------------------------------------
# Wildcard matching
# ---------------------------------------------------------------------------


def _wildcard_match(pattern: str, value: str) -> bool:
    """Match value against a glob-style pattern for permission rules.

    Supports: '*' (any chars except '/'), '**' (any chars including '/'),
    '?' (single char).  Normalises path separators so backslashes and
    forward slashes are equivalent.
    """
    normalized_pattern = pattern.replace("\\", "/")
    normalized_value = value.replace("\\", "/")
    return _glob_match(normalized_pattern, normalized_value)


_GLOB_MAX_DEPTH = 64  # Guard against adversarial **/**/**... patterns


def _glob_match(pattern: str, value: str) -> bool:
    """Glob matcher: '*' stays within a single path segment, '**' crosses '/'.

    Prefer wcmatch's ``GLOBSTAR`` implementation (same semantics, battle-tested
    against pathological patterns); keep the in-tree matcher for minimal
    installs where wcmatch is absent.
    """
    if _wc_glob is not None and _WC_FLAGS is not None:
        try:
            return bool(_wc_glob.globmatch(value, pattern, flags=_WC_FLAGS))
        except Exception:  # pragma: no cover - malformed pattern -> fallback
            pass
    return _legacy_glob_match(pattern, value)


def _legacy_glob_match(pattern: str, value: str) -> bool:
    """In-tree fallback; identical semantics to the wcmatch path."""
    p_len, v_len = len(pattern), len(value)

    def _m(pi: int, vi: int, depth: int = 0) -> bool:
        if depth > _GLOB_MAX_DEPTH:
            return False  # Refuse to match pathological patterns
        while pi < p_len:
            c = pattern[pi]
            if c == "*" and pi + 1 < p_len and pattern[pi + 1] == "*":  # '**'
                pn = pi + 2
                if pn < p_len and pattern[pn] == "/":
                    pn += 1
                for vn in range(vi, v_len + 1):
                    if vn not in (vi, v_len) and value[vn - 1] != "/":
                        continue
                    if _m(pn, vn, depth + 1):
                        return True
                return False
            if c == "*":  # single-segment '*', stops at '/'
                pn = pi + 1
                for vn in range(vi, v_len):
                    if value[vn] == "/":
                        break
                    if _m(pn, vn + 1, depth + 1):
                        return True
                return _m(pn, vi, depth + 1)
            if c == "?":
                if vi >= v_len or value[vi] == "/":
                    return False
                pi, vi = pi + 1, vi + 1
                continue
            if vi >= v_len or value[vi] != c:
                return False
            pi, vi = pi + 1, vi + 1
        return vi == v_len

    return _m(0, 0)


# ---------------------------------------------------------------------------
# Permission rules
# ---------------------------------------------------------------------------


def evaluate_permission(
    rules: list[PermissionRule], action: str, resource: str
) -> PermissionAction:
    """Return the decision from the last matching rule, or ``"prompt"`` default.

    A rule matches when its ``tool`` glob accepts *action* (the tool name) and
    its ``resource`` glob accepts *resource*.  An empty *resource* matches
    nothing: without this, a single ``allow **`` rule would silently approve
    every tool whose resource cannot be derived (``task``, ``web_fetch``, MCP
    tools), turning the rule list into a global approval kill-switch.
    Rules are evaluated in order and the *last* match wins.
    """
    matched: PermissionAction = "prompt"
    for rule in rules:
        if not resource:
            continue
        if rule.tool and not _wildcard_match(rule.tool, action):
            continue
        if _wildcard_match(rule.resource, resource):
            matched = rule.action
    return matched


# ---------------------------------------------------------------------------
# Helpers (unchanged)
# ---------------------------------------------------------------------------


def call_signature(tool: ToolCall) -> str:
    """Stable signature of (tool name, arguments) used for scoped approvals."""
    payload = json.dumps(
        {"name": tool.name, "arguments": tool.arguments},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return f"{tool.name}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def is_mcp_tool(name: str) -> bool:
    return name.startswith("mcp__")


def mcp_parts(name: str) -> tuple[str, str] | None:
    if not is_mcp_tool(name):
        return None
    parts = name.split("__", 2)
    if len(parts) != 3:
        return None
    return parts[1], parts[2]


def mcp_tool_allowed(allowed_tools: list[str], tool_name: str) -> bool:
    """P1-9: 第三方 MCP server 最小权限白名单判定。

    ``allowed_tools`` 来自该 server 的配置（glob 列表）：

    * 空列表 = 未声明白名单 → 向后兼容，全部放行（历史行为）。
    * 非空 = 最小权限模式：``tool_name`` 命中任意一个 glob 才放行；
      未声明的工具一律拒绝（默认拒绝未声明项）。``"*"`` 显式放行全部。
    """
    if not allowed_tools:
        return True
    return any(_wildcard_match(pattern, tool_name) for pattern in allowed_tools)


# ---------------------------------------------------------------------------
# Resource extraction
# ---------------------------------------------------------------------------


def _extract_resource(tool: ToolCall) -> str:
    """Derive the resource string from a tool call's arguments.

    Used by :func:`decide` when evaluating wildcard permission rules.
    """
    args = tool.arguments
    if tool.name in {"write_file", "apply_patch", "apply_edit", "notebook_edit"}:
        return args.get("path", "")
    if tool.name in {"run_shell", "bg_start"}:
        return args.get("command", "")
    if tool.name in {"git_add", "git_commit"}:
        paths = args.get("paths", [])
        if isinstance(paths, list) and paths:
            return paths[0]
        return ""
    if tool.name in BROWSER_TOOLS:
        # Browser tools have no filesystem path.  Give them a stable non-empty
        # resource so an explicit ``tool="browser_*"`` allow rule can grant
        # browser automation without opening up every other tool.
        return str(args.get("url") or args.get("selector") or args.get("name") or "browser")
    return ""


# ---------------------------------------------------------------------------
# Decision entry point
# ---------------------------------------------------------------------------


def _scoped_rule_decision(
    scopes: dict[str, list[PermissionRule]] | None, tool: ToolCall
) -> tuple[bool, bool]:
    """Return ``(has_deny, has_allow)`` across user/project/local layers.

    A deny in any layer wins over every allow, including a stale ``allow_always``
    signature. An empty resource never matches: ``allow **`` must not become a
    global approval switch for tools whose resource cannot be derived.
    """
    if not scopes:
        return False, False
    resource = _extract_resource(tool)
    if not resource:
        return False, False
    allow = False
    for layer in ("user", "project", "local"):
        for rule in scopes.get(layer) or []:
            if rule.tool and not _wildcard_match(rule.tool, tool.name):
                continue
            if not _wildcard_match(rule.resource, resource):
                continue
            if rule.action == "deny":
                return True, False
            if rule.action == "allow":
                allow = True
    return False, allow


def _rule_decision(
    rules: list[PermissionRule] | None, tool: ToolCall
) -> PermissionAction:
    """Evaluate wildcard rules once; callers apply deny before every allow path."""
    if not rules:
        return "prompt"
    return evaluate_permission(rules, tool.name, _extract_resource(tool))


def _browser_guard_decision(
    tool: ToolCall,
    browser_guard: Literal["allow", "prompt", "deny"] | None,
) -> DecisionKind | None:
    """Per-domain browser rules are a hard layer before legacy/scoped allows.

    A page can navigate after approval; the host checked now is the host the
    action will run on, so this must not be downgraded by ``allow_always``.
    """
    if tool.name not in BROWSER_TOOLS:
        return None
    if browser_guard == "deny":
        return "deny"
    if browser_guard == "prompt":
        return "prompt"
    return None


def _mode_decision(
    mode: ApprovalMode,
    tool: ToolCall,
    plan_approved: bool,
    readonly_mcp: set[str],
) -> DecisionKind:
    """Final fallback by tool class and approval mode."""
    if tool.name in READONLY_TOOLS or tool.name in readonly_mcp:
        return "allow"
    if tool.name in BROWSER_TOOLS:
        # Generic browser approval: suggest/manual ask, full-auto runs. The
        # per-domain decision was already applied above.
        return "allow" if mode == "full-auto" else "prompt"
    if tool.name in WRITE_TOOLS:
        if mode in ("suggest", "manual", "plan-only"):
            return "prompt"
        if mode == "approve-plan":
            return "allow" if plan_approved else "prompt"
        return "allow"
    if tool.name in EXECUTE_TOOLS or tool.name in SHELL_TOOLS or is_mcp_tool(tool.name):
        if mode == "full-auto":
            return "allow"
        if mode == "approve-plan" and plan_approved:
            return "allow"
        return "prompt"
    return "prompt"


def decide(
    mode: ApprovalMode,
    tool: ToolCall,
    *,
    allow_always: set[str],
    readonly_mcp: set[str],
    rules: list[PermissionRule] | None = None,
    plan_approved: bool = False,
    scopes: dict[str, list[PermissionRule]] | None = None,
    browser_guard: Literal["allow", "prompt", "deny"] | None = None,
) -> DecisionKind:
    # Deny is the highest-priority signal regardless of which layer or rule set
    # produced it; a browser guard can only add a prompt, never downgrade it.
    scoped_deny, scoped_allow = _scoped_rule_decision(scopes, tool)
    if scoped_deny:
        return "deny"
    rule_decision = _rule_decision(rules, tool)
    if rule_decision == "deny":
        return "deny"

    browser = _browser_guard_decision(tool, browser_guard)
    if browser is not None:
        return browser

    # Scoped allow is intentionally before allow_always: a project rule can
    # grant a tool, but deny already ran first and still wins.
    if scoped_allow:
        return "allow"
    # Approvals are bound to the exact (tool, arguments) pair, so approving one
    # command can never silently authorize a different one later.
    if call_signature(tool) in allow_always:
        return "allow"
    if rule_decision == "allow":
        return "allow"
    return _mode_decision(mode, tool, plan_approved, readonly_mcp)
