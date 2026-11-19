from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from spark.models import ToolResult
from spark.pr import GitHubClient, build_context, find_repo, review_brief

if TYPE_CHECKING:
    from spark.tools.registry import ToolContext
from spark.tools.untrusted import wrap


class PrNumberArgs(BaseModel):
    number: int


class PrContentViewArgs(BaseModel):
    number: int
    max_diff_chars: int = 20000


class PrDiffArgs(BaseModel):
    number: int
    max_chars: int = 20000


class PrChecksArgs(BaseModel):
    number: int


class PrReviewArgs(BaseModel):
    number: int
    max_diff_chars: int = 20000


def _guard(ctx: ToolContext) -> str:
    if not getattr(ctx.config.agent, "network_access", True):
        return "网络访问已关闭（agent.network_access=false）"
    try:
        find_repo(ctx.sandbox.root)
    except Exception as exc:  # noqa: BLE001 - surface a readable error
        return str(exc)
    return ""


def _client(ctx: ToolContext) -> GitHubClient:
    return GitHubClient(find_repo(ctx.sandbox.root))


def pr_view(ctx: ToolContext, args: PrNumberArgs) -> ToolResult:
    error = _guard(ctx)
    if error:
        return ToolResult(ok=False, payload={"error": error})
    client = _client(ctx)
    try:
        context = build_context(client, args.number, max_diff_chars=0)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": str(exc)})
    finally:
        client.close()
    return ToolResult(ok=True, payload=context)


def pr_context(ctx: ToolContext, args: PrContentViewArgs) -> ToolResult:
    error = _guard(ctx)
    if error:
        return ToolResult(ok=False, payload={"error": error})
    client = _client(ctx)
    try:
        context = build_context(client, args.number, max_diff_chars=args.max_diff_chars)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": str(exc)})
    finally:
        client.close()
    context = dict(context)
    context["body"] = wrap(str(context.get("body") or ""))
    context["diff"] = wrap(str(context.get("diff") or ""))
    context["comments"] = [
        {**row, "body": wrap(str(row.get("body") or ""))}
        for row in context.get("comments") or []
    ]
    return ToolResult(ok=True, payload=context)


def pr_diff(ctx: ToolContext, args: PrDiffArgs) -> ToolResult:
    error = _guard(ctx)
    if error:
        return ToolResult(ok=False, payload={"error": error})
    client = _client(ctx)
    try:
        diff = client.diff(args.number)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": str(exc)})
    finally:
        client.close()
    return ToolResult(
        ok=True,
        payload={
            "number": args.number,
            "diff": wrap(diff[: args.max_chars]),
            "truncated": len(diff) > args.max_chars,
        },
    )


def pr_review(ctx: ToolContext, args: PrReviewArgs) -> ToolResult:
    error = _guard(ctx)
    if error:
        return ToolResult(ok=False, payload={"error": error})
    client = _client(ctx)
    try:
        context = build_context(client, args.number, max_diff_chars=args.max_diff_chars)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": str(exc)})
    finally:
        client.close()
    brief = review_brief(context)
    context = dict(context)
    context["body"] = wrap(str(context.get("body") or ""))
    context["diff"] = wrap(str(context.get("diff") or ""))
    context["comments"] = [
        {**row, "body": wrap(str(row.get("body") or ""))}
        for row in context.get("comments") or []
    ]
    return ToolResult(ok=True, payload={"review": brief, "context": context})


def pr_checks(ctx: ToolContext, args: PrChecksArgs) -> ToolResult:
    error = _guard(ctx)
    if error:
        return ToolResult(ok=False, payload={"error": error})
    client = _client(ctx)
    try:
        checks = client.checks(args.number)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": str(exc)})
    finally:
        client.close()
    return ToolResult(ok=True, payload={"number": args.number, "checks": checks})
