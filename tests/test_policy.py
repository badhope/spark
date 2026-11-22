from spark.models import ToolCall
from spark.policy import decide


def _call(name: str, **args) -> ToolCall:
    return ToolCall(id="1", name=name, arguments=args)


def test_suggest_prompts_write_and_shell() -> None:
    assert (
        decide("suggest", _call("read_file"), allow_always=set(), readonly_mcp=set())
        == "allow"
    )
    assert (
        decide("suggest", _call("write_file"), allow_always=set(), readonly_mcp=set())
        == "prompt"
    )
    assert (
        decide("suggest", _call("run_shell"), allow_always=set(), readonly_mcp=set())
        == "prompt"
    )


def test_auto_edit_prompts_only_shell() -> None:
    assert (
        decide("auto-edit", _call("write_file"), allow_always=set(), readonly_mcp=set())
        == "allow"
    )
    assert (
        decide("auto-edit", _call("run_shell"), allow_always=set(), readonly_mcp=set())
        == "prompt"
    )


def test_full_auto_allows_builtin() -> None:
    assert (
        decide("full-auto", _call("write_file"), allow_always=set(), readonly_mcp=set())
        == "allow"
    )
    assert (
        decide("full-auto", _call("run_shell"), allow_always=set(), readonly_mcp=set())
        == "allow"
    )


def test_full_auto_allows_browser_tools_without_approver() -> None:
    # full-auto deliberately installs no approver; browser actions must be
    # decided before the final "prompt" fallthrough or every call fails with
    # ``no approver`` even though the user chose full-auto.
    for name in (
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
    ):
        assert (
            decide("full-auto", _call(name), allow_always=set(), readonly_mcp=set())
            == "allow"
        )


def test_suggest_still_prompts_browser_actions() -> None:
    assert (
        decide("suggest", _call("browser_open"), allow_always=set(), readonly_mcp=set())
        == "prompt"
    )


def test_browser_permission_rule_is_scoped_to_browser_tools() -> None:
    from spark.config import PermissionRule

    rules = [PermissionRule(action="allow", resource="**", tool="browser_*")]
    assert (
        decide(
            "suggest",
            _call("browser_open", url="https://example.com"),
            allow_always=set(),
            readonly_mcp=set(),
            rules=rules,
        )
        == "allow"
    )
    assert (
        decide(
            "suggest",
            _call("browser_click", selector="#submit"),
            allow_always=set(),
            readonly_mcp=set(),
            rules=rules,
        )
        == "allow"
    )
    # The rule must not leak into unrelated tools with no resource.
    assert (
        decide(
            "suggest",
            _call("run_shell", command="echo hi"),
            allow_always=set(),
            readonly_mcp=set(),
            rules=rules,
        )
        == "prompt"
    )


def test_allow_always_and_mcp() -> None:
    from spark.policy import call_signature

    allowed = {call_signature(_call("run_shell", command="echo hi"))}
    assert (
        decide(
            "suggest",
            _call("run_shell", command="echo hi"),
            allow_always=allowed,
            readonly_mcp=set(),
        )
        == "allow"
    )
    # A different command must still prompt, even for the same tool.
    assert (
        decide(
            "suggest",
            _call("run_shell", command="rm -rf x"),
            allow_always=allowed,
            readonly_mcp=set(),
        )
        == "prompt"
    )
    assert (
        decide(
            "auto-edit", _call("mcp__fs__write"), allow_always=set(), readonly_mcp=set()
        )
        == "prompt"
    )
    assert (
        decide(
            "suggest",
            _call("mcp__fs__read"),
            allow_always=set(),
            readonly_mcp={"mcp__fs__read"},
        )
        == "allow"
    )
