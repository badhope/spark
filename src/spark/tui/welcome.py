from __future__ import annotations

import os

from spark.config import SparkConfig, mask_secret

EXAMPLES = [
    "读取 README.md 并用中文总结这个项目做什么",
    "跑一遍测试，把失败的用例修好",
    "搜索所有调用 xxx 的地方并说明调用链",
    "给 src/ 下的模块补一份架构说明文档",
]

SHORTCUTS = [
    ("Enter", "发送任务"),
    ("Ctrl+T", "测试模型连通性"),
    ("Ctrl+C", "中止当前任务"),
    ("Ctrl+L", "清空输出区"),
    ("Ctrl+R", "切换会话"),
    ("y/n/a", "审批: 允许/拒绝/始终允许"),
    ("Esc", "关闭弹窗 / 拒绝审批"),
    ("?", "打开/关闭本帮助"),
    ("Ctrl+D", "退出"),
]


def key_status(cfg: SparkConfig) -> tuple[str, str]:
    """Return (marker, label) describing whether credentials are usable.

    Must mirror :func:`spark.config.require_api_key`: the environment variable is
    only consulted when ``provider.api_key_env`` is bound to an actual name. The
    old code also fell back to ``SPARK_API_KEY`` here, so an unbound config
    showed "OK" while ``require_api_key`` raised and the agent silently ran on
    the mock provider.
    """
    if cfg.provider.name == "mock":
        return "!", "mock 模型（未接真实模型）"
    if cfg.provider.api_key:
        return "OK", f"密钥已配置 {mask_secret(cfg.provider.api_key)}"
    if cfg.provider.name == "ollama":
        return "OK", "Ollama 本地模型无需密钥"
    if cfg.provider.api_key_env and os.environ.get(cfg.provider.api_key_env):
        return "OK", f"密钥来自环境变量 {cfg.provider.api_key_env}"
    return "X", "缺少 API Key（按 ? 查看如何配置）"


def status_text(cfg: SparkConfig, session_id: str, workdir: str = "") -> str:
    marker, _key_label = key_status(cfg)
    parts = [
        f"模型 {cfg.provider.model}",
        f"接口 {cfg.provider.base_url}",
        f"密钥 {marker}",
        f"审批 {cfg.agent.approval}",
        f"访问 {cfg.agent.sandbox_mode}",
    ]
    if workdir:
        parts.append(f"目录 {workdir}")
    parts.append(f"会话 {session_id[:8]}")
    return "  |  ".join(parts)


def welcome_text(cfg: SparkConfig, workdir: str = "") -> str:
    marker, key_label = key_status(cfg)
    lines = [
        "[bold #ff7a3d]◆ Spark[/bold #ff7a3d] [dim]Ember[/dim] — 本地编程智能体",
        "",
        f"[bold]模型[/bold]  {cfg.provider.model}",
        f"[bold]接口[/bold]  {cfg.provider.base_url}",
        f"[bold]密钥[/bold]  {key_label}",
        f"[bold]审批[/bold]  {cfg.agent.approval}    [bold]访问[/bold]  {cfg.agent.sandbox_mode}",
    ]
    if workdir:
        lines.append(f"[bold]目录[/bold]  {workdir}")
    lines += [
        "",
        "[bold]可以这样说：[/bold]",
    ]
    lines += [f"  · {item}" for item in EXAMPLES]
    if marker == "X":
        lines += [
            "",
            "[bold red]尚未配置 API Key，模型无法调用。[/bold red]",
            "  在终端运行 [bold]spark init[/bold] 填写，或按 [bold]?[/bold] 查看完整说明。",
        ]
    elif marker == "!":
        lines += [
            "",
            "当前是 mock 模型，不会真实调用大模型。运行 [bold]spark init[/bold] 切换到真实模型。",
        ]
    lines += ["", "按 [bold]?[/bold] 查看快捷键与更多示例。"]
    return "\n".join(lines)


def help_text() -> str:
    lines = ["[bold #ff7a3d]◆ Spark 使用帮助[/bold #ff7a3d]", ""]
    width = max(len(key) for key, _ in SHORTCUTS)
    lines += [f"  {key.ljust(width)}   {desc}" for key, desc in SHORTCUTS]
    lines += [
        "",
        "[bold]任务示例[/bold]",
        "",
    ]
    lines += [f"  · {item}" for item in EXAMPLES]
    lines += [
        "",
        "[bold]常用命令[/bold]",
        "",
        "  spark web              打开浏览器图形界面（推荐新手）",
        "  spark doctor           体检配置并给出修复建议",
        "  spark init             首次配置模型与密钥",
        "  spark config show      查看当前配置（密钥自动打码）",
        "  spark sessions         列出历史会话",
        "  spark resume <id>      继续某个历史会话",
        "",
        "按 [bold]?[/bold] 或 [bold]Esc[/bold] 关闭",
    ]
    return "\n".join(lines)


def error_hint(text: str) -> str:
    """Append an actionable hint for common failures."""
    lowered = (text or "").lower()
    if "api key" in lowered or "api_key" in lowered or "401" in lowered:
        return "提示：运行 spark init 重新配置密钥，或 spark doctor 查看诊断。"
    if "connection" in lowered or "timeout" in lowered or "网络" in text:
        return "提示：检查 base_url 是否可达，或稍后重试。"
    if "circuit breaker" in lowered:
        return (
            "提示：模型在重复调用同一工具，已自动中止。可调大 agent.max_repeat_calls。"
        )
    if "token budget" in lowered:
        return "提示：本轮 token 预算用尽。可调大 agent.max_turn_tokens 或拆小任务。"
    if "max_tool_rounds" in lowered:
        return "提示：达到工具轮数上限。可调大 agent.max_tool_rounds 或拆小任务。"
    return "提示：运行 spark doctor 查看完整配置诊断。"
