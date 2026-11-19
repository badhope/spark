# ACP 接入（Zed / JetBrains / Neovim）

Spark 以 [Agent Client Protocol](https://agentclientprotocol.com)（ACP）agent 的身份
被编辑器拉起。ACP 由 Zed 发起，opencode / Gemini CLI 等均已接入；JetBrains 2025.3+
原生支持 ACP，Neovim 通过 CodeCompanion / avante / agentic.nvim 支持。

**结论：不写 JetBrains 专属插件，也不写 Neovim 专属插件。** 编辑器自己负责聊天 UI、
diff 审批和文件系统权限，Spark 只提供 `spark acp` 这个标准 agent。

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

## 在 JetBrains 中接入（2025.3+）

一键写入 `~/.jetbrains/acp.json`（已有配置会先备份，其他 agent 与 `default_mcp_settings` 保留）：

```bash
spark ide install jetbrains --workdir /path/to/your/project
```

重启 IDE 后，在 **AI Chat → 配置 ACP Agent** 中选择 **Spark**。JetBrains 25.3+
原生提供 ACP 聊天面板，审批与 diff 由 IDE 自己呈现，不需要 JetBrains AI 订阅。

手动配置等价于：

```json
{
  "agent_servers": {
    "Spark": {
      "command": "spark",
      "args": ["acp", "--workdir", "/path/to/your/project"],
      "env": {}
    }
  }
}
```

## 在 Neovim 中接入（CodeCompanion）

先安装 [codecompanion.nvim](https://github.com/olimorris/codecompanion.nvim)，再生成独立模块：

```bash
spark ide install neovim --workdir /path/to/your/project
```

它只写 `~/.config/nvim/lua/spark_acp.lua`，**不会改你的 `init.lua`**；在配置里加入：

```lua
require("spark_acp").setup()
```

模块内部复用 CodeCompanion 的 ACP 适配器，`commands.default` 为
`{ "spark", "acp", "--workdir", "<项目>" }`。如果已用 avante.nvim，可手动加：

```lua
opts = {
  provider = "spark",
  acp_providers = {
    spark = { command = "spark", args = { "acp", "--workdir", "/path/to/your/project" } },
  },
}
```

## 常用参数

| 参数 | 说明 |
|---|---|
| `--workdir <path>` | 会话工作目录（ACP `cwd` 的一致性校验基准） |
| `--approval <mode>` | 审批档：`suggest` / `auto-edit` / `full-auto` / `plan-only` / `approve-plan` |
| `--provider <name>` | 模型供应商（8 种：`openai_compat` / `openai` / `anthropic` / `gemini` / `azure` / `bedrock` / `ollama` / `mock`） |
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
