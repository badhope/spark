# OpenCode 兼容层

> 目标：让团队已有的 OpenCode 资产直接进入 Spark 的编程主链路，而不是复制成第二套提示词。
> 入口：`spark opencode --workdir .`；代码：`src/spark/opencode_compat.py`；测试：`tests/test_opencode_compat.py`。

## 1. 支持范围

| OpenCode 资产 | Spark 落点 | 行为 |
|---|---|---|
| `.opencode/agents/**/*.md` | 声明式子 Agent（`task` 工具可调用） | Markdown body 变 system prompt；文件名/相对路径变 agent ID |
| `.opencode/commands/*.md` | 自定义 slash 命令（`/name`） | body 变模板；`$ARGUMENTS` 替换 |
| `opencode.json(c)` 的 `agent` / `agents` | 声明式子 Agent | 支持 `prompt` / `system`、`{file:...}` 内联文件 |
| `opencode.json(c)` 的 `command` / `commands` | 自定义 slash 命令 | `template` 必填；文件命令优先于内联命令 |
| `permission` / `permissions` | 子 Agent 工具 denylist | `edit` / `bash` / `webfetch` / `read` 的 `deny` 映射到 Spark 工具名；支持 `*` 与 `browser_*` 通配 |
| `tools` | 子 Agent 工具 denylist | `write: false` / `edit: false` / `bash: false` 等映射 |
| `mcp` | `spark opencode --json` 输出 | 读取并归一化，当前不自动写入 Spark MCP 配置 |
| `model` / `small_model` / `default_agent` / `instructions` | 项目状态输出 | 保留可见，不静默丢弃 |

## 2. 使用

```bash
# 看当前项目读到了什么
spark opencode --workdir .

# 机器可读
spark opencode --workdir . --json

# OpenCode 的 reviewer 子 Agent 直接在 Spark 里被 task 调用
# （主 Agent 看到 <available_subagents> 后自行选择）
```

只要仓库里有 `.opencode/agents/reviewer.md`，Spark 的 `task` 工具就可以用 `{"agent": "reviewer", "prompt": "..."}` 调用它；不需要改文件位置。

## 3. 权限映射

| OpenCode action | Spark denylist |
|---|---|
| `edit: deny` | `write_file`, `apply_patch`, `apply_edit`, `notebook_edit` |
| `bash: deny` | `run_shell`, `bg_*`, `pty_*` |
| `webfetch: deny` | `web_fetch`, `web_search`, `browser_*` |
| `read: deny` | `read_file`, `read_notebook` |
| `*: deny` | `*`（全部工具） |

映射在**子 Agent 启动时**执行：`SubagentRunner` 用实际注册的工具 schema 求差集，不依赖静态工具清单。这样新增工具不会绕过权限。

## 4. 明确不做的

- 不实现 OpenCode 的插件运行时；`plugin` 字段保留可见，不假装加载。
- 不实现 OpenCode 的 provider 注册表；Spark 仍走自己的 provider/factory。
- 不自动把 `mcp` 写进 Spark 配置；先输出，后续可加显式导入命令。
- 不把 `mode: primary` 变成主 Agent 替换；只作为可调用的声明式子 Agent，避免一个仓库配置劫持主循环。
- 不执行 OpenCode 模板里的 `` !`command` `` shell 注入；只做 `$ARGUMENTS` 文本替换。这是安全边界，不是功能缺失。

## 5. 验证

`tests/test_opencode_compat.py` 8 项：

- JSONC 注释、尾逗号、字符串内 `//` 与 `https://` 不被误删；
- Markdown agent 的 v1 `permission` 与 v2 `permissions` 都能映射 denylist；
- `.claude/agents` 与 `.opencode/agents` 同名时 Claude 优先；
- 文件命令优先于内联命令；
- `{file:...}` 内联提示词只在 config 目录内解析；
- 缺配置不抛异常，`spark opencode` 正常显示空状态。

## 6. 与核心链路的绑定点

1. **Agent 发现**：`agents.discover_declared_agents()` 合并 OpenCode agent，主 Agent 的 `<available_subagents>` 直接可见。
2. **命令发现**：`slash.discover_commands()` 合并 `.opencode/commands` 与内联 `command`，`expand_command()` 直接展开。
3. **权限执行**：`SubagentRunner.spawn_subtask()` / `spawn_subtasks_parallel()` 接收 `denied_tools`，按实际 schema 过滤。
4. **运行状态**：`spark opencode` 只读解析，不触发模型，不影响主链路。
