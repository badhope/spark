# Spark 全能力审计（2026-11-21）

## 结论

**不能把“全部功能都测过了”说成事实。**

当前可以确认的是：**本机可真实调用的核心功能已经逐项跑过**，包括 59 个内置工具、真实本地模型、Web/API、CLI、ACP、A2A、MCP、relay、PTY、队列、定时任务、检查点和 OTel 本地 collector。剩下的缺口集中在**外部凭据、外部服务、真实 IDE/桌面环境和可选依赖**，不是靠再点几下 UI 就能补齐的。

本文件只记录**本轮真实执行过**的能力；测试通过只作为回归证据，不替代功能验收。

## 验收标记

| 标记 | 含义 |
| --- | --- |
| ✅ 真实通过 | 本轮真实 HTTP / CLI / 子进程 / 真实模型调用，看到预期结果 |
| 🟡 部分通过 | 主路径真实通过，但存在空结果、降级或外部依赖未闭合 |
| ⛔ 受阻 | 缺凭据、缺外部服务、缺桌面/IDE 环境，或属于破坏性操作 |
| ❌→✅ | 发现真实缺陷，已修复并复验 |

## 一、本轮真实通过的核心能力

### 1. 真实模型与工具调用

| 能力 | 证据 | 状态 |
| --- | --- | --- |
| MiniCPM5 本地模型对话 | `POST /api/chat`，13.6s，返回 `LOCAL-API-OK` | ✅ |
| 真实模型触发 `write_file` | 27.8s，`tool_start/tool_end` 成功，`checkpoint.txt` 落盘 5 bytes，内容 `CP-OK` | ✅ |
| 检查点回滚 | `/api/checkpoints` 返回 checkpoint `1`；rollback 恢复 1 个文件；再回滚删除新增 `after.txt`，保留 `checkpoint.txt` | ✅ |
| 审批挂起/放行 | `approval_needed` 带 `session_id/turn_id/tool`；`allow` 后继续 `tool_start → tool_end`，`approval.txt` 落盘 18 bytes | ✅ |
| 审批后取消 | 模型再次请求 `run_tests` 时 `POST /api/cancel` 200，流收到 `turn_error: Turn cancelled` 与 `done` | ✅ |
| 提问/回答 | 真实 `question` 事件后 `POST /api/question/answer {"answer":"blue"}` 200，`tool_end` 返回 `{"answer":"blue"}` | ✅ |
| 模型档案保存/激活/删除 | `/api/models/save`、`/api/models/activate`、`/api/models/delete` 200；provider 在 `mock` 与 `llama_cpp` 间真实切换 | ✅ |
| 本地模型下载入口 | `POST /api/local-models/download` 对已安装 `minicpm5-1b` 返回 202 `ready` | ✅ |

小模型本身有两个行为问题，不属于接口故障：会把 `ask_user` 的 options 传成字符串、会重复调用同一个工具；审批/取消/循环保护能兜住。

### 2. 59 个内置工具

`ToolRegistry.schemas()` 枚举到 **59 个工具**。本轮用 `ToolRegistry.execute()` 在 `/tmp/spark-tool-work` 逐个发起真实调用，不走模型；`task` 另用 `AgentLoop._run_tool` 实测。

| 工具组 | 实测结果 |
| --- | --- |
| 文件与搜索：`read_file`、`list_dir`、`write_file`、`apply_patch`、`apply_edit`、`grep`、`glob`、`diagnostics` | ✅ 全部成功，生成/修改/读取真实文件 |
| Shell/Git/测试：`run_shell`、`git_status`、`git_diff`、`git_log`、`git_branch`、`git_add`、`git_commit`、`run_tests` | ✅ 全部成功；`run_tests` 真实跑到 `1 passed` |
| 代码理解：`find_symbol`、`affected_tests` | ✅ 成功 |
| LSP：`lsp_definition`、`lsp_references`、`lsp_hover`、`lsp_symbols`、`lsp_diagnostics`、`lsp_rename`、`lsp_rename_symbol`、`lsp_code_action` | ✅ 安装真实 `pylsp`；返回 `source: "lsp"`、`degraded: false`；`rename` 真实把 `greet` 改成 `welcome` |
| 浏览器：`browser_open`、`browser_click`、`browser_type`、`browser_scroll`、`browser_wait`、`browser_screenshot`、`browser_viewport` | ✅ 用 `file:///tmp/spark-tool-work/page.html` 实测；点击后文本变 `clicked`，截图落盘。本机 `127.0.0.1` 被 SSRF 保护拒绝属预期 |
| 后台任务：`bg_start`、`bg_list`、`bg_output`、`bg_kill` | ✅ 全部成功 |
| Notebook：`read_notebook`、`notebook_edit` | ✅ `demo.ipynb` 第 2 个 cell 从 `print(1)` 改为 `print(2)` |
| 交互/计划：`ask_user`、`update_plan`、`load_skill`、`goal_update` | ✅ 异步回调返回 `blue`；`.spark/skills/demo.md` 成功加载；`goal_update` 接 `SessionStore` 后以 evidence 关闭目标 |
| Git worktree/task：`worktree_create`、`worktree_list`、`worktree_remove`、`task_begin`、`task_step_commit`、`task_finish` | ✅ 创建 `/tmp/spark-tool-work/audit-wt`；`task/audit-task` 真实提交；`task_finish success=false` 删除 worktree 和分支 |
| 子代理：`task` | ✅ 经 `AgentLoop._run_tool` 调 mock provider 子代理，返回 `I am Spark (mock provider)...` |
| MCP 适配层：`mcp_resources/list`、`mcp_resources/read`、`mcp_prompts/list`、`mcp_prompts/get` | ✅ 用真实 FastMCP server 验证 resources/prompts；读到 `REAL_RES_DATA`/`REAL_PROMPT_TEXT` |
| PR：`pr_checks`、`pr_context`、`pr_view`、`pr_diff`、`pr_review` | 🟡 `pr_checks` 返回 200 但检查列表为空；其余 4 个无 GitHub token，被公共 API 403/rate limit 阻断 |

核对结果文件：`/tmp/tool-audit-results.json`、`/tmp/tool-audit-results2.json`、`/tmp/tool-audit-results3.json`；脚本 `/tmp/tool_audit*.py`。`/tmp` 不是长期证据库，关键结论已回写到本文件。

### 3. Web/API

| 能力 | 证据 | 状态 |
| --- | --- | --- |
| 真实会话新建/改名/fork/删除 | 正确字段 `session_id` 下均 200；fork 返回新会话 | ✅ |
| SSE 对话 | `/api/chat/stream` 收到 `context → text_delta → turn_end → done` | ✅ |
| 会话历史/时间线/文件树/文件读取 | `/api/history`、`/api/timeline`、`/api/fs/tree`、`/api/fs/read` 200，`checkpoint.txt` 读回 `CP-OK` | ✅ |
| 内置终端 PTY | open/write/resize/stream/close 200；SSE 收到 `PTY_OK`，cwd 为 workdir | ✅ |
| 空闲队列 | `action=create` 入队，真实执行并返回 `QUEUE_OK`，状态 `ok` | ✅ |
| 定时任务 | create/list/update/toggle/run_now/delete；`run_now` 真实出现 `last_status=ok` 与会话 | ✅（本轮修缺陷） |
| 技能 | list/save/read/delete 200；无 frontmatter 返回 422 提示，不再静默成功 | ✅ |
| 系统提示词/命令模板 | `save_system` 写入后读回，再恢复原值；`save_command`/`list`/`delete_command` 200 | ✅ |
| Wiki | status/generate/read/delete 200；生成 1 module，INDEX 493 chars | ✅ |
| 知识库 | add `checkpoint.txt`、search `CP-OK` 命中、delete 清空 | ✅ |
| 审批档案 | save/activate/delete 200；激活后重建 loop | ✅ |
| 插件发现/开关 | list/enable/disable 真实 HTTP 200；插件执行权限仅本机 | ✅ |
| 诊断/自愈/路由/成本/调试 | `/api/selfheal` 200；`/api/status`、`/api/config` 等真实读取 | ✅ |
| Shell 面板 | `/api/shell` 执行 `echo WEB_SHELL_OK`，exit 0 | ✅ |
| 模型/本地模型列表 | `/api/models`、`/api/local-models` 200 | ✅ |
| 设备配对 | code/claim/devices 200；设备令牌真实用于 relay | ✅ |
| UI 回归 | 既有真实浏览器基线 37 passed；本轮代码修复未触碰前端结构，最终全量回归覆盖 UI 测试 | 🟡 |
| 桌面 WebView | `spark desktop` 实测失败：缺少 `pywebview` | ⛔ |
| 企业控制面 | `spark enterprise` 8s 内无监听，缺 PostgreSQL/Keycloak 运行环境 | ⛔ |

### 4. CLI

| 命令 | 实测结果 | 状态 |
| --- | --- | --- |
| `version` / `--version` | 0.15.2 | ✅ |
| `init` | 隔离配置写入成功 | ✅ |
| `doctor` | 工作目录/审批/OTel 提示正常；本地 `llama_cpp` 已被识别为“本地模型已就绪”，不再误报 mock | ✅ |
| `exec`（mock） | 文本、`--json` NDJSON 均成功 | ✅ |
| `exec`（本地 GGUF） | 显式与默认 provider 均返回 `CLI_LOCAL_OK` / `CLI_DEFAULT_OK`；此前失败是隔离目录被测试清理、GGUF 软链丢失 | ✅ |
| `providers` / `route` / `sessions` | 正常输出；`route` 显示 offline/ollama 回退 | ✅ |
| `config show/path/set` | 写入 `agent.max_tool_rounds=12` 后回读 12 | ✅ |
| `queue list` | 正常 | ✅ |
| `wiki` | 3 文件/2 符号，生成 INDEX.md | ✅ |
| `sbom` | CycloneDX 1.5，255 components | ✅ |
| `replay` / `context-profile` | 单 turn JSON；1710/32768 tokens 分类占比 | ✅ |
| `review` | 假 OpenAI 端点返回结构化 JSON | ✅ |
| `models` / `test` | 在线目录；假端点 2 轮通过 | ✅ |
| `agent list` / `profile list` | 空态正常；Web API 已真实保存/激活/删除档案 | ✅ |
| `mcp list/search/add/remove` | 在线搜到 filesystem server，add/list/remove 全链路成功 | ✅ |
| `plugins install/list/enable/disable/remove` | 本地 official 插件全链路成功 | ✅ |
| `workflow run` | mock 下 1-step 真实执行，`workflow_end ok=true`，state JSON 落盘 | ✅ |
| `ide install zed/jetbrains/neovim` | 三份配置生成成功 | ✅ |
| `web` | 8877 真实服务，真实模型 API | ✅ |
| `mcp-serve` | stdio 与 streamable HTTP 均被真实 MCP 客户端连接，58 tools，`read_file` 返回 `MCP_REAL_OK` | ✅ |
| `acp` | 真实子进程：initialize、new_session、prompt，`end_turn` | ✅ |
| `a2a-serve` | Agent Card、health、message/send、tasks/get、message/stream 四帧 | ✅ |
| `relay serve/connect/call` | 补设备令牌后 `HTTP 200`，返回真实 `/api/status` | ✅ |
| `desktop` | 缺 `pywebview` | ⛔ |
| `enterprise` | 缺 PostgreSQL/Keycloak，8s 无监听 | ⛔ |
| `update --check` | 发布源 403 rate limit | ⛔ |
| `uninstall` | 破坏性操作，未执行 | ⛔ |
| `resume` | 真实 PTY（30×100）启动 TUI，历史会话 `say hi` 已加载；发现 Ctrl-D 被输入框的 delete_right 吞掉，已改为 priority 绑定并验证可退出 | ✅ |

### 5. 协议与外部集成

| 能力 | 证据 | 状态 |
| --- | --- | --- |
| A2A | 真实 HTTP 服务 41242；`message/send` 返回 completed artifact；`tasks/get` 正常；`message/stream` 返回 working → artifact×2 → completed | ✅ |
| ACP | 真实 `spark acp` 子进程，协议版本 1，session 创建，prompt 返回 mock 文本 | ✅ |
| MCP server 暴露 | stdio/HTTP 两种传输，58 tools，真实 `read_file` | ✅ |
| MCP 客户端 | 现有 138 项定向测试；真实 MCP server 已作为服务端验收；真实 resources/prompts 客户端链路通过 | ✅ |
| relay | 端到端 200；401 是上游控制令牌缺失，补设备令牌后通过 | ✅ |
| OTel | 本机起真实 OTLP gRPC collector，收到 `spark.turn` span | ✅ |
| 插件子进程/hook | 单测覆盖；本地安装启用通过，未做人工 hook 触发 | 🟡 |
| PR 写/publish | 无 token，未做 | ⛔ |
| VS Code Extension Host | 无 Code/Extension Host，仅配置生成 | ⛔ |
| JetBrains/Neovim 真实 IDE | 配置生成成功；无 IDE 客户端 | 🟡 |
| 企业控制面 | 无 PostgreSQL/Keycloak | ⛔ |
| 会议转写 | `faster_whisper` 未安装（ModuleNotFoundError） | ⛔ |
| 外部 OTel/Langfuse | 本机 collector 已过；未接真实 Langfuse | 🟡 |

## 二、本轮修掉的真实缺陷

### 1. 命令列表 Path 序列化

`discover_commands()` 返回 `Path`，`routes._json()` 的 `json.dumps` 无法序列化，handler 在写响应前抛 `TypeError`，连接被关闭。已改为相对 workdir 的 POSIX 字符串，并加回归断言；真实 HTTP 从断连变为 200。提交 `85da14e`。

### 2. 定时任务“立即运行”永远不执行

`run_now` 原来把 `next_run_at` 设为 `0`；`AutomationScheduler.run_due()` 把 `0` 解释为“新建任务尚未排期”，只补算下一次 cron，任务永不出队。已改为 `time.time() - 1`（非零过去值），并更新回归断言。

- 定向回归：`tests/test_automations_web.py` **17 passed**。
- 真实复验：`run_now` 后任务出现 `last_status=ok` 和真实执行会话。
- 修复已提交 `0674fa0`。

## 三、仍然没有测/没有闭合的项

1. **商业模型 Key**：DeepSeek/Qwen/Kimi/GLM/豆包/OpenAI/Anthropic 均未发真实请求；只有本地 GGUF 与假 OpenAI 端点。
2. **PR 写与只读**：4 个读工具 403，`pr_checks` 空结果，publish 无 token。
3. **企业控制面**：PostgreSQL + Keycloak + Casbin + 全格式导出未起真实环境。
4. **真实 IDE**：VS Code Extension Host、JetBrains 2025.3、Neovim 客户端未拉起。
5. **桌面**：`pywebview` 未安装，且当前无桌面会话。
6. **外部可观测后端**：本机 OTLP gRPC collector 已通；真实 Langfuse/Tempo 未接。
7. **会议转写**：`faster-whisper` 未安装，真实音频未跑。
8. **插件 hook 人工触发**：单测覆盖，未在真实插件上手工触发 hook。
9. **更新/卸载**：发布源 403；卸载属破坏性操作。
10. **UI**：全量回归已覆盖 UI/浏览器测试；独立 UI 基线 37 passed。

## 四、验收口径

如果“全部功能”指**本机可执行的核心路径 + 全部内置工具 + 本机协议服务**：当前可以给真实通过证据，但 PR/商业模型/企业/IDE/桌面/会议转写必须保留 ⛔。

如果指**字面每一个外部集成、每一个工具、每一个平台都真实跑过**：还缺上面的 10 项，不能宣称 100%。

补齐顺序建议：

1. 给 PR 只读与 publish 配测试 token。
2. 起 PostgreSQL/Keycloak 测企业控制面。
3. 准备真实 IDE 与桌面会话。
4. 装 `faster-whisper` + 音频样本。
5. 接真实 Langfuse collector。

## 五、最终回归

- 全量 pytest：**1761 passed, 13 skipped, 14 warnings in 1119.94s (18:39)**。
- 覆盖 UI/浏览器、协议、工具、Web、CLI 模块测试；不代表外部集成已验收。
- 定向定时任务回归：`tests/test_automations_web.py` **17 passed**。
- 本轮代码改动：命令列表 Path 修复已提交 `85da14e`；`run_now` 修复已提交 `0674fa0`；本文件随附。
- 全量回归日志：`../spark-full-final2.log`（仓库外层）。

该文件不把 pytest 数量当作功能完成证明。
