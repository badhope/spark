"""Per-domain browser authorization rules.

Rules are deliberately data, not code: a cloned repository must not be able to
grant itself browser access, and every decision must be explainable.  The
strongest matching rule wins (deny > read > prompt > allow), so adding a broad
``allow`` line can never weaken a narrower ``deny`` line.

Syntax: one rule per string, ``host-pattern=action``.  Actions:

* ``allow``  — read and write actions are allowed.
* ``read``   — read actions allowed; write actions are denied.
* ``prompt`` — read actions allowed; write actions need an explicit approval.
* ``deny``   — no action is allowed.

Host patterns use shell glob syntax and are matched case-insensitively:
``example.com`` (exact), ``*.example.com`` (subdomains only), ``*`` (all).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

SiteAction = Literal["allow", "read", "prompt", "deny"]
ActionKind = Literal["read", "write"]
_SITE_ACTIONS = {"allow", "read", "prompt", "deny"}
_RANK = {"allow": 0, "prompt": 1, "read": 2, "deny": 3}
_PATTERN_RE = re.compile(r"[A-Za-z0-9*?._-]+")
#: Hosts where a wrong click can move money or grant account access.
SENSITIVE_HOST_PATTERNS: tuple[str, ...] = (
    "alipay.com",
    "*.alipay.com",
    "paypal.com",
    "*.paypal.com",
    "stripe.com",
    "*.stripe.com",
    "pay.weixin.qq.com",
    "unionpay.com",
    "*.unionpay.com",
    "*.bank.*",
    "accounts.google.com",
    "login.microsoftonline.com",
    "okta.com",
    "*.okta.com",
    "auth0.com",
    "*.auth0.com",
    "console.aws.amazon.com",
    "console.cloud.google.com",
    "portal.azure.com",
)

_WRITE_TOOLS = {
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_select",
    "browser_eval",
    "browser_file_upload",
}
_READ_TOOLS = {
    "browser_open",
    "browser_search",
    "browser_snapshot",
    "browser_hover",
    "browser_scroll",
    "browser_history",
    "browser_wait",
    "browser_screenshot",
    "browser_viewport",
    "browser_download",
    "browser_pdf",
    "browser_console",
    "browser_network",
    "browser_trace_start",
    "browser_trace_stop",
    "browser_metrics",
}


@dataclass(frozen=True)
class SiteRule:
    pattern: str
    action: SiteAction


#: Fallback for sensitive hosts.  A user rule that matches the host replaces
#: these completely, so an explicit ``allow`` is a real override.
DEFAULT_SENSITIVE_RULES: tuple[SiteRule, ...] = tuple(
    SiteRule(pattern=pattern, action="prompt") for pattern in SENSITIVE_HOST_PATTERNS
)


def _normalize_pattern(raw: str) -> str:
    pattern = raw.strip().lower()
    if "://" in pattern:
        pattern = urlparse(pattern).hostname or ""
    if not pattern or not _PATTERN_RE.fullmatch(pattern):
        raise ValueError(
            "browser_site_rules host pattern may only contain letters, digits, *, ?, dot, underscore or hyphen"
        )
    if ".." in pattern:
        raise ValueError("browser_site_rules host pattern must not contain '..'")
    return pattern


def parse_site_rules(raw: list[str]) -> list[SiteRule]:
    rules: list[SiteRule] = []
    for line in raw or []:
        text = str(line).strip()
        if not text or text.startswith("#"):
            continue
        if "=" not in text:
            raise ValueError(f"browser_site_rules entry must be host=action: {text!r}")
        host, action = (part.strip() for part in text.split("=", 1))
        action = action.lower()
        if action not in _SITE_ACTIONS:
            raise ValueError(
                f"browser_site_rules action must be allow, read, prompt or deny: {text!r}"
            )
        rules.append(SiteRule(pattern=_normalize_pattern(host), action=action))  # type: ignore[arg-type]
    return rules


def validate_site_rules(raw: list[str]) -> str | None:
    try:
        parse_site_rules(raw)
    except ValueError as exc:
        return str(exc)
    return None


def _matches(pattern: str, host: str) -> bool:
    host = host.lower().strip(".")
    return fnmatch.fnmatchcase(host, pattern)


def strongest_action(rules: list[SiteRule], host: str) -> SiteAction | None:
    if not host:
        return None
    matched: list[SiteAction] = [
        rule.action for rule in rules if _matches(rule.pattern, host)
    ]
    if not matched:
        return None
    return max(matched, key=lambda action: _RANK[action])


def action_kind(tool_name: str, args: dict[str, Any] | None = None) -> ActionKind:
    """Classify a browser tool as read-only or page-mutating.

    Conservative by default: an unknown/new browser tool is treated as a write
    action, so a future tool cannot silently inherit read-only access.
    """
    args = args or {}
    if tool_name in _WRITE_TOOLS:
        return "write"
    if tool_name == "browser_dialog":
        return "read" if str(args.get("action") or "") in {"status", "dismiss"} else "write"
    if tool_name == "browser_tabs":
        # list/select/new/close never mutate page content. ``select`` is
        # checked against the target tab's host by evaluate_call_guard(), so a
        # denied tab cannot be activated. Unknown tab actions stay writes.
        return (
            "read"
            if str(args.get("action") or "list") in {"list", "select", "new", "close"}
            else "write"
        )
    if tool_name in _READ_TOOLS:
        return "read"
    return "write"


def _specific_override(pattern: str, host: str) -> bool:
    """Whether a rule is narrow enough to override a sensitive-site fallback.

    Exact hosts always qualify. A subdomain glob must carry at least two fixed
    labels (``*.paypal.com`` qualifies, ``*.com`` and ``*`` do not), so a broad
    "allow everything" convenience rule cannot silently unlock banking writes.
    """
    pattern = pattern.lower()
    if pattern == host.lower().strip("."):
        return True
    if not pattern.startswith("*."):
        return False
    fixed = [
        part
        for part in pattern[2:].split(".")
        if part and "*" not in part and "?" not in part
    ]
    return len(fixed) >= 2


def guard(
    rules: list[SiteRule],
    tool_name: str,
    host: str,
    args: dict[str, Any] | None = None,
    *,
    fallback_rules: list[SiteRule] | tuple[SiteRule, ...] = (),
) -> tuple[Literal["allow", "prompt", "deny"], str]:
    """Return the guard decision for one browser call on one host.

    A concrete user rule for the host can override a built-in sensitive-site
    fallback. Broad wildcard allows cannot erase that default, so ``*=allow``
    does not silently unlock payment or banking writes.
    """
    action = strongest_action(rules, host)
    if action is None and fallback_rules:
        action = strongest_action(list(fallback_rules), host)
        if action is not None:
            kind = action_kind(tool_name, args)
            if action == "deny":
                return "deny", f"sensitive site blocked: {host}"
            if action == "read" and kind == "write":
                return "deny", f"sensitive site read-only: {host}"
            if action == "prompt" and kind == "write":
                return "prompt", f"sensitive site needs confirmation: {host}"
            return "allow", ""
    if fallback_rules:
        fallback = strongest_action(list(fallback_rules), host)
        # A broad ``*=allow`` must not erase a stricter fallback. A concrete
        # host/subdomain override still may.
        if fallback and action == "allow":
            override = any(
                rule.action == "allow"
                and _matches(rule.pattern, host)
                and _specific_override(rule.pattern, host)
                for rule in rules
            )
            if not override:
                kind = action_kind(tool_name, args)
                if fallback == "deny":
                    return "deny", f"sensitive site blocked: {host}"
                if fallback == "read" and kind == "write":
                    return "deny", f"sensitive site read-only: {host}"
                if fallback == "prompt" and kind == "write":
                    return "prompt", f"sensitive site needs confirmation: {host}"
                return "allow", ""
    if action is None:
        return "allow", ""
    kind = action_kind(tool_name, args)
    if action == "deny":
        return "deny", f"browser_site_rules deny: {host}"
    if action == "read" and kind == "write":
        return "deny", f"browser_site_rules read-only: {host}"
    if action == "prompt" and kind == "write":
        return "prompt", f"browser_site_rules prompt: {host}"
    return "allow", ""


def host_of_url(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
