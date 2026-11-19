from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

import typer

from spark.brand import version_line
from spark.errors import ConfigError, PluginError, SparkError

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps CLI startup fast
    # Runtime-loaded globals (see ``_load_runtime_imports``); declaring them
    # here keeps mypy's name-defined gate green without importing pydantic on
    # the ``--version`` path.
    from typing import Any

    from spark.config import (
        ApprovalMode,
        ProviderName,
        SandboxMode,
        SparkConfig,
    )
    from spark.onboard import SetupError
    from spark.providers.routing import Budget, TaskType
    from spark.sandbox import WorkdirSandbox
    from spark.store import SessionStore
    from spark.tools.mcp_bridge import McpBridge
    from spark.tools.registry import ToolRegistry

    ensure_home: Any
    write_config_template: Any
    default_home: Any
    load_config: Any
    require_api_key: Any
    friendly_config_error: Any
    read_config: Any
    activate_model_profile: Any
    save_config: Any
    banner: Any
    ensure_skeleton: Any
    apply_setup: Any
    save: Any
    env_api_key: Any
    check_health: Any
    render_health: Any
    health_exit_code: Any
    config_as_dict: Any
    render_config_text: Any
    set_value: Any


_RUNTIME_IMPORTS_LOADED = False
#: Set by the root callback. Only a per-process CLI override; never persisted.
_RUNTIME_ROUTE: dict[str, str] = {}
_CONFIG_NAMES = (
    "ApprovalMode",
    "ProviderName",
    "SandboxMode",
    "SparkConfig",
    "activate_model_profile",
    "default_home",
    "ensure_home",
    "env_api_key",
    "load_config",
    "require_api_key",
    "save_config",
    "write_config_template",
)
_ONBOARD_NAMES = (
    "SetupError",
    "apply_setup",
    "banner",
    "check_health",
    "config_as_dict",
    "ensure_skeleton",
    "friendly_config_error",
    "health_exit_code",
    "read_config",
    "render_config_text",
    "render_health",
    "save",
    "set_value",
)


def _load_runtime_imports() -> None:
    """Load config/onboarding/sandbox only when a real command needs them.

    ``spark --version`` and ``spark version`` must not pay pydantic-settings +
    tomlkit + sandbox imports. Every other command reaches this through the
    Typer callback or a helper that is called from the command body.
    """
    global _RUNTIME_IMPORTS_LOADED
    if _RUNTIME_IMPORTS_LOADED:
        return
    import spark.config as _config
    import spark.onboard as _onboard
    from spark.sandbox import WorkdirSandbox

    for name in _CONFIG_NAMES:
        if name not in globals():
            globals()[name] = getattr(_config, name)
    for name in _ONBOARD_NAMES:
        if name not in globals():
            globals()[name] = getattr(_onboard, name)
    if "WorkdirSandbox" not in globals():
        globals()["WorkdirSandbox"] = WorkdirSandbox
    _RUNTIME_IMPORTS_LOADED = True


def __getattr__(name: str):
    """Keep historical ``spark.cli.<name>`` access working on demand.

    Tests and embedders monkeypatch ``cli.default_home`` / read
    ``cli.friendly_config_error``; without this hook those lookups fail before
    a command has triggered the lazy runtime import.
    """
    if name in _CONFIG_NAMES or name in _ONBOARD_NAMES or name == "WorkdirSandbox":
        _load_runtime_imports()
        try:
            return globals()[name]
        except KeyError:
            pass
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def probe_provider(*args, **kwargs):
    """Lazy shim: the probe pulls litellm (~6.7s), only needed by doctor/test."""
    from spark.providers.probe import probe_provider as _probe

    return _probe(*args, **kwargs)


def _agent_loop():
    """Import the agent loop lazily.

    ``spark.core.loop`` pulls litellm + langgraph (~7.8s on this machine), which
    made every CLI invocation — including ``spark --version`` — pay the full
    agent start-up cost. Only commands that actually run a turn need it.
    """
    from spark.core.loop import AgentLoop as _AgentLoop

    return _AgentLoop

app = typer.Typer(
    # Typer's completion machinery is built in; keeping it off only removed the
    # `--install-completion` flag. Shell completion is a real usability win for
    # a CLI with 14 subcommands.
    add_completion=True,
    invoke_without_command=True,
    no_args_is_help=False,
    help="◆ Spark [Ember] — 本地编程智能体。新手从 spark init → spark doctor → spark web 开始。",
    epilog=(
        "常用流程：spark init 首次配置 · spark doctor 体检 · spark web 图形界面 · spark exec 脚本化"
    ),
    context_settings={"allow_extra_args": True},
)


def _store() -> "SessionStore":
    from spark.store import SessionStore

    _load_runtime_imports()
    ensure_home()
    write_config_template(default_home() / "config.toml")
    return SessionStore(default_home() / "sessions.db")


def _resolve_session(
    store: SessionStore, workdir: Path, cfg: SparkConfig, resume: str | None
) -> str:
    if resume:
        row = store.get_session(resume)
        if not row:
            raise ConfigError(f"Unknown session id: {resume}")
        return resume
    return store.create_session(workdir, cfg.provider.model)


async def _boot_mcp(cfg: SparkConfig, registry: ToolRegistry) -> McpBridge:
    from spark.tools.mcp_bridge import McpBridge

    bridge = McpBridge()
    if cfg.mcp_servers:
        await bridge.start(cfg.mcp_servers, registry)
    return bridge


def is_native_provider(cfg: object) -> bool:
    return getattr(getattr(cfg, "provider", None), "name", "") in {
        "openai",
        "anthropic",
        "gemini",
        "azure",
        "bedrock",
    }


def _common_cfg(
    workdir: Path,
    config: Path | None,
    approval: str | None,
    model: str | None,
    provider: str | None,
    sandbox_mode: str | None = None,
    add_dirs: list[Path] | None = None,
) -> SparkConfig:
    _load_runtime_imports()
    cfg = load_config(
        config_path=config,
        workdir=workdir,
        approval=cast(ApprovalMode, approval),
        model=model,
        provider=cast(ProviderName, provider),
        sandbox_mode=cast(SandboxMode, sandbox_mode),
    )
    # --add-dir 是本次进程的额外授权，不写回配置文件：命令行给的一次性
    # 权限不该悄悄变成持久设置。
    for extra in add_dirs or []:
        resolved = str(extra.expanduser().resolve())
        if resolved not in cfg.agent.extra_dirs:
            cfg.agent.extra_dirs.append(resolved)
    tier = _RUNTIME_ROUTE.get("tier", "auto")
    if tier in ("capability", "cost", "offline"):
        from spark.providers.routing import (
            apply_routed_provider,
            describe_decision,
            route_for_cli,
        )

        decision = route_for_cli(
            cfg,
            tier_class=tier,
            task_type=cast("TaskType", _RUNTIME_ROUTE.get("task", "code")),
            budget=cast("Budget", _RUNTIME_ROUTE.get("budget", "medium")),
        )
        apply_routed_provider(cfg, decision)
        cfg.routing_decision = dict(describe_decision(decision))
    if cfg.provider.name in {"openai_compat"} or is_native_provider(cfg):
        try:
            require_api_key(cfg)
        except ConfigError:
            typer.echo("◆ " + friendly_config_error(cfg), err=True)
    return cfg


def _launch_tui(
    *,
    workdir: Path,
    cfg: SparkConfig,
    store: SessionStore,
    session_id: str,
    initial_prompt: str | None,
) -> None:
    from spark.providers.factory import create_provider
    from spark.tui.app import SparkApp

    prov = create_provider(cfg)
    sandbox = WorkdirSandbox(workdir, cfg)
    from spark.tools.registry import ToolContext, ToolRegistry

    tool_ctx = ToolContext(sandbox=sandbox, config=cfg)
    registry = ToolRegistry(tool_ctx)
    bridge = asyncio.run(_boot_mcp(cfg, registry))
    tool_ctx.mcp_call = bridge.call
    tool_ctx.mcp_bridge = bridge
    bridge.set_sampling_handler(prov)
    from spark.tools import goal as goal_tools

    goal_tools.attach_bridges(store, lambda: session_id or "")
    SparkApp(
        workdir=workdir,
        cfg=cfg,
        store=store,
        session_id=session_id,
        provider=prov,
        registry=registry,
        bridge=bridge,
        initial_prompt=initial_prompt,
    ).run()


@app.callback()
def main(
    ctx: typer.Context,
    version_flag: bool = typer.Option(
        False,
        "--version",
        "-V",
        is_eager=True,
        help="输出当前语义化版本号后退出",
    ),
    workdir: Path = typer.Option(
        Path("."), "--workdir", dir_okay=True, file_okay=False
    ),
    approval: str | None = typer.Option(None, "--approval"),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
    sandbox_mode: str | None = typer.Option(
        None,
        "--sandbox-mode",
        help="sandbox-only | workspace | full-access | unrestricted",
    ),
    tier: str = typer.Option(
        "auto",
        "--tier",
        help="模型路由档：auto | capability | cost | offline（对全部子命令生效）",
    ),
    task: str = typer.Option(
        "code",
        "--task",
        help="路由任务类型：code | debug | chat | plan | review | edit | compact | title",
    ),
    budget: str = typer.Option(
        "medium",
        "--budget",
        help="路由预算：low | medium | high",
    ),
) -> None:
    if tier not in {"auto", "capability", "cost", "offline"}:
        typer.echo(f"invalid --tier: {tier}", err=True)
        raise typer.Exit(2)
    if task not in {"code", "debug", "chat", "plan", "review", "edit", "compact", "title", "offline"}:
        typer.echo(f"invalid --task: {task}", err=True)
        raise typer.Exit(2)
    if budget not in {"low", "medium", "high"}:
        typer.echo(f"invalid --budget: {budget}", err=True)
        raise typer.Exit(2)
    _RUNTIME_ROUTE.update({"tier": tier, "task": task, "budget": budget})
    if version_flag:
        typer.echo(version_line(_installed_version()))
        raise typer.Exit(0)
    if ctx.invoked_subcommand is not None:
        if ctx.invoked_subcommand != "version":
            _load_runtime_imports()
        return
    _load_runtime_imports()
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(
            workdir, config, approval, model, provider, sandbox_mode=sandbox_mode
        )
        prompt = " ".join(ctx.args).strip() or None
        store = _store()
        session_id = _resolve_session(store, workdir, cfg, None)
        _launch_tui(
            workdir=workdir,
            cfg=cfg,
            store=store,
            session_id=session_id,
            initial_prompt=prompt,
        )
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("exec", help="非交互执行单条 prompt，通过 stdout 输出最终结果（适合脚本与管道）")
def exec_cmd(
    prompt: str = typer.Argument(...),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: str | None = typer.Option(None, "--approval"),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
    sandbox_mode: str | None = typer.Option(
        None,
        "--sandbox-mode",
        help="sandbox-only | workspace | full-access | unrestricted",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="输出 NDJSON 事件流（每行一个 JSON 对象，最后一行为 {\"type\":\"final\"}）；错误行 type=turn_error",
    ),
) -> None:
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(
            workdir, config, approval, model, provider, sandbox_mode=sandbox_mode
        )
        if cfg.agent.approval == "suggest":
            raise ConfigError(
                "spark exec cannot collect approvals; use --approval auto-edit or full-auto"
            )
        store = _store()
        session_id = store.create_session(
            workdir, cfg.provider.model, title=prompt[:80]
        )
        asyncio.run(_exec_async(prompt, workdir, cfg, store, session_id, as_json=as_json))
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


async def _exec_async(
    prompt: str,
    workdir: Path,
    cfg: SparkConfig,
    store: SessionStore,
    session_id: str,
    *,
    as_json: bool = False,
) -> None:
    from spark.providers.factory import create_provider

    prov = create_provider(cfg)
    sandbox = WorkdirSandbox(workdir, cfg)
    from spark.tools.registry import ToolContext, ToolRegistry

    tool_ctx = ToolContext(sandbox=sandbox, config=cfg)
    registry = ToolRegistry(tool_ctx)
    bridge = await _boot_mcp(cfg, registry)
    tool_ctx.mcp_call = bridge.call
    tool_ctx.mcp_bridge = bridge
    bridge.set_sampling_handler(prov)
    from spark.tools import goal as goal_tools

    goal_tools.attach_bridges(store, lambda: session_id)
    # One-shot CLI: the process *is* the turn, so there is nothing to resume
    # after a crash. The default in-memory saver still backs in-process
    # approval interrupts without paying for a durable DB.
    loop = _agent_loop()(
        workdir=workdir,
        cfg=cfg,
        provider=prov,
        registry=registry,
        store=store,
        session_id=session_id,
        approver=None,
    )
    try:
        import json as _json

        from spark.slash import handle_test, parse_slash

        def _emit(payload: dict) -> None:
            typer.echo(_json.dumps(payload, ensure_ascii=False))

        slash = parse_slash(prompt)
        if slash is not None and slash.name == "test":
            # spark exec /test [目录]：脚本化跑测试，失败则把 traceback 回灌进 loop
            result = await asyncio.to_thread(handle_test, sandbox, slash.args)
            if as_json:
                _emit(
                    {
                        "type": "test",
                        "ok": bool(result.ok),
                        "summary": result.summary,
                    }
                )
            else:
                typer.echo(result.summary)
                if result.output:
                    typer.echo(result.output)
            if result.ok or not result.feedback:
                raise typer.Exit(0)
            final = ""
            async for event in loop.iter_turn(result.feedback):
                if as_json:
                    _emit(event.model_dump(mode="json", exclude_none=True))
                if event.type == "turn_end" and event.text:
                    final = event.text
                if event.type == "turn_error" and event.text:
                    if as_json:
                        _emit({"type": "turn_error", "text": event.text})
                    else:
                        typer.echo(event.text, err=True)
                    raise typer.Exit(1)
            if final:
                if as_json:
                    _emit({"type": "final", "text": final})
                else:
                    typer.echo(final)
            raise typer.Exit(1)

        # H5：自定义命令展开（.spark/commands/*.md）
        from spark.slash import expand_command

        try:
            expanded = expand_command(str(workdir), prompt)
        except Exception:
            expanded = None
        if as_json:
            final = ""
            async for event in loop.iter_turn(expanded or prompt):
                _emit(event.model_dump(mode="json", exclude_none=True))
                if event.type == "turn_end" and event.text:
                    final = event.text
                if event.type == "turn_error" and event.text:
                    _emit({"type": "turn_error", "text": event.text})
                    raise typer.Exit(1)
            _emit({"type": "final", "text": final})
        else:
            events = await loop.run(expanded or prompt)
            final = ""
            for event in events:
                if event.type == "turn_end" and event.text:
                    final = event.text
                if event.type == "turn_error" and event.text:
                    typer.echo(event.text, err=True)
                    raise typer.Exit(1)
            typer.echo(final)
    finally:
        await bridge.close()
        store.close()


async def _workflow_run_async(
    path: Path,
    workdir: Path,
    cfg: SparkConfig,
    store: SessionStore,
    session_id: str,
    *,
    max_parallel: int,
    as_json: bool,
) -> bool:
    import json as _json

    from spark.agents import discover_declared_agents
    from spark.providers.factory import create_provider
    from spark.tools import goal as goal_tools
    from spark.tools.registry import ToolContext, ToolRegistry
    from spark.workflow import AgentWorkflowRunner, load_workflow, run_workflow

    prov = create_provider(cfg)
    sandbox = WorkdirSandbox(workdir, cfg)
    tool_ctx = ToolContext(sandbox=sandbox, config=cfg)
    registry = ToolRegistry(tool_ctx)
    bridge = await _boot_mcp(cfg, registry)
    tool_ctx.mcp_call = bridge.call
    tool_ctx.mcp_bridge = bridge
    bridge.set_sampling_handler(prov)
    goal_tools.attach_bridges(store, lambda: session_id)
    loop = _agent_loop()(
        workdir=workdir,
        cfg=cfg,
        provider=prov,
        registry=registry,
        store=store,
        session_id=session_id,
        approver=None,
    )
    agents = {agent.name: agent for agent in discover_declared_agents(workdir)}

    def emit(payload: dict) -> None:
        if as_json:
            typer.echo(_json.dumps(payload, ensure_ascii=False))
        else:
            step = payload.get("step", "")
            kind = payload.get("type", "")
            if kind == "step_started":
                typer.echo(f"→ {step}", err=True)
            elif kind in {"step_completed", "step_failed"}:
                typer.echo(f"{'✓' if kind == 'step_completed' else '✗'} {step}" + (f": {payload.get('error', '')}" if payload.get("error") else ""), err=True)

    try:
        spec = load_workflow(path, workdir=workdir)
        runner = AgentWorkflowRunner(loop, spec, agents=agents, on_event=None)
        run = await run_workflow(
            spec, runner, workdir=workdir, max_parallel=max_parallel, on_event=emit
        )
        if as_json:
            typer.echo(_json.dumps({
                "type": "workflow_end", "ok": run.ok,
                "statuses": run.statuses, "errors": run.errors,
            }, ensure_ascii=False))
        else:
            typer.echo(f"workflow {spec.name}: {'ok' if run.ok else 'failed'}")
            for step in spec.steps:
                line = f"  {step.id}: {run.statuses.get(step.id, 'pending')}"
                if step.id in run.errors:
                    line += f" ({run.errors[step.id]})"
                typer.echo(line)
        return run.ok
    finally:
        await bridge.close()
        store.close()


@app.command("route", help="预览模型路由决策（不发起模型请求）")
def route_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Path | None = typer.Option(None, "--config"),
    tier: str | None = typer.Option(None, "--tier", help="auto | capability | cost | offline"),
    task: str | None = typer.Option(None, "--task"),
    budget: str | None = typer.Option(None, "--budget"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show which tier would be chosen and why, including degradation chain."""
    import json

    from spark.providers.routing import describe_decision, route_for_cli

    try:
        cfg = _common_cfg(workdir.resolve(), config, None, None, None)
        decision = route_for_cli(
            cfg,
            tier_class=tier or _RUNTIME_ROUTE.get("tier", "auto"),
            task_type=cast("TaskType", task or _RUNTIME_ROUTE.get("task", "code")),
            budget=cast("Budget", budget or _RUNTIME_ROUTE.get("budget", "medium")),
        )
        payload = dict(describe_decision(decision))
        if as_json:
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        typer.echo(f"tier      {payload['class']} / {payload['tier']}")
        typer.echo(f"model     {payload['model']}")
        typer.echo(f"provider  {payload['provider']}")
        typer.echo(f"reason    {payload['reason']}")
        degraded = payload.get("degraded_from")
        if isinstance(degraded, list) and degraded:
            typer.echo(f"degraded  {', '.join(str(x) for x in degraded)}")
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("providers", help="列出内置 provider 预设（原生 + 国产兼容入口）")
def providers_cmd(
    native_only: bool = typer.Option(False, "--native", help="只显示原生 provider"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    import json

    from spark.providers.presets import presets_payload

    rows = presets_payload(native_only=native_only)
    if as_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for row in rows:
        marker = "native" if row.get("native") else "compat"
        typer.echo(
            f"{row['id']:<10} {marker:<6} {row['provider']:<14} "
            f"{row['model']}  ({row['api_key_env'] or 'no key'})"
        )


@app.command("wiki", help="从源码结构生成可追溯的本地项目文档（tree-sitter，离线可重复）")
def wiki_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    refresh: bool = typer.Option(False, "--refresh", help="忽略缓存，全量重建"),
    summarize: bool = typer.Option(False, "--summarize", help="额外用模型生成每个模块的一句话摘要（需要真实 provider）"),
    max_files: int = typer.Option(240, "--max-files", help="最多索引的文件数"),
    out_dir: str = typer.Option(".spark/wiki", "--out-dir", help="输出目录（相对 workdir）"),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """生成 ``<workdir>/.spark/wiki/``：INDEX.md + 每个模块一份符号表。

    结构事实全部来自 tree-sitter 静态解析，不需要网络、不需要模型，
    产物可 diff；``--summarize`` 才会调用模型补一句摘要。
    """
    from spark.wiki import WikiConfig, build_wiki

    workdir = workdir.resolve()
    if not workdir.is_dir():
        typer.echo(f"not a directory: {workdir}", err=True)
        raise typer.Exit(2)

    summarizer = None
    if summarize:
        cfg = _common_cfg(workdir, config, None, model, provider)
        if cfg.provider.name == "mock":
            typer.echo("--summarize needs a real model; pass --provider/--model", err=True)
            raise typer.Exit(2)
        summarizer = _make_wiki_summarizer(cfg)

    wiki_cfg = WikiConfig(max_files=max(1, max_files), summarize=summarize, out_dir=out_dir)
    stats = build_wiki(workdir, wiki_cfg, summarizer=summarizer, refresh=refresh)
    typer.echo(
        f"◆ Repo Wiki · {stats['modules']} 文件 / {stats['symbols']} 符号 "
        f"（新增或更新 {stats['built']}，命中缓存 {stats['cached']}）"
    )
    typer.echo(f"  {stats['index']}")


def _make_wiki_summarizer(cfg):
    """构造 ``(path, symbols) -> str``；失败由 build_wiki 吞掉并降级。"""
    import asyncio

    from spark.providers.factory import create_provider

    provider = create_provider(cfg)

    def summarize(path: str, symbols) -> str:
        names = ", ".join(s.name for s in symbols[:25])
        prompt = (
            "用一句中文（不超过 40 字）概括这个文件在项目中的职责，不要写代码，不要客套。\n"
            f"文件：{path}\n主要符号：{names}\n"
        )

        async def run() -> str:
            chunks: list[str] = []
            async for chunk in provider.stream(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            ):
                piece = getattr(chunk, "text", None) or ""
                if piece:
                    chunks.append(piece)
            return "".join(chunks).strip()

        try:
            text = asyncio.run(run())
        except Exception:
            return ""
        return text.splitlines()[0][:200] if text else ""

    return summarize


@app.command("review", help="AI 审查代码变更（默认未提交 diff；--commit/--base 可选范围），只读不改动")
def review_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    commit: str | None = typer.Option(None, "--commit", help="审查指定 commit/branch（git show）"),
    base: str | None = typer.Option(None, "--base", help="审查 base...HEAD 的全部提交"),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
    as_json: bool = typer.Option(False, "--json", help="输出机器可读 JSON（summary + findings）"),
) -> None:
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(workdir, config, None, model, provider)
        if cfg.provider.name == "mock":
            raise ConfigError(
                "spark review needs a real model; pass --provider openai_compat and --base-url"
            )
        key = require_api_key(cfg)
        if not key:
            raise ConfigError("spark review requires an API key")
        from spark.review import (
            ReviewError,
            ReviewTarget,
            build_review_prompt,
            collect_diff,
            diff_stats,
            merge_reviews,
            parse_review,
            render_review,
            split_diff,
        )

        if commit:
            target = ReviewTarget(mode="commit", commit=commit)
        elif base:
            target = ReviewTarget(mode="base", base=base)
        else:
            target = ReviewTarget(mode="staged")
        try:
            diff = collect_diff(workdir, target)
        except ReviewError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        stats = diff_stats(workdir, target)

        # 审查轮强制只读：无论用户配置如何，review 都不允许写/执行。
        cfg.agent.approval = "plan-only"
        cfg.agent.sandbox_mode = "sandbox-only"
        store = _store()
        session_id = store.create_session(workdir, cfg.provider.model, title="review")
        chunks = split_diff(diff)
        if len(chunks) > 1:
            typer.echo(f"diff 过大，分 {len(chunks)} 片审查…")
        results = []
        for index, chunk in enumerate(chunks, start=1):
            prompt = build_review_prompt(
                chunk, part=(index, len(chunks)) if len(chunks) > 1 else None
            )
            review = asyncio.run(
                _review_async(
                    prompt,
                    workdir,
                    cfg,
                    store,
                    session_id,
                    close_store=(index == len(chunks)),
                )
            )
            results.append(parse_review(review))
        result = merge_reviews(results) if len(results) > 1 else results[0]
        result.stats = stats
        if as_json:
            import json as _json

            typer.echo(
                _json.dumps(
                    {
                        "summary": result.summary,
                        "findings": [
                            {
                                "severity": f.severity,
                                "file": f.file,
                                "line": f.line,
                                "comment": f.comment,
                            }
                            for f in result.findings
                        ],
                        "stats": stats,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            header = f"◆ Spark Review（{target.mode}）"
            if stats:
                header += f" — {stats.get('files', 0)} 文件 +{stats.get('added', 0)}/-{stats.get('removed', 0)}"
            typer.echo(render_review(result, header))
        high = any(f.severity == "high" for f in result.findings)
        raise typer.Exit(2 if high else 0)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


async def _review_async(
    prompt: str,
    workdir: Path,
    cfg: SparkConfig,
    store: SessionStore,
    session_id: str,
    *,
    close_store: bool = True,
) -> str:
    """Run one read-only review turn.

    ``close_store=False`` when the caller loops over diff chunks: the store is
    shared, and closing it after the first chunk made the second one fail with
    "Cannot operate on a closed database".
    """
    from spark.providers.factory import create_provider

    prov = create_provider(cfg)
    sandbox = WorkdirSandbox(workdir, cfg)
    from spark.tools.registry import ToolContext, ToolRegistry

    tool_ctx = ToolContext(sandbox=sandbox, config=cfg)
    registry = ToolRegistry(tool_ctx)
    bridge = await _boot_mcp(cfg, registry)
    tool_ctx.mcp_call = bridge.call
    tool_ctx.mcp_bridge = bridge
    bridge.set_sampling_handler(prov)
    from spark.tools import goal as goal_tools

    goal_tools.attach_bridges(store, lambda: session_id)
    loop = _agent_loop()(
        workdir=workdir,
        cfg=cfg,
        provider=prov,
        registry=registry,
        store=store,
        session_id=session_id,
        approver=None,
    )
    try:
        final = ""
        async for event in loop.iter_turn(prompt):
            if event.type == "turn_end" and event.text:
                final = event.text
            if event.type == "turn_error" and event.text:
                raise ConfigError(f"review turn failed: {event.text}")
        return final
    finally:
        await bridge.close()
        if close_store:
            store.close()


@app.command("update", help="检查并安装新版本（校验 SHA256；--check 只看不装）")
def update_cmd(
    check: bool = typer.Option(False, "--check", help="只检查是否有新版本，不下载"),
    force: bool = typer.Option(False, "--force", help="即使已是新版也重新安装"),
    api_url: str | None = typer.Option(None, "--api-url", help="自定义 Release API（内网镜像）"),
) -> None:
    from spark import updater

    try:
        release = updater.fetch_latest(api_url or updater.RELEASE_API)
    except updater.UpdateError as exc:
        typer.echo(f"检查更新失败：{exc}", err=True)
        raise typer.Exit(1) from exc
    local = _installed_version()
    typer.echo(f"当前版本：{local}")
    typer.echo(f"最新版本：{release.version}")
    newer = updater.is_newer(release.version, local)
    if not newer and not force:
        typer.echo("已是最新版本。")
        return
    if check:
        typer.echo(f"有新版本可用：{updater.release_page_url(release.tag)}")
        return
    if getattr(sys, "frozen", False) is False:
        typer.echo(
            "当前是源码/pip 安装，请用 `pip install -U spark-agent` 升级；"
            "打包版可直接 `spark update`。"
        )
        raise typer.Exit(2)
    try:
        asset = updater.select_asset(release)
        sums_asset = next(
            (a for a in release.assets if a["name"].lower() == "sha256sums"), None
        )
        if sums_asset is None:
            raise updater.UpdateError("该 Release 未提供 SHA256SUMS，拒绝安装未校验的产物")
        with tempfile.TemporaryDirectory(prefix="spark-update-") as tmp:
            tmpdir = Path(tmp)
            sums_path = updater.download(sums_asset["url"], tmpdir / "SHA256SUMS")
            sums = updater.parse_sha256sums(sums_path.read_text(encoding="utf-8"))
            archive = updater.download(asset["url"], tmpdir / asset["name"])
            updater.verify(archive, sums, asset["name"])
            backup = updater.install_archive(archive, updater.current_executable())
        typer.echo(f"已更新到 {release.version}（旧版本备份：{backup}）")
        typer.echo("重新启动 spark 以使用新版本。")
    except updater.UpdateError as exc:
        typer.echo(f"更新失败：{exc}", err=True)
        raise typer.Exit(1) from exc


@app.command("web")
def web_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: str | None = typer.Option(
        None,
        "--approval",
        help="full-auto | auto-edit | approve-plan | suggest | plan-only | manual（默认逐项询问，确认后再执行）",
    ),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
    sandbox_mode: str | None = typer.Option(
        None,
        "--sandbox-mode",
        help="sandbox-only | workspace | full-access | unrestricted",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="默认仅本机可访问；改为 0.0.0.0 会把控制面暴露到局域网/公网，风险自负",
    ),
    port: int | None = typer.Option(
        None, "--port", help="监听端口；省略时从 8000 起自动选择可用端口"
    ),
    add_dir: list[Path] = typer.Option(
        None,
        "--add-dir",
        help="额外可读写的目录（可重复）；只放行文件读写，不放宽 shell 越界",
    ),
) -> None:
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(
            workdir, config, approval, model, provider,
            sandbox_mode=sandbox_mode, add_dirs=add_dir,
        )
        store = _store()
        from spark.web.server import build_web_server
        from spark.web.utils import _port_in_use, pick_free_port

        if port is None:
            port = pick_free_port(host, 8000)
        elif _port_in_use(host, port):
            typer.echo(
                f"端口 {port} 已被占用；省略 --port 可自动选择可用端口。", err=True
            )
            raise typer.Exit(1)
        server, state = build_web_server(
            workdir=workdir, cfg=cfg, store=store, host=host, port=port
        )
        typer.echo(f"◆ Spark Web  →  http://{host}:{port}")
        if host not in {"127.0.0.1", "localhost", "::1"}:
            typer.echo(
                f"◆ 远程访问令牌（请求头 X-Spark-Token）: {state.control_token}"
            )
        try:
            server.serve_forever()
        finally:
            store.close()
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("mcp-serve")
def mcp_serve_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: str | None = typer.Option(
        None,
        "--approval",
        help="full-auto | auto-edit | approve-plan | suggest | plan-only | manual（默认逐项询问，确认后再执行）",
    ),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
    sandbox_mode: str | None = typer.Option(
        None,
        "--sandbox-mode",
        help="sandbox-only | workspace | full-access | unrestricted",
    ),
    port: int | None = typer.Option(
        None,
        "--port",
        help="提供时以 streamable HTTP 模式在 127.0.0.1:<port> 运行；否则 stdio",
    ),
) -> None:
    """把 spark 内建工具反向暴露为 MCP server（stdio / streamable HTTP）。"""
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(
            workdir, config, approval, model, provider, sandbox_mode=sandbox_mode
        )
        from spark.mcp_expose import build_mcp_server

        server = build_mcp_server(str(workdir), cfg)
        if port:
            import uvicorn

            app = server.streamable_http_app()
            uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
        else:
            server.run(transport="stdio")
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("acp", help="以 ACP（Agent Client Protocol）agent 身份在 stdio 上运行，供 Zed 等 IDE 拉起")
def acp_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: str | None = typer.Option(
        None,
        "--approval",
        help="full-auto | auto-edit | approve-plan | suggest | plan-only | manual",
    ),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
    sandbox_mode: str | None = typer.Option(
        None,
        "--sandbox-mode",
        help="sandbox-only | workspace | full-access | unrestricted",
    ),
    tier: str = typer.Option(
        "auto",
        "--tier",
        help="模型路由档：auto | capability | cost | offline（对全部子命令生效）",
    ),
    task: str = typer.Option(
        "code",
        "--task",
        help="路由任务类型：code | debug | chat | plan | review | edit | compact | title",
    ),
    budget: str = typer.Option(
        "medium",
        "--budget",
        help="路由预算：low | medium | high",
    ),
) -> None:
    """stdio 上跑 ACP JSON-RPC（stdout 是协议通道，日志只走 stderr）。"""
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(
            workdir, config, approval, model, provider, sandbox_mode=sandbox_mode
        )
        from spark.acp_server import run_stdio

        run_stdio(workdir, cfg)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("desktop")
def desktop_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: str | None = typer.Option(
        None,
        "--approval",
        help="full-auto | auto-edit | approve-plan | suggest | plan-only | manual（默认逐项询问，确认后再执行）",
    ),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
    sandbox_mode: str | None = typer.Option(
        None,
        "--sandbox-mode",
        help="sandbox-only | workspace | full-access | unrestricted",
    ),
    port: int | None = typer.Option(
        None, "--port", help="本机服务监听端口；省略时自动选择可用端口"
    ),
    width: int = typer.Option(1280, "--width", help="窗口宽度"),
    height: int = typer.Option(832, "--height", help="窗口高度"),
) -> None:
    """在原生窗口中打开 Spark（固定绑定 127.0.0.1；要暴露局域网请用 spark web）。"""
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(
            workdir, config, approval, model, provider, sandbox_mode=sandbox_mode
        )
        store = _store()
        from spark.desktop import run_desktop

        code = run_desktop(
            workdir=workdir, cfg=cfg, store=store, port=port, width=width, height=height
        )
        raise typer.Exit(code)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("test")
def test_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
    base_url: str | None = typer.Option(None, "--base-url"),
    api_key: str | None = typer.Option(
        None, "--api-key", help="User API key for the custom endpoint"
    ),
    rounds: int = typer.Option(2, "--rounds"),
) -> None:
    """Probe a custom OpenAI-compatible model and print real response text."""
    try:
        workdir = workdir.resolve()
        cfg = load_config(
            config_path=config,
            workdir=workdir,
            model=model,
            provider=cast(ProviderName, provider),
        )
        if base_url:
            cfg.provider.base_url = base_url
            cfg.provider.name = "openai_compat"
        if api_key:
            cfg.provider.api_key = api_key
        if cfg.provider.name == "mock":
            raise ConfigError(
                "spark test needs a real model; pass --provider openai_compat and --base-url"
            )
        key = require_api_key(cfg)
        result = asyncio.run(
            probe_provider(
                base_url=cfg.provider.base_url,
                api_key=key or "",
                model=cfg.provider.model,
                rounds=rounds,
            )
        )
        if result.ok:
            typer.echo("OK")
            typer.echo(f"model: {result.model}")
            typer.echo(f"base_url: {result.base_url}")
            typer.echo(f"latency_ms: {result.latency_ms}")
            typer.echo(f"rounds: {result.rounds}")
            typer.echo("content:")
            typer.echo(result.content)
            typer.echo("stream_content:")
            typer.echo(result.stream_content)
            raise typer.Exit(0)
        typer.echo("FAIL", err=True)
        typer.echo(result.error or "probe failed", err=True)
        if result.content:
            typer.echo("partial content:", err=True)
            typer.echo(result.content, err=True)
        raise typer.Exit(1)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("sessions", help="列出所有会话，按更新时间倒序")
def sessions_cmd() -> None:
    store = _store()
    rows = store.list_sessions()
    store.close()
    if not rows:
        typer.echo("No sessions.")
        return
    for row in rows:
        typer.echo(
            f"{row['id']}\t{row['updated_at']}\t{row['workdir']}\t{row['title']}"
        )


@app.command("resume")
def resume_cmd(
    session_id: str = typer.Argument(...),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: str | None = typer.Option(None, "--approval"),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(workdir, config, approval, model, provider)
        store = _store()
        session_id = _resolve_session(store, workdir, cfg, session_id)
        row = store.get_session(session_id)
        if row:
            workdir = Path(row["workdir"])
        _launch_tui(
            workdir=workdir,
            cfg=cfg,
            store=store,
            session_id=session_id,
            initial_prompt=None,
        )
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("version", help="输出当前语义化版本号")
def version_cmd() -> None:
    """Show the installed Spark version."""
    typer.echo(version_line(_installed_version()))


def _installed_version() -> str:
    """Resolve the distribution version once, shared by --version and `version`."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("spark-agent")
    except PackageNotFoundError:
        from spark import __version__

        return __version__ or "development build"


@app.command("models", help="查询开放模型目录（models.dev）——搜索模型/价格/上下文长度")
def models_cmd(
    query: str = typer.Argument("", help="关键词（供应商或模型名，留空列出少量热门）"),
    limit: int = typer.Option(20, "--limit", "-n", help="最多返回条数"),
    refresh: bool = typer.Option(False, "--refresh", help="强制刷新目录缓存（默认 24h）"),
    probe: bool = typer.Option(
        False, "--probe", help="不查公共目录，改为探测当前配置的端点（列出并逐个试活）"
    ),
    config: Path | None = typer.Option(None, "--config"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
) -> None:
    from spark import model_catalog
    from spark.config import default_home

    if probe:
        _probe_configured_endpoint(config, workdir)
        return

    catalog = model_catalog.fetch_catalog(default_home(), force=refresh)
    rows = model_catalog.search(catalog, query, limit=limit)
    if not rows:
        typer.echo("（无匹配结果——网络不可达时会退回内置精简目录）")
        return
    typer.echo(f"{'供应商':<16} {'模型':<34} {'上下文':<10} 价格(USD/1M in/out)")
    for r in rows:
        cost = r.get("cost") or {}
        ctx = r.get("context")
        ctx_s = f"{ctx // 1000}k" if isinstance(ctx, int) else "-"
        try:
            price_s = f"{float(cost.get('input') or 0):g} / {float(cost.get('output') or 0):g}"
        except (TypeError, ValueError):
            price_s = "-"
        typer.echo(f"{r['provider'][:16]:<16} {r['model'][:34]:<34} {ctx_s:<10} {price_s}")


def _probe_configured_endpoint(config: Path | None, workdir: Path) -> None:
    """List the configured endpoint's models and report which ones answer.

    `spark models` alone reads a public catalogue, which says nothing about
    whether *your* key can serve a given model — the failure mode this project
    hit repeatedly (listed but 404, or listed but out of quota).
    """
    from spark.config import require_api_key
    from spark.providers.probe import list_models, probe_provider

    try:
        resolved = workdir.resolve()
        cfg = read_config(config, resolved)
        key = cfg.provider.api_key or require_api_key(cfg) or ""
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc

    if cfg.provider.name == "mock":
        typer.echo("当前是 mock 提供方，没有可探测的端点")
        return

    ids = asyncio.run(list_models(base_url=cfg.provider.base_url, api_key=key))
    if not ids:
        typer.echo(f"端点未返回模型列表：{cfg.provider.base_url}", err=True)
        typer.echo("检查 base_url 是否以 /v1 结尾、密钥是否有效", err=True)
        raise typer.Exit(1)

    typer.echo(f"端点：{cfg.provider.base_url}")
    typer.echo(f"{'模型':<34} {'结果':<10} 首包")
    ok_count = 0
    for mid in ids:
        result = asyncio.run(
            probe_provider(
                base_url=cfg.provider.base_url, api_key=key, model=mid, rounds=1
            )
        )
        if result.ok:
            ok_count += 1
            typer.echo(f"{mid[:34]:<34} {'可用':<10} {result.latency_ms} ms")
        else:
            reason = (result.error or "unknown")[:60].replace(chr(10), " ")
            typer.echo(f"{mid[:34]:<34} {'不可用':<10} {reason}")
    typer.echo("")
    typer.echo(f"{ok_count}/{len(ids)} 个模型可用")


@app.command("replay", help="查看 turn 级重放记录（上下文合同 spark.replay/1）")
def replay_cmd(
    session: str = typer.Argument("", help="会话 id（留空 = 列出全部有记录的会话）"),
    turn: int = typer.Option(-1, "--turn", "-t", help="查看第 N 个 turn（0 起）"),
    as_json: bool = typer.Option(False, "--json", help="输出原始 JSON 记录"),
) -> None:
    import json as _json

    from spark.core.replay import list_replay_sessions, load_replays

    if not session:
        sessions = list_replay_sessions()
        if not sessions:
            typer.echo("（还没有任何重放记录——先对话一轮）")
            raise typer.Exit(0)
        typer.echo(f"{'会话':<16} {'更新时间':<12} 大小")
        for row in sessions[:30]:
            import datetime as _dt

            stamp = _dt.datetime.fromtimestamp(row["updated_at"]).strftime("%m-%d %H:%M")
            typer.echo(f"{row['session_id']:<16} {stamp:<12} {row['bytes']}")
        typer.echo("\n查看明细：spark replay <会话id> [-t N]")
        return

    records = load_replays(session)
    if not records:
        typer.echo(f"（会话 {session} 没有重放记录）", err=True)
        raise typer.Exit(1)
    if turn >= 0:
        if turn >= len(records):
            typer.echo(f"（turn 越界：共 {len(records)} 条，0..{len(records) - 1}）", err=True)
            raise typer.Exit(1)
        record = records[turn]
        if as_json:
            typer.echo(_json.dumps(record, ensure_ascii=False, indent=2))
            return
        t = record["turn"]
        status = t.get("status", "?")
        typer.echo(f"◆ turn #{t.get('index')}  status={status}  {t.get('duration_ms')}ms  model={record.get('model')}")
        usage = t.get("usage") or {}
        typer.echo(
            f"  tokens: in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}"
            f" reasoning={usage.get('reasoning_tokens', 0)} ({usage.get('source')})"
        )
        typer.echo(f"  prompt: {(t.get('prompt') or '')[:120]}")
        if t.get("compaction"):
            typer.echo(f"  compaction: {t['compaction']}")
        for call in t.get("tool_calls") or []:
            mark = "ok" if call.get("ok") else ("fail" if call.get("ok") is False else "??")
            typer.echo(f"  [{mark}] {call.get('name')} {_json.dumps(call.get('arguments') or {}, ensure_ascii=False)[:100]}")
        for ap in t.get("approvals") or []:
            typer.echo(f"  审批 {ap.get('tool')}: {ap.get('decision')}")
        text = (t.get("text") or "").strip()
        if text:
            typer.echo("  ── 回复 ──")
            for line in text.splitlines()[:20]:
                typer.echo(f"  {line}")
        return
    typer.echo(f"{'#':<4} {'状态':<10} {'耗时ms':<8} {'工具':<6} {'prompt'}")
    for i, record in enumerate(records):
        t = record.get("turn") or {}
        typer.echo(
            f"{i:<4} {str(t.get('status', '?')):<10} {str(t.get('duration_ms', 0)):<8} "
            f"{len(t.get('tool_calls') or []):<6} {(t.get('prompt') or '')[:60]}"
        )
    typer.echo("\n查看明细：spark replay <会话id> -t <N>（--json 输出原始记录）")


@app.command("context-profile", help="上下文火焰图——这轮请求的上下文都被谁吃了")
def context_profile_cmd(
    session: str = typer.Option("", "--session", "-s", help="会话 id（留空 = 最近一个）"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    from spark.core.context_profile import profile_context
    from spark.store import SessionStore

    cfg = _common_cfg(workdir, config, None, None, None)
    store = SessionStore(default_home() / "sessions.db")
    sid = session
    if not sid:
        rows = store.list_sessions()
        if not rows:
            typer.echo("（没有任何会话——先跑一次对话）", err=True)
            raise typer.Exit(1)
        sid = rows[0]["id"]
        typer.echo(f"（最近会话 {sid}）")
    profile = profile_context(store, sid, workdir.resolve(), cfg)
    typer.echo(profile.render())


profile_app = typer.Typer(help="审批/行为档案（H4）——approval+工具白名单+system prompt 组合")
app.add_typer(profile_app, name="profile")


@profile_app.command("list")
def profile_list_cmd(
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """列出全部审批档案，标记当前激活项。"""
    cfg = read_config(config, Path(".").resolve())
    if not cfg.approval_profiles:
        typer.echo(
            "（尚无档案——在 config.toml 添加 [[approval_profiles]]，"
            "字段：id/name/approval/tools/system_prompt/model_profile_id）"
        )
        return
    for p in cfg.approval_profiles:
        mark = "●" if p.id == cfg.active_approval_profile_id else " "
        tools = ",".join(p.tools) if p.tools else "all"
        typer.echo(
            f" {mark} {p.id:<18} {p.name:<16} {p.approval:<12} tools={tools}"
        )


@profile_app.command("use")
def profile_use_cmd(
    profile: str = typer.Argument(..., help="档案 id 或名称"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """激活一个审批档案（联动其 model_profile_id 指定的模型档案）。"""
    cfg = read_config(config, Path(".").resolve())
    if not cfg.approval_profiles:
        typer.echo("（尚无档案可切换）", err=True)
        raise typer.Exit(1)
    hit = next(
        (p for p in cfg.approval_profiles if p.id == profile or p.name == profile),
        None,
    )
    if hit is None:
        typer.echo(f"档案不存在: {profile}（用 `spark profile list` 查看）", err=True)
        raise typer.Exit(1)
    cfg.active_approval_profile_id = hit.id
    if hit.model_profile_id:
        mp = next(
            (m for m in cfg.model_profiles if m.id == hit.model_profile_id), None
        )
        if mp is not None:
            activate_model_profile(cfg, mp)
    path = save_config(cfg, config)
    typer.echo(
        f"● 档案已激活: {hit.name or hit.id} "
        f"(approval={hit.approval}, tools={'all' if not hit.tools else ','.join(hit.tools)})"
    )
    typer.echo(f"  配置已写入 {path}")


agent_app = typer.Typer(help="大脑档案管理——一键切换模型/供应商组合（P2：agent profiles）")
app.add_typer(agent_app, name="agent")


@agent_app.command("list")
def agent_list_cmd(
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """列出全部大脑档案，标记当前激活项。"""
    cfg = read_config(config, Path(".").resolve())
    if not cfg.model_profiles:
        typer.echo("（尚无档案——在 Web 设置中添加，或编辑 config.toml 的 [[model_profiles]]）")
        return
    for p in cfg.model_profiles:
        mark = "●" if p.id == cfg.active_profile_id else " "
        typer.echo(f" {mark} {p.id:<18} {p.name:<16} {p.provider:<12} {p.model}")


@agent_app.command("use")
def agent_use_cmd(
    profile: str = typer.Argument(..., help="档案 id 或名称"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """一键切换大脑（写回 active_profile_id 并持久化）。"""
    cfg = read_config(config, Path(".").resolve())
    if not cfg.model_profiles:
        typer.echo("（尚无档案可切换）", err=True)
        raise typer.Exit(1)
    hit = next(
        (p for p in cfg.model_profiles if p.id == profile or p.name == profile),
        None,
    )
    if hit is None:
        typer.echo(f"档案不存在: {profile}（用 `spark agent list` 查看）", err=True)
        raise typer.Exit(1)
    activate_model_profile(cfg, hit)
    path = save_config(cfg, config)
    typer.echo(f"● 大脑已切换: {hit.name or hit.id} ({hit.provider}/{hit.model})")
    typer.echo(f"  配置已写入 {path}")


@app.command("init")
def init_cmd(
    config: Path | None = typer.Option(None, "--config", help="写入的配置文件路径"),
    base_url: str | None = typer.Option(
        None, "--base-url", help="模型接口地址，例如 https://api.agnes-ai.cn/v1"
    ),
    model: str | None = typer.Option(None, "--model", help="模型名"),
    api_key: str | None = typer.Option(
        None, "--api-key", help="API Key（不会回显）"
    ),
    provider: str | None = typer.Option(
        None, "--provider", help="openai_compat | ollama | mock"
    ),
    approval: str | None = typer.Option(
        None, "--approval", help="full-auto | auto-edit | approve-plan | suggest | plan-only | manual"
    ),
    sandbox_mode: str | None = typer.Option(
        None,
        "--sandbox-mode",
        help="sandbox-only | workspace | full-access | unrestricted",
    ),
    probe: bool = typer.Option(True, "--probe/--no-probe", help="保存后立即测试连通性"),
) -> None:
    """Interactive setup: write provider credentials and verify them."""
    typer.echo(banner("首次配置向导"))
    try:
        workdir = Path.cwd()
        path = ensure_skeleton(config)
        cfg = read_config(config, workdir)
        changed = apply_setup(
            cfg,
            base_url=base_url,
            model=model,
            api_key=api_key,
            provider=provider,
            approval=approval,
            sandbox_mode=sandbox_mode,
        )
        if not changed:
            if not sys.stdin.isatty():
                typer.echo(
                    "未提供任何配置项。使用 --base-url / --model / --api-key 指定，或直接编辑："
                )
                typer.echo(str(path))
                raise typer.Exit(2)
            _prompt_setup(cfg)
            changed = ["interactive"]
        written = save(cfg, config)
        typer.echo(f"已写入配置：{written}")
        for name in changed:
            typer.echo(f"  更新 {name}")
        typer.echo(
            f"  API Key：{'已设置（已隐藏）' if cfg.provider.api_key else '未设置'}"
        )
        if not probe:
            return
        typer.echo("正在测试连通性…")
        key = cfg.provider.api_key or env_api_key(cfg) or ""
        result = asyncio.run(
            probe_provider(
                base_url=cfg.provider.base_url,
                api_key=key,
                model=cfg.provider.model,
                rounds=1,
            )
        )
        if result.ok:
            typer.echo(f"连通性正常（{result.latency_ms} ms）")
        else:
            typer.echo(f"连通性失败：{result.error or '未知错误'}", err=True)
            typer.echo("可用 spark doctor 查看完整诊断。", err=True)
            raise typer.Exit(1)
    except SetupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def _prompt_setup(cfg: SparkConfig) -> None:
    import getpass

    typer.echo("Spark 首次配置（直接回车跳过该项）")
    url = typer.prompt("模型接口地址", default=cfg.provider.base_url)
    cfg.provider.base_url = url.rstrip("/")
    cfg.provider.model = typer.prompt("模型名", default=cfg.provider.model)
    key = getpass.getpass("API Key（输入不回显，留空则不设置）: ").strip()
    if key:
        cfg.provider.api_key = key


def _perm_ok() -> bool:
    """配置文件权限是否达标：POSIX = 0600；Windows = ACL 收紧到仅当前用户。"""
    from spark.config import default_config_path, win_acl_restricted

    f = default_config_path()  # 动态：遵守 SPARK_HOME
    if sys.platform == "win32":
        return f.exists() and win_acl_restricted(f)
    import stat

    try:
        return stat.S_IMODE(f.stat().st_mode) == 0o600
    except OSError:
        return False


@app.command("doctor")
def doctor_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Path | None = typer.Option(None, "--config"),
    probe: bool = typer.Option(True, "--probe/--no-probe", help="实际调用一次模型验证"),
    fix: bool = typer.Option(False, "--fix", help="自动修复可安全修复的问题（建目录/重建配置/备份损坏配置）"),
    backup: bool = typer.Option(
        False, "--backup", help="备份会话数据库到 ~/.spark/backups/（VACUUM INTO，可在线执行）"
    ),
    as_json: bool = typer.Option(False, "--json", help="输出机器可读 JSON"),
    check_updates: bool = typer.Option(
        True, "--check-updates/--no-check-updates", help="检查是否有新版本（24h 缓存）"
    ),
) -> None:
    """Diagnose configuration and print actionable fixes."""
    typer.echo(banner("配置体检"))
    try:
        resolved = workdir.resolve()
    except OSError as exc:
        typer.echo(f"工作目录无效：{exc}", err=True)
        raise typer.Exit(1) from exc
    if fix:
        from spark import selfheal

        try:
            repair_cfg = read_config(config, resolved)
        except SparkError:
            # The config itself is unreadable — repair still needs a config
            # object, and rebuilding the file is exactly what it should do.
            repair_cfg = SparkConfig()
        report = selfheal.repair(
            repair_cfg,
            workdir=resolved,
            config_path=config or (default_home() / "config.toml"),
        )
        for action in report["actions"]:
            mark = "已修复" if action["applied"] else "未修复"
            detail = f"（{action['detail']}）" if action.get("detail") else ""
            error = f"：{action['error']}" if action.get("error") else ""
            typer.echo(f"  {mark} {action['kind']} {detail}{error}")
        if not any(a["applied"] for a in report["actions"]):
            typer.echo("  没有需要自动修复的项")
    try:
        cfg = read_config(config, resolved)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    rows = check_health(
        cfg, workdir=resolved, config_path=config or (default_home() / "config.toml")
    )
    if (
        probe
        and cfg.provider.name != "mock"
        and (cfg.provider.api_key or env_api_key(cfg))
    ):
        key = cfg.provider.api_key or env_api_key(cfg)
        result = asyncio.run(
            probe_provider(
                base_url=cfg.provider.base_url,
                api_key=key,
                model=cfg.provider.model,
                rounds=1,
            )
        )
        if result.ok:
            rows.append(("ok", f"模型连通正常（{result.latency_ms} ms）", ""))
        else:
            rows.append(
                (
                    "bad",
                    f"模型连通失败：{result.error or '未知错误'}",
                    "核对 base_url / 模型名 / 密钥，或运行 spark init 重新配置",
                )
            )
    if backup:
        import time as _time

        from spark.store import SessionStore

        db = default_home() / "sessions.db"
        if not db.exists():
            typer.echo("  跳过：还没有会话数据库", err=True)
        else:
            stamp = _time.strftime("%Y%m%d-%H%M%S")
            target = default_home() / "backups" / f"sessions-{stamp}.db"
            store = SessionStore(db)
            try:
                out = store.backup_to(target)
                typer.echo(
                    f"  已备份：{out}（{_human_size(out.stat().st_size)}，schema v{store.schema_version()}）"
                )
            finally:
                store.close()

    if check_updates:
        from spark import updater

        try:
            latest = updater.check_cached(default_home())
        except Exception:  # aqg: top-level boundary — 更新检查永不影响 doctor 结果
            latest = None
        if latest:
            rows.append(
                (
                    "warn",
                    f"有新版本可用：{latest}（当前 {_installed_version()}）",
                    "运行 spark update 升级（打包版），或 pip install -U spark-agent",
                )
            )

    typer.echo(render_health(rows, as_json=as_json))
    raise typer.Exit(health_exit_code(rows))


def _dir_size(path: Path) -> int:
    """目录/文件的累计字节数；统计失败按 0 处理（不能因为权限卡住卸载）。"""
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0


def _human_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _home_inventory(home: Path) -> list[tuple[str, int]]:
    """Spark home 顶层落点清单（名称, 字节数），按名字排序；不存在则空。"""
    if not home.is_dir():
        return []
    items = [(p.name, _dir_size(p)) for p in home.iterdir()]
    return sorted(items, key=lambda item: item[0])


@app.command("uninstall", help="清除 Spark 全部本地数据（配置/会话/记忆/知识库），先列清单再确认")
def uninstall_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认，直接删除"),
    list_only: bool = typer.Option(
        False, "--list", help="只打印将删除的清单，不做任何改动"
    ),
) -> None:
    """Remove every on-disk artifact under the Spark home directory.

    Spark 不自建 venv：Python 包本身属于安装它的环境，请用
    ``pip uninstall spark-agent``（或对应包管理器）移除；这里清的是数据。
    """
    home = default_home()
    inventory = _home_inventory(home)
    if not inventory:
        typer.echo(f"{home} 不存在或已是空目录，没有可清理的数据")
        return
    total = sum(size for _, size in inventory)
    typer.echo(banner("卸载 Spark"))
    typer.echo(f"数据目录：{home}")
    for name, size in inventory:
        typer.echo(f"  - {name}（{_human_size(size)}）")
    typer.echo(f"共 {len(inventory)} 项，合计 {_human_size(total)}")
    typer.echo(
        "提示：Python 包本身不在这里，卸载请另执行 pip uninstall spark-agent；"
        "各项目内的 .spark/ 工作区文件不受影响"
    )
    if list_only:
        return
    # 防御性守卫：SPARK_HOME 指向家目录本身、盘符根或任何不像 Spark 数据目录
    # 的位置时拒绝删除——一个环境变量写错不该有能力删掉用户整个家目录。
    resolved = home.resolve()
    user_home = Path.home().resolve()
    if resolved in (user_home, user_home.parent) or resolved.parent == resolved:
        typer.echo(
            f"拒绝删除：{resolved} 不是典型的 Spark 数据目录（应为 ~/.spark 或 "
            "$SPARK_HOME 指向的专用目录）。请核对 SPARK_HOME 后重试。",
            err=True,
        )
        raise typer.Exit(2)
    if not yes and not typer.confirm("确认删除以上全部数据？此操作不可恢复"):
        typer.echo("已取消，未删除任何文件")
        return
    try:
        import shutil

        shutil.rmtree(home)
    except OSError as exc:
        typer.echo(f"删除失败：{exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"已删除 {home}")


@app.command("a2a-serve", help="以 A2A 协议（Agent2Agent）暴露本 agent，供跨编排器调用")
def a2a_serve_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址（默认仅本机）"),
    port: int = typer.Option(41241, "--port", help="监听端口（0 = 随机）"),
    approval: str = typer.Option(
        "full-auto", "--approval", help="无人值守服务的审批模式（默认 full-auto）"
    ),
) -> None:
    """Serve the agent over the A2A protocol (JSON-RPC over HTTP).

    Agent Card 在 /.well-known/agent-card.json；message/send 为非流式实现。
    """
    from spark.config import load_config as _load

    try:
        cfg = _load(workdir=workdir.resolve(), approval=approval)  # type: ignore[arg-type]
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    from spark.a2a_server import serve_a2a

    try:
        server, base_url = serve_a2a(workdir.resolve(), cfg, host=host, port=port)
    except OSError as exc:
        typer.echo(f"A2A server 启动失败：{exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"◆ Spark A2A  →  {base_url}")
    typer.echo(f"  Agent Card: {base_url}/.well-known/agent-card.json")
    typer.echo("  Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


mcp_app = typer.Typer(help="MCP 插件市场：搜索/添加/移除 MCP server（与 Web 设置面板同一配置面）")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("list")
def mcp_list_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """列出当前配置的 MCP server。"""
    cfg = _common_cfg(workdir, config, None, None, None)
    if not cfg.mcp_servers:
        typer.echo("（未配置任何 MCP server）用 `spark mcp search <关键词>` 发现并添加。")
        return
    for s in cfg.mcp_servers:
        target = (
            f"{s.command} {' '.join(s.args)}".strip()
            if s.transport == "stdio"
            else (s.http.url if s.http else "?")
        )
        ro = f" · 只读白名单 {len(s.readonly_tools)} 项" if s.readonly_tools else ""
        at = f" · 工具白名单 {len(s.allowed_tools)} 项" if s.allowed_tools else ""
        typer.echo(f"  {s.name}  [{s.transport}]  {target}{ro}{at}")


@mcp_app.command("search")
def mcp_search_cmd(
    query: str = typer.Argument(..., help="关键词，如 github / sqlite / browser"),
    limit: int = typer.Option(8, "--limit", help="返回条数（1-50）"),
) -> None:
    """搜索 MCP 注册表（离线时自动回退内置策划索引）。"""
    from spark.mcp_registry import search_servers

    try:
        result = search_servers(query, limit=limit)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"搜索失败：{exc}", err=True)
        raise typer.Exit(1) from exc
    items = result.get("items") or []
    typer.echo(f"来源：{result.get('source')}（{'离线兜底' if result.get('source') == 'offline' else '在线'}）")
    if not items:
        typer.echo("（无匹配结果）")
        return
    for it in items:
        china = it.get("china") or {}
        flag = "🇨🇳可用" if china.get("available") else "⚠直连存疑"
        typer.echo(f"  {it['name']}  [{it['transport']}]  {flag}")
        if it.get("description"):
            typer.echo(f"    {str(it['description'])[:110]}")
        if it.get("suggested_command"):
            typer.echo(f"    启动：{it['suggested_command']} {' '.join(it.get('suggested_args') or [])}")
        elif it.get("remote_url"):
            typer.echo(f"    远程：{it['remote_url']}")
    typer.echo("添加：spark mcp add <name>（--env KEY=VALUE 补充必需环境变量）")


@mcp_app.command("add")
def mcp_add_cmd(
    name: str = typer.Argument(..., help="注册表里的 server 名（先 search 找到）"),
    env: list[str] = typer.Option(
        None, "--env", help="环境变量 KEY=VALUE（可重复；必需变量缺失会被提醒）"
    ),
    readonly: bool = typer.Option(
        False, "--readonly", help="最小权限：只注册该 server 声明为只读的工具"
    ),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """从注册表按名添加一个 MCP server 到配置。"""
    from spark.config import McpServerConfig, McpTransportConfig
    from spark.mcp_registry import search_servers

    cfg = _common_cfg(workdir, config, None, None, None)
    if any(s.name == name for s in cfg.mcp_servers):
        typer.echo(f"{name} 已在配置中（spark mcp remove {name} 可移除）", err=True)
        raise typer.Exit(2)
    result = search_servers(name, limit=10)
    items = result.get("items") or []
    hit = next((it for it in items if it.get("name") == name), None)
    if hit is None:
        typer.echo(
            f"注册表里没找到 {name}（来源 {result.get('source')}）。先 `spark mcp search {name}` 核对名称。",
            err=True,
        )
        raise typer.Exit(2)
    env_map: dict[str, str] = {}
    for pair in env or []:
        k, _, v = pair.partition("=")
        if k.strip():
            env_map[k.strip()] = v
    required = [e["name"] for e in hit.get("required_env") or [] if e.get("required")]
    missing = [k for k in required if k not in env_map]
    if missing:
        typer.echo(f"缺少必需环境变量：{', '.join(missing)}（--env KEY=VALUE 提供）", err=True)
        raise typer.Exit(2)
    if hit.get("transport") == "stdio" and hit.get("suggested_command"):
        server = McpServerConfig(
            name=name,
            command=hit["suggested_command"],
            args=list(hit.get("suggested_args") or []),
            env=env_map,
            readonly_tools=["*"] if readonly else [],
        )
    elif hit.get("remote_url"):
        server = McpServerConfig(
            name=name,
            transport="streamable_http",
            http=McpTransportConfig(url=str(hit["remote_url"])),
            readonly_tools=["*"] if readonly else [],
        )
    else:
        typer.echo(f"{name} 没有可用的启动命令或远程地址，无法自动添加。", err=True)
        raise typer.Exit(2)
    cfg.mcp_servers = [*(cfg.mcp_servers or []), server]
    written = save(cfg, config)
    typer.echo(f"已添加 {name}（{server.transport}）→ {written}")
    if result.get("source") == "offline":
        typer.echo("（注意：本次来自离线策划索引，字段可能略有出入，建议 Web 设置面板核对）")


@mcp_app.command("remove")
def mcp_remove_cmd(
    name: str = typer.Argument(...),
    config: Path | None = typer.Option(None, "--config"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
) -> None:
    """从配置移除一个 MCP server。"""
    cfg = _common_cfg(workdir, config, None, None, None)
    before = len(cfg.mcp_servers or [])
    cfg.mcp_servers = [s for s in (cfg.mcp_servers or []) if s.name != name]
    if len(cfg.mcp_servers) == before:
        typer.echo(f"配置里没有 {name}", err=True)
        raise typer.Exit(2)
    written = save(cfg, config)
    typer.echo(f"已移除 {name} → {written}")


ide_app = typer.Typer(help="IDE 一键接入（ACP / VS Code）", no_args_is_help=True)
app.add_typer(ide_app, name="ide")


@ide_app.command("install", help="把 Spark 写入指定 IDE 的 ACP 配置")
def ide_install_cmd(
    target: str = typer.Argument(..., help="zed | jetbrains | neovim"),
    workdir: Path = typer.Option(Path("."), "--workdir", help="ACP 会话默认工作目录"),
    settings: Path | None = typer.Option(None, "--settings", help="自定义配置路径"),
    command: str = typer.Option("spark", "--command", help="编辑器拉起的可执行文件"),
    force: bool = typer.Option(False, "--force", help="配置损坏或非 Spark 生成时，备份后覆盖"),
) -> None:
    """Write/merge an ACP server entry without clobbering unrelated settings."""
    from spark.ide import install

    try:
        result = install(
            target,
            workdir.resolve(),
            settings_path=settings,
            command=command,
            force=force,
        )
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"✓ {result.target}: {result.path}")
    if result.backup:
        typer.echo(f"  备份: {result.backup}")
    if not result.changed:
        typer.echo("  已是目标配置，无需修改")
    if result.target == "neovim":
        typer.echo(f"  在 Neovim init.lua 中加入：{result.snippet.get('usage')}")
    elif result.target == "jetbrains":
        typer.echo("  在 JetBrains AI Chat → 配置 ACP Agent 中选择 Spark")


plugins_app = typer.Typer(help="工作区插件（清单/校验/安装/卸载/启用/停用）", no_args_is_help=True)
app.add_typer(plugins_app, name="plugins")


workflow_app = typer.Typer(help="多 Agent YAML 工作流：校验 DAG / 输出执行图")
app.add_typer(workflow_app, name="workflow")


@workflow_app.command("validate", help="校验工作流角色、步骤和依赖环")
def workflow_validate_cmd(
    path: Path = typer.Argument(..., help="YAML 工作流文件"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    check_agents: bool = typer.Option(False, "--check-agents", help="校验 role.agent 是否存在于 .claude/agents"),
) -> None:
    from spark.workflow import load_workflow

    agents = None
    if check_agents:
        from spark.agents import discover_declared_agents

        agents = {agent.name for agent in discover_declared_agents(workdir.resolve())}
    try:
        spec = load_workflow(path, workdir=workdir, available_agents=agents)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"✓ {spec.name} v{spec.version}: {len(spec.roles)} roles, {len(spec.steps)} steps")
    typer.echo("order: " + " -> ".join(spec.order()))


@workflow_app.command("graph", help="输出工作流执行图 JSON")
def workflow_graph_cmd(
    path: Path = typer.Argument(...),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    pretty: bool = typer.Option(True, "--pretty/--compact"),
) -> None:
    import json

    from spark.workflow import load_workflow

    try:
        spec = load_workflow(path, workdir=workdir)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(json.dumps(spec.graph(), ensure_ascii=False, indent=2 if pretty else None))


@workflow_app.command("run", help="按 DAG 并发执行 YAML 工作流")
def workflow_run_cmd(
    path: Path = typer.Argument(..., help="YAML 工作流文件"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: str | None = typer.Option(None, "--approval"),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
    sandbox_mode: str | None = typer.Option(None, "--sandbox-mode"),
    max_parallel: int = typer.Option(2, "--max-parallel", min=1, max=8),
    as_json: bool = typer.Option(False, "--json", help="输出 NDJSON 工作流事件"),
) -> None:
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(
            workdir, config, approval, model, provider, sandbox_mode=sandbox_mode
        )
        if cfg.agent.approval == "suggest":
            raise ConfigError(
                "spark workflow run cannot collect approvals; use --approval auto-edit or full-auto"
            )
        store = _store()
        session_id = store.create_session(workdir, cfg.provider.model, title=path.name)
        ok = asyncio.run(
            _workflow_run_async(
                path, workdir, cfg, store, session_id,
                max_parallel=max_parallel, as_json=as_json,
            )
        )
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    if not ok:
        raise typer.Exit(1)




pr_app = typer.Typer(help="Pull Request 上下文（GitHub/GitCode/Gitee；除 publish 外只读）")
app.add_typer(pr_app, name="pr")


def _pr_repo(repo: str, workdir: Path):
    from spark.pr import RepoRef, find_repo

    if repo:
        owner, _, name = repo.partition("/")
        if not owner or not name:
            typer.echo("--repo 需要 owner/name 格式", err=True)
            raise typer.Exit(2)
        return RepoRef(owner, name)
    return find_repo(workdir.resolve())


def _pr_client(repo, number: int, *, token: str = ""):
    from spark.pr import GitHubClient

    return GitHubClient(repo, token or None)


@pr_app.command("view", help="查看 PR 标题、文件、diff、评论和 CI 状态")
def pr_view_cmd(
    number: int = typer.Argument(..., help="PR 编号"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    repo: str = typer.Option("", "--repo", help="owner/name，默认读取 origin"),
    max_diff: int = typer.Option(20000, "--max-diff"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    import json

    from spark.pr import build_context, format_context

    try:
        ref = _pr_repo(repo, workdir)
        client = _pr_client(ref, number)
        try:
            context = build_context(client, number, max_diff_chars=max_diff)
        finally:
            client.close()
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if json_output:
        typer.echo(json.dumps(context, ensure_ascii=False, indent=2))
    else:
        typer.echo(format_context(context))


@pr_app.command("diff", help="只输出 PR 的 unified diff")
def pr_diff_cmd(
    number: int = typer.Argument(...),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    repo: str = typer.Option("", "--repo"),
) -> None:
    try:
        ref = _pr_repo(repo, workdir)
        client = _pr_client(ref, number)
        try:
            typer.echo(client.diff(number))
        finally:
            client.close()
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


@pr_app.command("review", help="生成结构化 review brief（风险、测试缺口、CI 清单）")
def pr_review_cmd(
    number: int = typer.Argument(...),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    repo: str = typer.Option("", "--repo"),
    max_diff: int = typer.Option(20000, "--max-diff"),
    json_output: bool = typer.Option(False, "--json"),
    include_diff: bool = typer.Option(False, "--diff", help="同时打印完整 PR 上下文"),
) -> None:
    import json

    from spark.pr import build_context, format_context, format_review, review_brief

    try:
        ref = _pr_repo(repo, workdir)
        client = _pr_client(ref, number)
        try:
            context = build_context(client, number, max_diff_chars=max_diff)
        finally:
            client.close()
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    brief = review_brief(context)
    if json_output:
        typer.echo(json.dumps({"review": brief, "context": context}, ensure_ascii=False, indent=2))
        return
    typer.echo(format_review(context, brief))
    if include_diff:
        typer.echo("\n" + format_context(context))


@pr_app.command("publish", help="发布 PR review 评论（必须 --confirm，写操作）")
def pr_publish_cmd(
    number: int = typer.Argument(...),
    body_file: Path = typer.Option(..., "--body-file", help="review Markdown 文件"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    repo: str = typer.Option("", "--repo"),
    event: str = typer.Option("COMMENT", "--event", help="COMMENT/REQUEST_CHANGES/APPROVE"),
    confirm: bool = typer.Option(False, "--confirm", help="显式确认发布到远端"),
) -> None:
    from spark.pr import post_review

    try:
        body = body_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        typer.echo(f"读取 review 文件失败: {exc}", err=True)
        raise typer.Exit(2) from exc
    if not body:
        typer.echo("review 文件为空", err=True)
        raise typer.Exit(2)
    if not confirm:
        typer.echo("预览（未发布）：\n")
        typer.echo(body[:2000])
        typer.echo("\n加上 --confirm 才会发布到 PR。", err=True)
        raise typer.Exit(2)
    try:
        ref = _pr_repo(repo, workdir)
        client = _pr_client(ref, number)
        try:
            result = post_review(client, number, body, event=event)
        finally:
            client.close()
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"已发布 review: {result.get('html_url') or result.get('id') or 'ok'}")


@pr_app.command("checks", help="输出 PR 的 CI 检查")
def pr_checks_cmd(
    number: int = typer.Argument(...),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    repo: str = typer.Option("", "--repo"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    import json

    try:
        ref = _pr_repo(repo, workdir)
        client = _pr_client(ref, number)
        try:
            checks = client.checks(number)
        finally:
            client.close()
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if json_output:
        typer.echo(json.dumps(checks, ensure_ascii=False, indent=2))
        return
    for row in checks.get("check_runs") or []:
        typer.echo(f"{row.get('conclusion') or row.get('status')}\t{row.get('name')}")
    for row in checks.get("statuses") or []:
        typer.echo(f"{row.get('state')}\t{row.get('context')}")




@plugins_app.command("list", help="列出 .spark/plugins 下的插件与清单状态")
def plugins_list_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Path | None = typer.Option(None, "--config"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    from spark.plugins import record_to_dict, scan_plugins

    records = scan_plugins(workdir.resolve())
    enabled = _enabled_plugin_names(config)
    if json_output:
        import json

        payload = [
            {**record_to_dict(record), "enabled": record.name in enabled}
            for record in records
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    if not records:
        typer.echo("没有插件。")
        return
    for record in records:
        version = record.manifest.version if record.manifest else "-"
        status = record.error or "ok"
        active = "enabled" if record.name in enabled else "-"
        typer.echo(f"{record.name}\t{version}\t{status}\t{active}\t{record.path}")


@plugins_app.command("validate", help="校验一个插件目录或 manifest")
def plugins_validate_cmd(
    source: Path = typer.Argument(..., help="插件目录或 spark-plugin.json"),
    require_entry: bool = typer.Option(True, "--require-entry/--no-require-entry"),
) -> None:
    from spark.plugins import validate_plugin

    try:
        manifest = validate_plugin(source, require_entry=require_entry)
    except PluginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"✓ {manifest.name} {manifest.version}")
    if manifest.permissions:
        typer.echo("  permissions: " + ", ".join(manifest.permissions))
    if manifest.tools:
        typer.echo(f"  tools: {len(manifest.tools)}")
    if manifest.hooks:
        typer.echo(f"  hooks: {len(manifest.hooks)}")


@plugins_app.command("install", help="从本地目录安装插件（复制到 .spark/plugins）")
def plugins_install_cmd(
    source: Path = typer.Argument(..., help="插件目录"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    force: bool = typer.Option(False, "--force", help="已存在时备份旧版本再安装"),
) -> None:
    from spark.plugins import install_local

    try:
        record = install_local(source, workdir.resolve(), force=force)
    except PluginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"✓ {record.name} -> {record.path}")


@plugins_app.command("search", help="在插件市场索引中搜索（--registry 或 SPARK_PLUGIN_REGISTRY）")
def plugins_search_cmd(
    query: str = typer.Argument("", help="搜索词；留空列出全部"),
    registry: str = typer.Option("", "--registry", help="市场索引 URL 或本地 JSON"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    import json

    from spark.plugins import search_registry

    try:
        entries = search_registry(query, registry or None)
    except PluginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if json_output:
        typer.echo(
            json.dumps([entry.model_dump() for entry in entries], ensure_ascii=False)
        )
        return
    if not entries:
        typer.echo("没有匹配的插件。")
        return
    for entry in entries:
        typer.echo(f"{entry.name}\t{entry.version}\t{entry.description}\t{entry.url}")


@plugins_app.command("updates", help="检查已安装插件在市场中是否有新版本")
def plugins_updates_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    registry: str = typer.Option("", "--registry", help="市场索引 URL 或本地 JSON"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    import json

    from spark.plugins import check_updates

    try:
        updates = check_updates(workdir.resolve(), registry or None)
    except PluginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if json_output:
        typer.echo(
            json.dumps(
                [item.__dict__ for item in updates], ensure_ascii=False
            )
        )
        return
    if not updates:
        typer.echo("所有已安装插件都是最新版本。")
        return
    for item in updates:
        typer.echo(f"{item.name}\t{item.installed} -> {item.available}\t{item.url}")


@plugins_app.command("upgrade", help="从市场索引升级指定插件")
def plugins_upgrade_cmd(
    name: str = typer.Argument(..., help="插件 name"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    registry: str = typer.Option("", "--registry", help="市场索引 URL 或本地 JSON"),
) -> None:
    from spark.plugins import upgrade_from_registry

    try:
        record = upgrade_from_registry(name, workdir.resolve(), source=registry or None)
    except PluginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"✓ {record.name} {record.manifest.version if record.manifest else ''} -> {record.path}")


@plugins_app.command("install-registry", help="按市场索引中的名称安装插件")
def plugins_install_registry_cmd(
    name: str = typer.Argument(..., help="插件 name"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    registry: str = typer.Option("", "--registry", help="市场索引 URL 或本地 JSON"),
    force: bool = typer.Option(False, "--force", help="已存在时备份旧版本再安装"),
) -> None:
    from spark.plugins import install_from_registry

    try:
        record = install_from_registry(
            name, workdir.resolve(), source=registry or None, force=force
        )
    except PluginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"✓ {record.name} -> {record.path}")


@plugins_app.command("install-bundle", help="按市场索引中的插件包一次安装多个插件")
def plugins_install_bundle_cmd(
    name: str = typer.Argument(..., help="插件包 name"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    registry: str = typer.Option("", "--registry", help="市场索引 URL 或本地 JSON"),
    force: bool = typer.Option(False, "--force", help="已存在时备份旧版本再安装"),
) -> None:
    from spark.plugins import install_bundle

    try:
        records = install_bundle(
            name, workdir.resolve(), source=registry or None, force=force
        )
    except PluginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    for record in records:
        typer.echo(f"✓ {record.name} -> {record.path}")


@plugins_app.command("install-official", help="安装 official 插件包")
def plugins_install_official_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    registry: str = typer.Option("", "--registry", help="市场索引 URL 或本地 JSON"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    from spark.plugins import install_bundle

    try:
        records = install_bundle(
            "official", workdir.resolve(), source=registry or None, force=force
        )
    except PluginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    for record in records:
        typer.echo(f"✓ {record.name} -> {record.path}")
    typer.echo("插件包已安装；每个插件仍可单独 spark plugins disable <name>。")


@plugins_app.command("install-git", help="从 git 仓库克隆并安装插件（仅 https/ssh 网络地址）")
def plugins_install_git_cmd(
    url: str = typer.Argument(..., help="git 地址"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    ref: str = typer.Option("", "--ref", help="分支/标签"),
    subdir: str = typer.Option("", "--subdir", help="插件在仓库内的子目录"),
    force: bool = typer.Option(False, "--force", help="已存在时备份旧版本再安装"),
) -> None:
    from spark.plugins import install_git

    try:
        record = install_git(
            url, workdir.resolve(), ref=ref, subdir=subdir, force=force
        )
    except PluginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"✓ {record.name} -> {record.path}")


@plugins_app.command("remove", help="卸载插件（移动到 .spark/plugins/.trash）")
def plugins_remove_cmd(
    name: str = typer.Argument(..., help="插件 name"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
) -> None:
    from spark.plugins import remove_plugin

    try:
        target = remove_plugin(name, workdir.resolve())
    except PluginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"✓ {name} -> {target}")


def _enabled_plugin_names(config: Path | None = None) -> set[str]:
    import tomlkit

    from spark.config import default_home

    path = config or (default_home() / "config.toml")
    if not path.exists():
        return set()
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - listing must not fail on a broken config
        return set()
    plugins = doc.get("plugins")
    if plugins is None or not hasattr(plugins, "get"):
        return set()
    names = plugins.get("enabled")
    if not isinstance(names, list):
        return set()
    return {str(item) for item in names}


def _set_plugin_enabled(name: str, enabled: bool, config: Path | None) -> Path:
    """Reuse the same config writer as the Web plugin toggle."""
    from spark.plugins import set_plugin_enabled

    return set_plugin_enabled(name, enabled, path=config)


@plugins_app.command("enable", help="启用插件 hooks/tools（写入全局 config.toml 的 plugins.enabled）")
def plugins_enable_cmd(
    name: str = typer.Argument(..., help="插件 name"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    from spark.plugins import scan_plugins

    if name not in {record.name for record in scan_plugins(workdir.resolve())}:
        typer.echo(f"插件未安装：{name}（先运行 spark plugins install）", err=True)
        raise typer.Exit(2)
    try:
        path = _set_plugin_enabled(name, True, config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"✓ 已启用 {name} -> {path}")
    typer.echo("提示：插件需声明 hooks 权限，重启会话后生效。")


@plugins_app.command("disable", help="停用插件 hooks/tools（从全局 config.toml 的 plugins.enabled 移除）")
def plugins_disable_cmd(
    name: str = typer.Argument(..., help="插件 name"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    try:
        path = _set_plugin_enabled(name, False, config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"✓ 已停用 {name} -> {path}")


config_app = typer.Typer(help="查看或修改配置", no_args_is_help=True)
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Path | None = typer.Option(None, "--config"),
    as_json: bool = typer.Option(False, "--json", help="输出机器可读 JSON"),
) -> None:
    """Print the effective configuration with secrets masked."""
    try:
        cfg = read_config(config, workdir.resolve())
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if as_json:
        import json

        typer.echo(json.dumps(config_as_dict(cfg), ensure_ascii=False, indent=2))
        return
    typer.echo(render_config_text(cfg))


@config_app.command("set")
def config_set_cmd(
    key: str = typer.Argument(..., help="点号路径，例如 agent.max_repeat_calls"),
    value: str = typer.Argument(..., help="新值"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Set one configuration value and persist it."""
    try:
        cfg = read_config(config, workdir.resolve())
        set_value(cfg, key, value)
        written = save(cfg, config)
    except SetupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    shown = "*" * len(value) if key.endswith("api_key") else value
    typer.echo(f"{key} = {shown}")
    typer.echo(f"已保存到 {written}")


@config_app.command("path", help="输出当前使用的配置文件绝对路径")
def config_path_cmd() -> None:
    """Print the configuration file location."""
    typer.echo(str(default_home() / "config.toml"))


@app.command("enterprise", help="启动企业控制面 API（/api/v1；默认仅回环 127.0.0.1:18800）")
def enterprise_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="绑定地址"),
    port: int = typer.Option(18800, "--port", help="监听端口"),
    reload: bool = typer.Option(False, "--reload", help="代码变更自动重载（开发用）"),
) -> None:
    """Run the FastAPI enterprise control plane (SQLite/dev auth by default)."""
    try:
        from spark.control.cli import serve
    except Exception as exc:  # pragma: no cover - enterprise extra missing
        typer.echo(
            "企业控制面依赖未安装：pip install 'spark-agent[enterprise]'",
            err=True,
        )
        raise typer.Exit(1) from exc
    serve(host=host, port=port, reload=reload)
