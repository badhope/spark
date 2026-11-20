from __future__ import annotations

import logging
import sys
import time
from importlib.resources import files
from pathlib import Path

from spark.config import SparkConfig
from spark.core.tokens import estimate_message_tokens
from spark.models import ChatMessage
from spark.sandbox import safe_bare_filename
from spark.skills import discover_skills, inject_skills

logger = logging.getLogger(__name__)


def load_system_prompt() -> str:
    return files("spark.prompts").joinpath("system.md").read_text(encoding="utf-8")


def load_local_system_prompt() -> str:
    """Compact prompt for 1B-class local GGUF models.

    The full agent prompt plus skills/repo-map/tool schemas is several thousand
    tokens; a 1B model starts ignoring instructions and inventing tool results.
    Keep identity, tool discipline and workdir safety, drop the optional
    discovery blocks.
    """
    return files("spark.prompts").joinpath("system_local.md").read_text(encoding="utf-8")


# 项目指令文件生态（2024→2026 事实标准）：
# * 根层：AGENTS.md（agents.md / agentsstandard.com 规范）与 CLAUDE.md（Anthropic）并列加载
# * 全局层：~/.agents/{AGENTS.md,CLAUDE.md} —— 用户级跨项目约定
# * 子目录层：workdir 内嵌套目录的 AGENTS.md/CLAUDE.md（Claude Code memory hierarchy 语义）
_GLOBAL_AGENTS_DIR = Path.home() / ".agents"
_INSTRUCTION_NAMES = ("AGENTS.md", "CLAUDE.md")
_MAX_INSTRUCTION_DEPTH = 3
_MAX_INSTRUCTION_FILES = 20
_MAX_INSTRUCTION_TOTAL_CHARS = 16000


def _read_instruction(path: Path, *, inside_workdir: Path | None) -> str | None:
    """安全读取单个指令文件：UTF-8、symlink 解析后必须仍在 workdir 内
    （全局层跳过该校验）、超长截断。返回文本或 None。"""
    if inside_workdir is not None:
        try:
            resolved = path.resolve()
        except OSError:
            return None
        if not resolved.is_relative_to(inside_workdir) or not resolved.is_file():
            return None
    elif not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.strip():
        return None
    return text[: _MAX_INSTRUCTION_TOTAL_CHARS // 2]


def _walk_dirs(top: Path, max_depth: int):
    """深度受限目录遍历，产出 (dir_path, depth)。不跟随 symlink。"""
    stack = [(top, 0)]
    while stack:
        current, depth = stack.pop()
        yield current, depth
        if depth >= max_depth:
            continue
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for e in entries:
            if e.is_dir() and not e.is_symlink():
                stack.append((e, depth + 1))


def load_agents_md(workdir: Path, cfg: SparkConfig) -> str | None:
    """加载项目指令（AGENTS.md/CLAUDE.md 生态）。

    三级叠加：~/.agents/ 全局 → workdir 根（配置指定名 + CLAUDE.md）→
    workdir 内嵌套目录（深度 ≤3、最多 20 个文件）。全部内容以
    ``## <路径>`` 标注来源后拼接，总量上限 16k 字符。任一阶段为空则跳过；
    完全无文件时返回 None（上游保持既有 <project_instructions> 包裹行为）。
    路径逃逸防护保留：配置名含 ``../`` 或分隔符时该名称被跳过（测试语义不变）。
    """
    chunks: list[str] = []
    base = workdir.resolve()
    seen: set[str] = set()

    def add(path: Path, inside: bool) -> None:
        nonlocal chunks
        key = str(path.resolve()) if inside else str(path)
        if key in seen:
            return
        seen.add(key)
        text = _read_instruction(path, inside_workdir=base if inside else None)
        if text:
            chunks.append(f"## {path}\n{text}")

    # 1) 全局层
    for name in _INSTRUCTION_NAMES:
        add(_GLOBAL_AGENTS_DIR / name, inside=False)

    # 2) 根层
    root_names = set(_INSTRUCTION_NAMES)
    cfg_name = (cfg.context.agents_md or "").strip()
    if cfg_name and safe_bare_filename(cfg_name):
        root_names.add(cfg_name)
    for name in root_names:
        add(base / name, inside=True)

    # 3) 子目录层
    counted = 0
    for root, depth in _walk_dirs(base, _MAX_INSTRUCTION_DEPTH):
        if depth == 0:
            continue
        rel = root.relative_to(base)
        if rel.parts and rel.parts[0] in {
            ".git",
            "node_modules",
            "vendor",
            "dist",
            "build",
            "__pycache__",
            ".venv",
            ".tox",
            ".spark",
            ".agents",
        }:
            continue
        for name in _INSTRUCTION_NAMES:
            if counted >= _MAX_INSTRUCTION_FILES:
                break
            p = Path(root) / name
            if p.is_file():
                add(p, inside=True)
                counted += 1

    if not chunks:
        return None
    text = "\n\n".join(chunks)
    if len(text) > _MAX_INSTRUCTION_TOTAL_CHARS:
        text = text[:_MAX_INSTRUCTION_TOTAL_CHARS]
    return text


_ENV_BLOCK_CACHE: list[str] = []


def environment_block(workdir: Path) -> str:
    """Host platform plus the runtimes that are actually installed.

    The prompt used to assert a Linux toolchain unconditionally, so on Windows
    every command the model issued started with a `python3`/`pip3` that does not
    exist there. Probed once per process: PATH contents do not depend on the
    workdir, and each probe spawns a subprocess.
    """
    if _ENV_BLOCK_CACHE:
        return _ENV_BLOCK_CACHE[0]
    from spark.tools.shell import detect_env  # deferred: tools pull in sandbox/config

    detected = detect_env(workdir)
    runtime_text = (
        ", ".join(f"{name} {version}" for name, version in sorted(detected.items()))
        or "none detected on PATH"
    )
    block = (
        "## Detected environment\n"
        f"Platform: {sys.platform}. Use the runtimes listed below with exactly these "
        f"names; do not assume a POSIX toolchain. Detected: {runtime_text}"
    )
    _ENV_BLOCK_CACHE.append(block)
    return block


MODE_HINTS = {
    "sandbox-only": "Access mode: sandbox-only. You can read files inside the workdir and plan, but shell execution and file writes are disabled. Focus on analysis, exploration and recommendations.",
    "workspace": "Access mode: workspace. You may read/write files and run shell commands inside the workdir only. Operations escaping the workdir or touching protected paths (including Spark's own source tree and its config under ~/.spark) are blocked.",
    "full-access": "Access mode: full-access. You may operate beyond the workdir, except protected system paths and the Spark agent's own code. Use with care.",
    "unrestricted": "Access mode: unrestricted. All safeguards are off, including protection of Spark's own code and configuration. Any operation is permitted. Be deliberate: mistakes here can damage the agent itself.",
}


_SYSTEM_PROMPT_CACHE: dict[tuple, tuple[float, str]] = {}
_SYSTEM_PROMPT_TTL_SEC = 30.0


def _system_prompt_key(workdir: Path, cfg: SparkConfig) -> tuple:
    return (
        str(Path(workdir).resolve()),
        str(getattr(cfg.provider, "name", "")),
        str(getattr(cfg.agent, "sandbox_mode", "")),
        getattr(cfg.agent, "system_prompt", "") or "",
        str(cfg.context.agents_md or ""),
        bool(getattr(cfg.context, "repo_map_enabled", True)),
        int(getattr(cfg.context, "repo_map_max_chars", 0) or 0),
    )


def invalidate_system_prompt_cache() -> None:
    """写操作/配置变更后调用：下次取用重算（含仓库地图、技能与子代理发现）。"""
    _SYSTEM_PROMPT_CACHE.clear()


def system_prompt(workdir: Path, cfg: SparkConfig) -> str:
    """The single system message: base prompt + workdir + environment + mode + skills.

    Shared by :func:`build_messages` and :func:`history_token_usage` so the token
    estimate can never be computed against a different prompt than the one sent.

    按 (workdir, 沙盒模式, 自定义提示, agents_md, repo_map 配置) 缓存，30 秒
    TTL；写工具执行后由 :class:`~spark.core.loop.AgentLoop` 主动调
    :func:`invalidate_system_prompt_cache`，避免每个工具回环都全仓重扫。
    """
    key = _system_prompt_key(workdir, cfg)
    now = time.monotonic()
    hit = _SYSTEM_PROMPT_CACHE.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    text = _build_system_prompt(workdir, cfg)
    _SYSTEM_PROMPT_CACHE.clear()  # 单条目缓存：workdir/配置切换时不膨胀
    _SYSTEM_PROMPT_CACHE[key] = (now + _SYSTEM_PROMPT_TTL_SEC, text)
    return text


def _build_system_prompt(workdir: Path, cfg: SparkConfig) -> str:
    local_provider = str(getattr(cfg.provider, "name", "")) == "llama_cpp"
    # 用户自定义系统提示词：cfg.agent.system_prompt 非空时覆盖默认 prompts/system.md
    custom_prompt = getattr(cfg.agent, "system_prompt", "") or ""
    if custom_prompt.strip():
        base = custom_prompt.strip()
    elif local_provider:
        base = load_local_system_prompt().strip()
    else:
        base = load_system_prompt()
    text = base + f"\n\nWorkdir: {workdir.resolve()}"
    text += f"\n\n{environment_block(workdir)}"
    mode_hint = MODE_HINTS.get(getattr(cfg.agent, "sandbox_mode", "workspace"))
    if mode_hint:
        text += f"\n{mode_hint}"
    if local_provider:
        # 1B 级模型对长提示中的技能目录、子代理清单和仓库地图不敏感，反而
        # 容易忽略“先调用工具”的主指令；保留工具本身，砍掉这些发现型区块。
        return text
    text = inject_skills(text, discover_skills(workdir))
    # P1-4: 声明式子代理（.claude/agents/*.md，文件即 subagent）
    from spark.agents import discover_declared_agents, inject_subagents

    text = inject_subagents(text, discover_declared_agents(workdir))
    # P0-10: repo-map 代码索引（aider 范式）——树里符号定位，减少全仓读取
    if getattr(cfg.context, "repo_map_enabled", True):
        try:
            from spark.repo_map import RepoMapConfig, build_repo_map

            repo_map = build_repo_map(
                workdir,
                RepoMapConfig(max_chars=cfg.context.repo_map_max_chars),
            )
            if repo_map:
                text += f"\n\n{repo_map}"
        except Exception:
            # 索引失败不影响主流程：静默降级（地图是增强，不是依赖）
            logger.debug("repo_map generation failed", exc_info=True)
    return text


def trim_recent(recent: list[ChatMessage], budget_chars: int) -> list[ChatMessage]:
    """Char-budget trimming from the newest message backwards.

    Budget is counted in characters (``context.history_budget_chars``), per
    message: body length plus serialised tool-call arguments. The newest
    message is always kept so a turn is never handed an empty history.

    Lives here rather than in ``core.compact`` so building messages never
    imports the summariser stack (litellm + llama_index cost ~1.7 s at import).
    """
    kept: list[ChatMessage] = []
    used = 0
    for msg in reversed(recent):
        size = len(msg.content or "") + sum(
            len(str(c.arguments)) for c in (msg.tool_calls or [])
        )
        if used + size > budget_chars and kept:
            break
        kept.append(msg)
        used += size
    kept.reverse()
    return kept


def build_messages(
    *,
    workdir: Path,
    cfg: SparkConfig,
    history: list[ChatMessage],
    memory_block: str | None = None,
    _compactor=None,
) -> list[ChatMessage]:
    """Build the list of messages sent to the provider.

    Budget trimming 走 Compactor 的字符预算实现（口径 =
    ``context.history_budget_chars``）；``_compactor`` 参数仅为向后兼容
    保留（可传实例或 None）。
    """
    messages: list[ChatMessage] = [ChatMessage(role="system", content=system_prompt(workdir, cfg))]
    agents = load_agents_md(workdir, cfg)
    if agents:
        messages.append(
            ChatMessage(
                role="user",
                content=f"<project_instructions>\n{agents}\n</project_instructions>",
            )
        )
    if memory_block:
        messages.append(ChatMessage(role="user", content=memory_block))
    rest = [m for m in history if m.role != "system"]

    summaries = [m for m in rest if m.role == "summary"]
    recent = [m for m in rest if m.role != "summary"]

    if _compactor is not None:
        kept = _compactor.trim_recent(recent, cfg.context.history_budget_chars)
    else:
        kept = trim_recent(recent, cfg.context.history_budget_chars)

    kept = _collapse_old_images(kept)
    messages.extend(summaries)
    messages.extend(kept)
    return messages


def _collapse_old_images(kept: list[ChatMessage]) -> list[ChatMessage]:
    """Keep images only on the newest image message; older ones become text placeholders to save tokens."""
    last = -1
    for i, m in enumerate(kept):
        if m.images:
            last = i
    out: list[ChatMessage] = []
    for i, m in enumerate(kept):
        if m.images and i != last:
            out.append(
                ChatMessage(
                    role=m.role,
                    content=f"[image attached earlier]\n{m.content or ''}",
                    tool_calls=m.tool_calls,
                    tool_call_id=m.tool_call_id,
                    name=m.name,
                )
            )
        else:
            out.append(m)
    return out


def history_token_usage(
    *,
    cfg: SparkConfig,
    history: list[ChatMessage],
    workdir: Path,
    tool_overhead_tokens: int = 0,
) -> dict:
    """Estimate total request size: system + agents.md + memory slot + history + tool schemas."""
    system = system_prompt(workdir, cfg)
    total = estimate_message_tokens(ChatMessage(role="system", content=system))
    agents = load_agents_md(workdir, cfg)
    if agents:
        total += estimate_message_tokens(ChatMessage(role="user", content=agents))
    total += 512  # memory block slot
    total += sum(estimate_message_tokens(m) for m in history if m.role != "system")
    total += tool_overhead_tokens
    return {
        "used": total,
        "limit": cfg.context.max_context_tokens,
        "percent": round(total * 100 / max(1, cfg.context.max_context_tokens), 1),
    }
