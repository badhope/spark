# ACP 接入（Zed 等 IDE）

Spark 可以以 [Agent Client Protocol](https://agentclientprotocol.com)（ACP）agent 的身份
被编辑器拉起——协议由 Zed 发起，opencode / Gemini CLI 等均已接入。接 ACP = 免费进 IDE，
Spark 自己不写任何编辑器插件。

## 在 Zed 中接入

一键写入（已有 `settings.json` 会先备份，未知键保留）：

```bash
spark ide install zed --workdir /path/to/your/project
```

也可以手动编辑 `~/.config/zed/settings.json`：

```json
{
  "agent": {
    "custom_servers": {
      "spark": {
        "command": "spark",
        "args": ["acp", "--workdir", "/path/to/your/project"]
      }
    }
  }
}
```

或在对话面板中用 `agent: add custom server` 命令添加。之后在 Agent Panel 里选择 Spark 即可。

## 常用参数

| 参数 | 说明 |
|---|---|
| `--workdir <path>` | 会话工作目录（ACP `cwd` 的一致性校验基准） |
| `--approval <mode>` | 审批档：`suggest` / `auto-edit` / `full-auto` / `plan-only` / `approve-plan` |
| `--provider <name>` | 模型供应商（`openai_compat` / `ollama` / `mock`） |
| `--model <name>` | 模型名 |
| `--sandbox-mode <mode>` | 沙箱档：`sandbox-only` / `workspace` / `full-access` / `unrestricted` |

## 协议映射速查

| Spark 概念 | ACP 表达 |
|---|---|
| 审批六档 | `modes`（session/new 返回，Zed 可切档） |
| 工具审批门 | `session/request_permission`（allow_once / allow_always / reject_once） |
| 文本流 | `session/update` → `agent_message_chunk` |
| 思考流 | `session/update` → `agent_thought_chunk` |
| 工具调用 | `session/update` → `tool_call` / `tool_call_update` |
| 计划 | `session/update` → `plan`（update_plan 工具驱动） |
| 取消 | `session/cancel` → 协作式取消，回 `stopReason: cancelled` |
| 历史回放 | `session/load`（user/assistant 文本逐条回放） |

## 会话与存储

ACP 会话与 Web / TUI 会话共用同一个 `sessions.db`：IDE 里聊的轮次，
`spark sessions` / `spark resume` 一样可见、可续。

## 限制（当前版本）

- 仅 stdio 传输；Streamable HTTP 传输在 P2 后续批次。
- 客户端传入的 ACP 代理型 MCP server（`type: "acp"`）被忽略并记 warning。
- 图片 prompt 已公告能力（`promptCapabilities.image`），依赖 provider 侧多模态支持。
