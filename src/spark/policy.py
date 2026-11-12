"""Tool-call approval policy and wildcard permission rules."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from spark.config import ApprovalMode, PermissionAction, PermissionRule
from spark.models import ToolCall

_wc_glob: Any = None
try:  # optional: minimal installs keep the in-tree matcher below
    from wcmatch import glob as _wc_glob
except Exception:  # pragma: no cover - optional dependency
    pass

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
}
# These run code, spawn agents, or reach the network on behalf of the repository
# being edited, so they are not read-only: ``run_tests`` executes whatever the
# cloned repo declares as its test command, ``task`` fans out sub-agents that
# inherit the caller's grants, and ``web_fetch`` can be aimed at internal
# endpoints. They prompt unless the user opted into ``full-auto``.
EXECUTE_TOOLS = {"run_tests", "task", "web_fetch"}
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
# Shell and worktree-mutating tools can have unbounded blast radius, so they may
# never be granted a permanent "always allow".
NEVER_PERSIST_ALLOW = SHELL_TOOLS | {
    "worktree_create",
    "worktree_remove",
    "task_begin",
    "task_finish",
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
    return ""


# ---------------------------------------------------------------------------
# Decision entry point
# ---------------------------------------------------------------------------


def decide(
    mode: ApprovalMode,
    tool: ToolCall,
    *,
    allow_always: set[str],
    readonly_mcp: set[str],
    rules: list[PermissionRule] | None = None,
    plan_approved: bool = False,
    scopes: dict[str, list[PermissionRule]] | None = None,
) -> DecisionKind:
    # H3 多作用域（deny 跨层优先）：任何一层（user/project/local）的 deny
    # 规则都拒绝——包括压过 allow_always（防过期授权被仓库层 deny 打穿）；
    # 无 deny 时任何一层的 allow 都放行。空 resource 不参与规则匹配
    # （防 "allow **" 变成全局放行，见 evaluate_permission 同款约束）。
    if scopes:
        resource = _extract_resource(tool)
        if resource:
            scoped_allow = False
            for layer in ("user", "project", "local"):
                for rule in scopes.get(layer) or []:
                    if rule.tool and not _wildcard_match(rule.tool, tool.name):
                        continue
                    if not _wildcard_match(rule.resource, resource):
                        continue
                    if rule.action == "deny":
                        return "deny"
                    if rule.action == "allow":
                        scoped_allow = True
            if scoped_allow:
                return "allow"

    # Approvals are bound to the exact (tool, arguments) pair, so approving one
    # command can never silently authorize a different one later.
    if call_signature(tool) in allow_always:
        return "allow"

    # Wildcard rule evaluation
    if rules:
        resource = _extract_resource(tool)
        rule_decision = evaluate_permission(rules, tool.name, resource)
        if rule_decision == "deny":
            return "deny"
        if rule_decision == "allow":
            return "allow"
        # rule_decision == "prompt" falls through to the original logic below

    if tool.name in READONLY_TOOLS:
        return "allow"
    if tool.name in readonly_mcp:
        return "allow"
    if tool.name in WRITE_TOOLS:
        # 写工具：suggest/manual/plan-only 提示；approve-plan 需计划已批准；
        # auto-edit/full-auto 自动。
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
