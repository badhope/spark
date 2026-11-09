from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from rich.markup import escape as rich_escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from spark.config import SparkConfig, require_api_key
from spark.core.loop import AgentLoop
from spark.core.turn_store import persistent_checkpointer
from spark.models import ApprovalAction, ApprovalDecision, ApprovalRequest, TurnEvent
from spark.providers.base import Provider
from spark.providers.probe import probe_provider
from spark.store import SessionStore
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolRegistry
from spark.tui.welcome import error_hint, help_text, status_text, welcome_text

_NARROW_TERMINAL_WIDTH = 32
_TOOLBAR_LABELS = {
    "test-model": ("Test model", "T"),
    "help": ("Help (?)", "?"),
}
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_FLUSH_THRESHOLD = 200  # flush text buffer after this many chars


class HelpScreen(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("使用帮助", id="title"),
            RichLog(id="help-body", wrap=True, markup=True, max_lines=200),
            Horizontal(Button("关闭 (Esc)", id="close", variant="primary")),
            id="dialog",
        )

    def on_mount(self) -> None:
        self.call_after_refresh(self._fill_body)

    def _fill_body(self) -> None:
        try:
            self.query_one("#help-body", RichLog).write(help_text())
        except NoMatches:
            # help 组件可能尚未挂载，跳过不阻塞
            return

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key in {"escape", "question_mark"}:
            self.dismiss(None)


class ApprovalScreen(ModalScreen[ApprovalDecision]):
    CSS = """
    #dialog {
        width: 100%;
        max-width: 60;
        height: auto;
        max-height: 100%;
        padding: 1 2;
        background: #1c1815;
    }
    #title {
        width: 100%;
        height: auto;
    }
    #detail {
        width: 100%;
        height: auto;
    }
    #approval-actions {
        width: 100%;
        height: auto;
    }
    #approval-actions Button {
        min-width: 0;
        width: 1fr;
    }
    #dialog.narrow {
        padding: 0 1;
    }
    #approval-actions.narrow {
        layout: vertical;
    }
    #approval-actions.narrow Button {
        width: 100%;
    }
    """

    def __init__(
        self, request: ApprovalRequest, access_mode: str = "workspace"
    ) -> None:
        super().__init__()
        self.request = request
        self.access_mode = access_mode

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                f"是否允许执行 {self.request.tool_call.name}？  [访问级别={self.access_mode}]",
                id="title",
            ),
            RichLog(id="detail", wrap=True, markup=True),
            Horizontal(
                Button("允许 (y)", id="allow", variant="success"),
                Button("拒绝 (n)", id="deny", variant="error"),
                Button("始终允许 (a)", id="always", variant="primary"),
                id="approval-actions",
            ),
            id="dialog",
        )

    def _set_narrow(self, narrow: bool) -> None:
        self.query_one("#dialog").set_class(narrow, "narrow")
        self.query_one("#approval-actions").set_class(narrow, "narrow")

    def on_mount(self) -> None:
        self._set_narrow(self.size.width <= _NARROW_TERMINAL_WIDTH)
        log = self.query_one("#detail", RichLog)
        log.write(rich_escape(self.request.diff or self.request.summary))
        try:
            self.query_one("#deny", Button).focus()
        except Exception:
            # 按钮聚焦失败时用户可以手动点击
            logger.debug("无法聚焦 deny 按钮", exc_info=True)

    def on_resize(self, event: Resize) -> None:
        self._set_narrow(event.size.width <= _NARROW_TERMINAL_WIDTH)

    def on_key(self, event) -> None:
        if event.key == "escape" or event.key == "n":
            self.dismiss(
                ApprovalDecision(tool_call_id=self.request.tool_call.id, action="deny")
            )
        elif event.key == "y":
            self.dismiss(
                ApprovalDecision(tool_call_id=self.request.tool_call.id, action="allow")
            )
        elif event.key == "a":
            self.dismiss(
                ApprovalDecision(
                    tool_call_id=self.request.tool_call.id, action="allow_always"
                )
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping: dict[str, ApprovalAction] = {
            "allow": "allow",
            "deny": "deny",
            "always": "allow_always",
        }
        action = mapping[event.button.id or "deny"]
        self.dismiss(
            ApprovalDecision(tool_call_id=self.request.tool_call.id, action=action)
        )


class _SessionPickerList(Vertical):
    """Inner vertical that holds the session buttons."""


class SessionPicker(ModalScreen[str | None]):
    """Simple modal for picking a session to resume."""

    CSS = """
    #dialog {
        width: 100%;
        max-width: 80;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: #1c1815;
    }
    #title {
        width: 100%;
        height: auto;
    }
    #picker-list {
        width: 100%;
        height: auto;
    }
    #picker-list Button {
        width: 100%;
        height: auto;
        min-height: 1;
        margin-top: 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("选择会话 (Esc 取消)", id="title"),
            _SessionPickerList(id="picker-list"),
            id="dialog",
        )

    def on_mount(self) -> None:
        container = self.query_one("#picker-list", Vertical)
        for s in self.app.store.list_sessions():
            sid = s["id"]
            label = s.get("title") or sid[:8]
            workdir = s.get("workdir", "")
            container.mount(
                Button(f"{label}  @{workdir}", id=f"session-{sid}", variant="default")
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        sid = (event.button.id or "").replace("session-", "", 1)
        self.dismiss(sid)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class SparkApp(App):
    TITLE = "Spark"
    SUB_TITLE = "Ember"
    CSS = """
    Screen { background: #14110f; }
    #status {
        width: 100%;
        min-width: 0;
        max-width: 100%;
        height: 1;
        overflow-x: hidden;
        color: #ff7a3d;
        text-style: bold;
    }
    #toolbar {
        width: 100%;
        min-width: 0;
        height: 3;
    }
    #toolbar Button {
        min-width: 0;
        width: 1fr;
    }
    #chat { height: 1fr; }
    #composer { dock: bottom; }
    #dialog { padding: 1 2; background: #1c1815; }
    #help-body { height: 1fr; }
    #title { color: #ff7a3d; text-style: bold; }
    #footer FooterKey {
        min-width: 0;
        width: 1fr;
    }
    """
    BINDINGS = [
        Binding("ctrl+c", "cancel_turn", "Cancel turn", show=True),
        Binding("ctrl+d", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_chat", "Clear view", show=True),
        Binding("ctrl+t", "test_model", "Test model", show=True),
        Binding("question_mark", "toggle_help", "Help", show=True),
        Binding("ctrl+r", "resume_session", "Switch session", show=True),
    ]

    def __init__(
        self,
        *,
        workdir: Path,
        cfg: SparkConfig,
        store: SessionStore,
        session_id: str,
        provider: Provider,
        registry: ToolRegistry,
        bridge: McpBridge | None,
        initial_prompt: str | None = None,
        memory=None,
    ) -> None:
        super().__init__()
        self.workdir = workdir
        self.cfg = cfg
        self.store = store
        self.session_id = session_id
        self.provider = provider
        self.registry = registry
        self.bridge = bridge
        self.initial_prompt = initial_prompt
        self.memory = memory
        self.loop_engine = AgentLoop(
            workdir=workdir,
            cfg=cfg,
            provider=provider,
            registry=registry,
            store=store,
            session_id=session_id,
            approver=self._approve,
            memory=memory,
            checkpointer=persistent_checkpointer(),
        )
        self._running = False
        self._current_text = ""
        self._spinner_index = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._status_text(), id="status")
        yield Horizontal(
            Button("Test model", id="test-model", variant="primary"),
            Button("Help (?)", id="help", variant="default"),
            id="toolbar",
        )
        yield RichLog(
            id="chat", wrap=True, highlight=True, markup=True, max_lines=5000
        )
        yield Input(
            placeholder="Describe a task and press Enter (? for help)", id="composer"
        )
        yield Footer(id="footer")

    def _status_text(self) -> str:
        return status_text(self.cfg, self.session_id, str(self.workdir))

    def _set_toolbar_compact(self, compact: bool) -> None:
        try:
            toolbar = self.query_one("#toolbar", Horizontal)
        except NoMatches:
            # toolbar 组件可能尚未挂载，后续 _update_status 会重试
            return
        toolbar.set_class(compact, "narrow")
        for button_id, labels in _TOOLBAR_LABELS.items():
            button = self.query_one(f"#{button_id}", Button)
            button.label = labels[1] if compact else labels[0]

    def on_resize(self, event: Resize) -> None:
        self._set_toolbar_compact(event.size.width <= _NARROW_TERMINAL_WIDTH)

    def on_mount(self) -> None:
        self._set_toolbar_compact(self.size.width <= _NARROW_TERMINAL_WIDTH)
        self.set_interval(0.1, self._tick_spinner)
        chat = self.query_one("#chat", RichLog)
        chat.write(welcome_text(self.cfg, str(self.workdir)))
        restored = 0
        for msg in self.loop_engine.history:
            if msg.role in {"user", "assistant"} and msg.content:
                chat.write(f"[bold cyan]{msg.role}[/bold cyan] {rich_escape(msg.content)}")
                restored += 1
        if restored:
            chat.write(f"[dim]（已恢复 {restored} 条历史消息）[/dim]")
        if self.initial_prompt:
            self.run_worker(self._run_prompt(self.initial_prompt), exclusive=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text or self._running:
            return
        from spark.slash import parse_slash

        slash = parse_slash(text)
        if slash is not None and slash.name == "test":
            self.run_worker(self._run_test_slash(slash.args), exclusive=True)
            return
        self.run_worker(self._run_prompt(text), exclusive=True)

    def action_cancel_turn(self) -> None:
        self.loop_engine.cancel()

    def action_clear_chat(self) -> None:
        self.query_one("#chat", RichLog).clear()

    def action_test_model(self) -> None:
        if self._running:
            return
        self.run_worker(self._test_model(), exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "test-model":
            self.action_test_model()
        elif event.button.id == "help":
            self.action_toggle_help()

    def action_toggle_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
        else:
            self.push_screen(HelpScreen())

    async def _test_model(self) -> None:
        chat = self.query_one("#chat", RichLog)
        chat.write("[dim]正在测试模型连通性…[/dim]")
        try:
            key = require_api_key(self.cfg) or ""
            result = await probe_provider(
                base_url=self.cfg.provider.base_url,
                api_key=key,
                model=self.cfg.provider.model,
                rounds=2,
            )
        except Exception as exc:
            chat.write(f"[red bold]测试失败[/red bold] {rich_escape(str(exc))}")
            chat.write(f"[yellow]{error_hint(str(exc))}[/yellow]")
            return
        if result.ok:
            chat.write(
                f"[green]连通正常[/green] {result.latency_ms}ms x{result.rounds}"
            )
            chat.write(f"content: {rich_escape(str(result.content))}")
            chat.write(f"stream: {rich_escape(str(result.stream_content))}")
        else:
            chat.write(f"[red bold]测试失败[/red bold] {rich_escape(result.error or '')}")
            chat.write(f"[yellow]{error_hint(result.error or '')}[/yellow]")

    async def _approve(self, request: ApprovalRequest) -> ApprovalDecision:
        return await self.push_screen_wait(
            ApprovalScreen(request, access_mode=self.cfg.agent.sandbox_mode)
        )

    async def _run_prompt(self, text: str) -> None:
        # H5：自定义命令展开（.spark/commands/*.md），展示原文、执行展开产物
        from spark.slash import expand_command

        turn_text = text
        try:
            expanded = expand_command(self.registry.ctx.sandbox.root, text)
        except Exception:
            expanded = None
        if expanded:
            turn_text = expanded
        self._running = True
        self._current_text = ""
        self._update_status()
        chat = self.query_one("#chat", RichLog)
        chat.write(f"[bold cyan]你[/bold cyan] {rich_escape(text)}")
        try:
            async for event in self.loop_engine.iter_turn_managed(turn_text):
                self._render(event, chat)
        finally:
            self._flush_text(chat)
            self._running = False
            self._update_status()

    async def _run_test_slash(self, raw_args: str) -> None:
        """/test：跑仓库测试，渲染摘要；失败时把 traceback 回灌给 agent 修正。"""
        self._running = True
        self._current_text = ""
        self._update_status()
        chat = self.query_one("#chat", RichLog)
        shown = (f"/test {raw_args}").strip()
        chat.write(f"[bold cyan]你[/bold cyan] {rich_escape(shown)}")
        chat.write("[dim]⏳ 正在运行测试…[/dim]")
        from spark.slash import handle_test

        sandbox = self.registry.ctx.sandbox
        try:
            result = await asyncio.to_thread(handle_test, sandbox, raw_args)
        except Exception as exc:  # noqa: BLE001 — TUI 不应因 slash 崩溃
            chat.write(f"[red]✗ /test 执行异常：{rich_escape(str(exc))}[/red]")
            self._running = False
            self._update_status()
            return
        color = "green" if result.ok else "red"
        chat.write(f"[{color}]{rich_escape(result.summary)}[/{color}]")
        if result.output:
            chat.write(f"[dim]{rich_escape(result.output)}[/dim]")
        if not result.ok and result.feedback:
            chat.write("[yellow]→ 回灌失败输出，交由 agent 修正…[/yellow]")
            try:
                async for event in self.loop_engine.iter_turn_managed(result.feedback):
                    self._render(event, chat)
            finally:
                self._flush_text(chat)
        self._running = False
        self._update_status()

    def _flush_text(self, chat: RichLog) -> None:
        if self._current_text:
            chat.write(self._current_text.rstrip("\n"))
            self._current_text = ""

    def _render(self, event: TurnEvent, chat: RichLog) -> None:
        if event.type == "text_delta" and event.text:
            self._current_text += event.text
            if "\n" in event.text or len(self._current_text) > _FLUSH_THRESHOLD:
                self._flush_text(chat)
            return
        self._flush_text(chat)
        if event.type == "reasoning_delta" and event.text:
            chat.write(f"[dim italic]思考: {rich_escape(event.text)}[/dim italic]")
        elif event.type == "tool_start" and event.tool_call:
            chat.write(f"[bold yellow]▶ {rich_escape(event.tool_call.name)}[/bold yellow] ...")
        elif event.type == "tool_end" and event.tool_call and event.result:
            payload = str(event.result.payload)
            if len(payload) > 500:
                payload = payload[:500] + "..."
            status = "ok" if event.result.ok else "fail"
            color = "green" if event.result.ok else "red"
            chat.write(
                f"[{color}]◀ {event.tool_call.name} [{status}][/color] {rich_escape(payload)}"
            )
        elif event.type == "plan" and event.data is not None:
            steps = event.data.get("steps") or []
            if steps:
                chat.write("[bold]plan:[/bold] " + " | ".join(
                    str(s.get("title", "")) for s in steps
                ))
            else:
                chat.write("[dim]plan: (已清空)[/dim]")
        elif event.type == "context" and event.data and event.data.get("usage"):
            usage = event.data["usage"]
            chat.write(
                f"[dim]context: {usage.get('used')}/{usage.get('limit')} tokens[/dim]"
            )
        elif (
            event.type == "compaction"
            and event.data
            and event.data.get("before_tokens")
        ):
            chat.write(
                f"[dim]compacted: {event.data['before_tokens']} -> "
                f"{event.data.get('after_tokens')} tokens[/dim]"
            )
        elif event.type == "turn_error":
            text = event.text or "未知错误"
            hint = error_hint(text)
            chat.write("[red]━" * 30 + "[/red]")
            chat.write(f"[red bold]✗ 错误[/red bold] {rich_escape(text)}")
            if hint:
                chat.write(f"[yellow]→ {hint}[/yellow]")
            chat.write("[red]━" * 30 + "[/red]")
        elif event.type == "turn_end" and event.text:
            pass

    def _update_status(self, spinner_frame: str = "") -> None:
        try:
            status = self.query_one("#status", Static)
        except NoMatches:
            return
        base = self._status_text()
        if self._running and spinner_frame:
            status.update(f"Spark {spinner_frame} {base}")
            status.styles.color = "#ffcc00"
        else:
            status.update(base)
            status.styles.color = "#ff7a3d"

    def _tick_spinner(self) -> None:
        if not self._running:
            return
        self._spinner_index = (self._spinner_index + 1) % len(_SPINNER_FRAMES)
        self._update_status(_SPINNER_FRAMES[self._spinner_index])

    def action_resume_session(self) -> None:
        if self._running:
            return
        self.run_worker(self._switch_session(), exclusive=True)

    async def _switch_session(self) -> None:
        target = await self.push_screen_wait(SessionPicker())
        if not target or target == self.session_id:
            return
        self.loop_engine.cancel()
        self.session_id = target
        self.loop_engine = AgentLoop(
            workdir=self.workdir,
            cfg=self.cfg,
            provider=self.provider,
            registry=self.registry,
            store=self.store,
            session_id=target,
            approver=self._approve,
            memory=self.memory,
        )
        self.query_one("#chat", RichLog).clear()
        self.query_one("#chat", RichLog).write(
            welcome_text(self.cfg, str(self.workdir))
        )
        restored = 0
        chat = self.query_one("#chat", RichLog)
        for msg in self.loop_engine.history:
            if msg.role in {"user", "assistant"} and msg.content:
                chat.write(f"[bold cyan]{msg.role}[/bold cyan] {rich_escape(msg.content)}")
                restored += 1
        if restored:
            chat.write(f"[dim]（已恢复到会话 {target[:8]}，{restored} 条历史消息）[/dim]")
        self._update_status()

    async def on_unmount(self) -> None:
        try:
            self.loop_engine.cancel()
        except Exception:
            # finally 块中取消运行中的 loop，失败仅记录不影响退出
            logger.debug("on_unmount 取消 loop_engine 失败", exc_info=True)
        if self.bridge:
            await self.bridge.close()
        self.store.close()
