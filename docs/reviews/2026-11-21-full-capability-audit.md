# Spark 全能力审计（2026-11-21）

## 结论

**不能把“全部功能都测过了”说成事实。**

当前可以确认的是：**本机可真实调用的核心功能已经逐项跑过**，包括 59 个内置工具、真实本地模型、Web/API、CLI、ACP、A2A、MCP、relay、PTY、队列、定时任务、检查点和 OTel 本地 collector。剩下的缺口集中在**外部凭据、外部服务、真实 IDE 环境**；桌面窗口与会议转写已补跑并通过。

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
| PR：`pr_checks`、`pr_context`、`pr_view`、`pr_diff`、`pr_review` | ✅ 工具路径与 mock transport 通过；真实 API 需用户 token（未代发）。新增 `spark pr status` / `spark pr token` 入口与 0600 本机存储 |

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
| 桌面 WebView | Xvfb + Qt xcb 真实启动 pywebview 窗口，加载 Spark 首页并渲染欢迎弹窗，接口全部 200 | ✅ |
| 企业控制面（dev） | 真实启动（12s 就绪），health/me/project/task 200；会议与报告生成 md/docx/pptx/pdf 全部 200；member 写 403、租户隔离通过 | ✅ |

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
| `desktop` | 真实窗口 1280×832，首页/命令/静态资源均 200；Linux `desktop` extra 补齐 `pywebview[qt]`，缺后端时返回 2 并给安装提示 | ✅ |
| `enterprise` | 真实 dev 模式启动，SQLite 自动建表；health/me/project/task/会议/报告/审计均 200；生产 PostgreSQL/Keycloak 由用户部署 | ✅（dev） |
| `update --check` | 默认 GitHub 发布源 403 rate limit；更新源属外部环境，用户可走镜像/打包版 | 🟡 |
| `uninstall` | 一次性 `SPARK_HOME` 下 `--list` 清单正确、`--yes` 删除成功；`SPARK_HOME=/tmp` 在清点前被拒（退出码 2），守卫覆盖系统目录/家目录/当前目录 | ✅（本轮修缺陷） |
| `resume` | 真实 PTY（30×100）启动 TUI，历史会话 `say hi` 已加载；发现 Ctrl-D 被输入框的 delete_right 吞掉，已改为 priority 绑定并验证可退出 | ✅ |

### 5. 协议与外部集成

| 能力 | 证据 | 状态 |
| --- | --- | --- |
| 商业模型官方协议 | 本机假服务真实 `POST /v1/chat/completions`，SSE 返回 `PROTOCOL_OK`，流尾 usage 12 tokens 被 Spark 记录；LiteLLM 原生 adapter 覆盖 openai/anthropic/gemini/azure/bedrock；provider 预设/映射回归 23 passed；真实厂商 Key 由用户填 | ✅（协议已实跑） |
| A2A | 真实 HTTP 服务 41242；`message/send` 返回 completed artifact；`tasks/get` 正常；`message/stream` 返回 working → artifact×2 → completed | ✅ |
| ACP | 真实 `spark acp` 子进程，协议版本 1，session 创建，prompt 返回 mock 文本 | ✅ |
| MCP server 暴露 | stdio/HTTP 两种传输，58 tools，真实 `read_file` | ✅ |
| MCP 客户端 | 现有 138 项定向测试；真实 MCP server 已作为服务端验收；真实 resources/prompts 客户端链路通过 | ✅ |
| relay | 端到端 200；401 是上游控制令牌缺失，补设备令牌后通过 | ✅ |
| OTel | 本机起真实 OTLP gRPC collector，收到 `spark.turn` span | ✅ |
| 插件子进程/hook | 本地插件启用后，真实 `read_file` 触发 `pre_tool` 子进程 hook，落盘 payload `{"tool":"read_file","arguments":{"path":"note.txt"}}`，且 `plugin_hook_issues=[]` | ✅ |
| PR 写/publish | 命令有 `--confirm` 预览门槛；真实发布需用户 token，本轮未代发 | ✅（入口）/ 🟡（真实发布） |
| VS Code Extension Host | 用户确认不测第三方客户端；配置生成成功 | ✅（范围外） |
| JetBrains/Neovim 真实 IDE | 用户确认不测第三方客户端；配置生成成功 | ✅（范围外） |
| 企业控制面（生产） | SQLite dev 已实跑；PostgreSQL/Keycloak 由用户部署，生产 OIDC 未代测 | 🟡 |
| 会议转写 | 安装 faster-whisper + tiny 模型；espeak 生成真实语音，转写返回文本；`tests/test_control_audio.py` **5 passed**（含上传→转写→纪要） | ✅ |
| 外部 OTel/Langfuse | 本机 OTLP collector 已收到 span；`SPARK_OTEL_ENDPOINT` 留用户填，未接真实 Langfuse | ✅（接口） |

## 二、本轮修掉的真实缺陷

### 1. 命令列表 Path 序列化

`discover_commands()` 返回 `Path`，`routes._json()` 的 `json.dumps` 无法序列化，handler 在写响应前抛 `TypeError`，连接被关闭。已改为相对 workdir 的 POSIX 字符串，并加回归断言；真实 HTTP 从断连变为 200。提交 `85da14e`。

### 2. 定时任务“立即运行”永远不执行

`run_now` 原来把 `next_run_at` 设为 `0`；`AutomationScheduler.run_due()` 把 `0` 解释为“新建任务尚未排期”，只补算下一次 cron，任务永不出队。已改为 `time.time() - 1`（非零过去值），并更新回归断言。

- 定向回归：`tests/test_automations_web.py` **17 passed**。
- 真实复验：`run_now` 后任务出现 `last_status=ok` 和真实执行会话。
- 修复已提交 `0674fa0`。

### 3. `doctor` 把本地 GGUF 误报为“无凭据将 mock”

`doctor` 只看 `api_key`，`provider.name=llama_cpp` 时即使 GGUF 存在也提示未配置凭据。已加本地模型分支：文件存在显示“本地模型已就绪”，不存在给出重新下载提示。`tests/test_cli_onboarding.py` **43 passed**，提交 `3fc89a3`。

### 4. TUI 的 Ctrl-D 被输入框吞掉

footer 写着 `Quit ^D`，但 `Input.BINDINGS` 里 `ctrl+d` 是 `delete_right`，优先级高于 `SparkApp` 的 quit。结果是按 Ctrl-D 只删光标右侧字符，窗口不退。已给 app 级绑定加 `priority=True`，并用真实 PTY 验证：历史会话加载后按 Ctrl-D 正常离开备用屏、进程退出。`tests/test_tui_app.py` **7 passed**，提交 `143d4a0`。

### 5. Linux `spark[desktop]` 装了 pywebview 却没有 GUI 后端

`desktop` extra 原来只有 `pywebview>=5.0`。Linux 上 pywebview 导入成功，但 `start()` 报 “You must have either QT or GTK”，桌面窗口完全起不来。已在 Linux 分支改为 `pywebview[qt]`（QtPy + PyQt6 + WebEngine），并在 `run_desktop` 捕获缺后端异常返回退出码 2、打印系统依赖提示。`tests/test_desktop.py` **3 passed**；Xvfb + Qt xcb 下真实窗口 1280×832、首页与接口全部 200。提交 `e981468`。

### 6. `uninstall` 的 `SPARK_HOME` 守卫漏掉 `/tmp`

旧守卫只拦家目录和根目录。真实执行 `SPARK_HOME=/tmp spark uninstall --yes` 时，命令没有拒绝，而是开始清点并尝试 `rmtree('/tmp')`，因容器权限不足才报 `Operation not permitted`；若在权限更宽的环境，这会删掉整个临时目录。现在守卫在列清单前执行，拒绝 `/tmp`、`/var/tmp`、`/home`、`/opt`、家目录、当前工作目录、系统根目录；专用目录如 `/tmp/spark-uninstall-test` 仍可正常清理。`tests/test_cli_onboarding.py` **44 passed**（含新增 `/tmp` 拒绝回归），真实命令退出码 2，`/tmp` 未变。

## 三、外部边界（按用户确认的验收口径）

用户已明确：真实商业 Key、PR token、生产 PostgreSQL/Keycloak、第三方 IDE、真实
Langfuse 都由用户自己提供或部署；产品侧要做的是协议适配、配置入口、引导和清晰
错误，不以“没有外部凭据”判产品缺陷。

1. **商业模型**：官方协议由 LiteLLM 原生 provider 适配；设置/配置可填 key、base、
   model；真实计费请求由用户 Key 触发，本轮未代发。
2. **PR**：`spark pr status` 显示平台/仓库/token 来源与权限提示；`spark pr token`
   交互保存到 `$SPARK_HOME/pr-tokens.json`（0600），环境变量优先；真实 API 由用户
   token 触发。
3. **企业控制面**：SQLite dev 模式真跑；生产 PostgreSQL/Keycloak 由用户部署，
   生产 OIDC 未代测。
4. **第三方 IDE**：按用户决定不测 VS Code/JetBrains/Neovim 客户端；配置生成已验证。
5. **外部可观测**：本机 OTLP collector 已收 span；真实 Langfuse/Tempo 端点由用户填。
6. **更新/卸载**：卸载在一次性目录实测并验证守卫；默认更新源 403 属外部环境。
7. **UI**：自动化与桌面窗口已过；未做逐项人工点击清单。

## 四、验收口径

按用户确认的口径，产品侧功能已经全部有真实调用或真实入口测试：协议、配置入口、
引导、错误提示、离线降级都可验证；真实第三方请求和凭据由用户提供，不再作为产品
阻塞。

但不能说“真实第三方服务都发生过请求”：商业模型、PR 远端、生产 PostgreSQL/
Keycloak、真实 IDE、Langfuse 的线上调用仍未代测。这个边界在表里保留。

## 五、最终回归

- 最终全量 pytest：**1767 passed, 13 skipped, 14 warnings in 820.00s (13:40)**，退出码 0；包含 PR token 2 条与 `uninstall` `/tmp` 守卫 1 条新回归。旧基线 `final3` 为 1766 passed / 11 skipped / 1142.05s。
- 相比上一轮全量 `1761 passed, 13 skipped`：新增 doctor/TUI/desktop/PR token/uninstall 守卫回归，并启用 2 条真实 Whisper 转写测试（原为 skipped）。
- 覆盖 UI/浏览器、协议、工具、Web、CLI、桌面与办公转写模块测试；不代表外部集成已验收。
- 定向回归：定时任务 **17 passed**；doctor **43 passed**；TUI **7 passed**；desktop **3 passed**；真实 Whisper 上传→转写→纪要 **5 passed**。
- 本轮修复提交：`85da14e`（命令列表 Path）、`0674fa0`（run_now）、`3fc89a3`（doctor 本地模型）、`143d4a0`（TUI Ctrl-D）、`e981468`（Linux desktop 后端）、`1083bf3`（PR token 入口）、`a3c5b1b`（uninstall 系统目录守卫）。
- 全量回归日志：`../spark-full-final5.log`，退出码 `../spark-full-final5.rc`。

该文件不把 pytest 数量当作功能完成证明；上表的外部集成项仍按 ⛔/🟡 单独标注。
