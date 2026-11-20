# Spark 全能力审计（2026-11-21）

## 结论

**不能说“全部功能都做过”。**

上一轮把“全量 pytest 1761 通过 + 37 项真实模型 UI + 若干 CLI/协议测试”等同于“全部能力完成”，这个表述过满。按入口、协议、工具、外部依赖逐项拆开后，真实状态是：

- 核心聊天/工具链路、Web 主界面、CLI 主链路、API 主路径：真实跑过。
- 59 个内置工具：schema 全部注册，但只有少数被本轮手工真实调用；其余主要靠模块测试覆盖。
- 商业模型、企业 PostgreSQL/Keycloak、真实 IDE Extension Host、桌面窗口、PR 写评论、OTel/Langfuse、会议转写、update/uninstall：**没有对应环境或凭据，未真实执行**。

因此本文件把“真实手工跑过”“自动化覆盖”“仅入口存在”“受阻未做”分开记录。后续不再用测试数量代替功能验收。

## 验收口径

| 标记 | 含义 |
| --- | --- |
| ✅ 真实通过 | 本轮用真实 HTTP / CLI / 浏览器 / 真实模型调用，看到预期结果 |
| 🟡 自动化覆盖 | 有 pytest 或协议客户端断言，但本轮没有逐项手工点过 |
| ⛔ 受阻未做 | 缺凭据、缺外部服务、缺桌面/IDE 环境，或属于破坏性操作 |
| ❌ 发现缺陷 | 已复现；修复状态单独标注 |

## 一、真实手工跑过的能力

### 1. 模型与核心对话

| 能力 | 证据 | 状态 |
| --- | --- | --- |
| 真实本地模型对话 | MiniCPM5-1B，Web UI 37 passed；CLI `exec` 返回 `CLI-OK` | ✅ |
| 真实模型工具调用（写文件） | `notes.txt` 11 bytes；`cli-notes3.txt` 9 bytes | ✅ |
| 真实模型工具调用（shell） | 同会话 `pwd`，回答 `/tmp/spark-ui-model-work`，与 stdout 一致 | ✅ |
| 多轮上下文 | 第一轮“收到”，第二轮“蓝鲸”正确 | ✅ |
| 工具循环保护 | Web/ACP/TUI/CLI 均返回结构化 `tool_loop_limit` / `tool_repeat_loop` | ✅ |
| SSE 流式 | `/api/chat/stream`：`context → text_delta → context → turn_end → done` | ✅ |
| OpenAI 兼容连通性探测 | 本地假端点，非流式 + 流式各 2 轮，`PROBE-OK` | ✅ |
| 模型目录查询 | `spark models --limit 3` 返回 deepseek/anthropic 实时目录 | ✅ |
| 模型档案保存/删除 | `/api/models/save`、`/api/models/delete` 200 | ✅ |
| 模型档案激活 | **未用真实 HTTP 跑成功路径** | ⛔ |

### 2. Web UI 与 API

| 能力 | 证据 | 状态 |
| --- | --- | --- |
| 设置页 | 18 个页签、65 个动作全点；0 console/page error、0 5xx | ✅ |
| 会话侧栏/消息操作/附件/命令面板/工作台/快捷键 | 25 passed（浏览器真实驱动） | ✅ |
| 移动端 390×844 | 消息按钮 36×36、无横向溢出 | ✅ |
| 无障碍 | axe-core WCAG A/AA 亮/暗 0 serious/critical | ✅ |
| 会话新建/改名/切换/fork/导出/导入/删除 | 真实 HTTP 全部 200 | ✅ |
| 消息赞踩 | assistant id 12 `up` 200；user 消息 404 属接口语义 | ✅ |
| 消息删除/截断 | `/api/messages/delete`、`/api/messages/truncate` 200 | ✅ |
| 命令保存 | 真实 bug：`Path` 无法 JSON 序列化，连接被关闭；修复后 200 | ❌→✅ |
| 技能 list/save/read/delete | 真实 HTTP 200，frontmatter 技能可发现 | ✅ |
| 记忆 add/delete/optimize | 真实 HTTP 200 | ✅ |
| 知识库 add/search | `README.md` 导入，搜索 `Smoke` 命中 | ✅ |
| Repo Wiki status/generate/read/delete | 真实 HTTP 200，生成 2 modules | ✅ |
| AGENTS.md 写/读 | 真实 HTTP 200 | ✅ |
| 定时任务 create/list/update/toggle/run_now/delete | 真实 HTTP 全部 200；cron 校验有效/无效均正确 | ✅ |
| 空闲队列 create/list/pause/resume/cancel | 真实 HTTP 200；paused 时 queued 1 | ✅ |
| 权限规则写入/删除 | `deny read_file *.secret` 200，删除后 remaining 0 | ✅ |
| 审批档案 save/activate | 真实 HTTP 200 | ✅ |
| 设置持久化 | `agent.max_tool_rounds=11` 写入后 `/api/config` 读到 11 | ✅ |
| 插件 list/enable/disable | 真实 HTTP 200（本机控制令牌） | ✅ |
| 诊断/调试/成本/路由预览/时间线/命令/历史 | 真实 GET 200 | ✅ |
| 文件树/文件读取 | 真实 GET 200；工作区被清理时 404 属预期 | ✅ |
| 内置终端 PTY | open/write/resize/close 全 200，80×24 | ✅ |
| 设备配对 | code/claim/devices/revoke 全 200；设备列表真实返回 | ✅ |
| 自愈 | `/api/selfheal` 200 | ✅ |
| 模型连通性测试 | 假 OpenAI 端点 200；真实商业端点无 Key | 🟡 |
| 检查点回滚 | 只验证了非法 id 404；mock 会话无真实快照样本 | ⛔ |
| 审批/提问的 pending 路径 | 只验证空态；无真实挂起 turn | ⛔ |
| 本地模型下载/激活 | 本轮未重新下载；真实 UI 启用过 MiniCPM5 | 🟡 |

API 触达统计：GET 30 条路由中 **29 条触达**（`/api/pty/stream` 未单测）；POST+PTY 48 条中 **43 条触达**，其余 5 条需要真实模型/下载/挂起状态。所有实际请求的 5xx 只有两次故意指向商业端点的 `/api/test` 502，服务日志 0 traceback。

### 3. CLI

34 个入口 `--help` 全部 rc=0。真实功能调用过的：

| 命令 | 结果 | 状态 |
| --- | --- | --- |
| `version` / `--version` | 0.15.2 | ✅ |
| `init` | 隔离配置写入成功 | ✅ |
| `doctor` | 缺工作目录 exit 1；建目录后全绿 exit 0 | ✅ |
| `exec` | mock 回复 exit 0；MiniCPM5 写文件 + `--json` NDJSON | ✅ |
| `providers` / `route` / `sessions` | 正常输出 | ✅ |
| `config show/path/set` | 读、路径、写入并回读 12 均成功 | ✅ |
| `queue list` | 正常 | ✅ |
| `wiki` | 临时项目 3 文件/2 符号，生成 INDEX.md | ✅ |
| `sbom` | CycloneDX 1.5，255 components | ✅ |
| `replay` | 列表 + 单 turn JSON（schema `spark.replay/1`） | ✅ |
| `context-profile` | 1710/32768 tokens，分类占比正确 | ✅ |
| `review` | 假端点返回结构化 JSON，1 file +2/-0 | ✅ |
| `agent list` / `profile list` | 空态提示正常 | ✅ |
| `mcp list/search` | 在线注册表搜索 github 成功 | ✅ |
| `plugins list/search/install/enable/disable/remove` | 本地 official 插件全链路通过 | ✅ |
| `workflow validate/graph/run` | 1-step 工作流真实执行 `workflow_end ok=true` | ✅ |
| `ide install zed/jetbrains/neovim` | 三份配置均生成，JSON/Lua 内容正确 | ✅ |
| `models` | 实时目录 | ✅ |
| `test` | 假端点 2 轮通过 | ✅ |
| `web` | 8877 真实服务 | ✅ |
| `mcp-serve` / `a2a-serve` / `acp` | 138 项协议定向测试通过 | 🟡 |
| `pr view` | 公共 GitHub 被 403 限流；无平台 token | ⛔ |
| `desktop` | 无桌面/WebView 环境，仅 `--help` | ⛔ |
| `enterprise` | 无 PostgreSQL/Keycloak，仅 `--help` | ⛔ |
| `update` | `--check` 被 GitHub 403 rate limit；未安装 | ⛔ |
| `uninstall` | 破坏性操作，未执行 | ⛔ |
| `relay` | 138 项测试覆盖，未单独起中继做端到端 | 🟡 |
| `resume` | 需要 TUI，未单独跑 | 🟡 |

### 4. 协议与外部集成

| 能力 | 证据 | 状态 |
| --- | --- | --- |
| A2A | Agent Card、message/send、message/stream 四帧 | 🟡 138 passed |
| ACP | initialize、session/new、六档审批 | 🟡 138 passed |
| MCP server 暴露 | 37 tools、`list_dir` 真实执行（历史 FEATURES 记录） | 🟡 |
| MCP 客户端 stdio/streamable | 138 passed | 🟡 |
| 插件子进程/hook/工具 | 138 passed；本地插件安装启用手工通过 | ✅/🟡 |
| LSP | 9 工具、无服务器降级、rename dry-run | 🟡 单测 |
| 浏览器工具 | Playwright 后台线程、截图落盘 | 🟡 单测 |
| PR 只读 | 平台 token/API 限流阻断 | ⛔ |
| 企业控制面 | 无 PostgreSQL/Keycloak | ⛔ |
| VS Code Extension Host | 无 Code/Extension Host | ⛔ |
| JetBrains/Neovim 真实 IDE | 只验证配置写入；无 IDE 客户端 | 🟡 |
| OTel/Langfuse | 无可达端点 | ⛔ |
| 会议转写 | faster-whisper 依赖/音频样本未验证 | ⛔ |
| 文档品牌化 | 无企业样式的真实导出 | ⛔ |

## 二、59 个内置工具的实际覆盖

工具注册表 **59 个**（`ToolRegistry.schemas()` 实测枚举）。本轮真实手工调用确认的只有：

- `write_file`（MiniCPM5 CLI/Web 真实写盘）
- `run_shell`（MiniCPM5 CLI/Web 真实执行）

其余 57 个没有逐个手工调用。它们有 schema 与模块测试，但不能写成“59 个工具全部真实跑过”。其中：

- 文件/搜索/Git/测试/后台任务/笔记本等：🟡 单测覆盖。
- 浏览器/LSP/PR/MCP/插件：🟡 单测或协议测试；PR 写路径 ⛔。
- `task`/`ask_user`/`update_plan`/`goal_update`/`load_skill`：🟡 单测/UI 交互覆盖，未逐个手工 tool-call。

## 三、仍然没有做的事（字面 100% 缺口）

1. 商业模型真实 Key：DeepSeek / Qwen / Kimi / GLM / 豆包 / OpenAI / Anthropic 均未发真实请求。
2. 企业控制面：PostgreSQL + Keycloak + Casbin + 全格式导出未起真实环境。
3. 真实 IDE：VS Code Extension Host、JetBrains 2025.3、Neovim 客户端未拉起。
4. 桌面窗口：pywebview / WebView2 未在真实桌面会话创建。
5. PR 写操作：GitHub/GitCode/Gitee token 与 publish 未做。
6. 可观测：OTel/Langfuse 无可达 collector。
7. 会议转写：faster-whisper 与真实音频未跑。
8. 更新/卸载：外网发布源 403；卸载属破坏性操作。
9. 本地模型重新下载/完整性校验：本轮未重复下载 657MB 权重。
10. 59 工具逐一手工调用：未做；只做了关键链路与测试覆盖。

## 四、本轮修掉的真实缺陷

- **命令列表导致 Web 连接被关闭**：`discover_commands()` 返回 `Path`，`routes._json()` 的 `json.dumps` 无法序列化，handler 在写响应前抛 `TypeError`。
- 修复：`_command_entries()` 将 `Path` 转为相对 workdir 的 POSIX 字符串；无法相对时退回绝对 POSIX。
- 回归：`tests/test_content_handlers_routing.py::TestPromptsEndpoint` 增加 `json.dumps(body)` 断言，9 passed；真实 HTTP `save_command` 由断连变为 200。
- 该修复已提交 `85da14e fix(web): serialize command paths as strings`。

## 五、验收建议

如果“全部功能”指“所有核心用户路径 + 所有本机可执行入口”，当前可以给出真实通过的证据，但必须保留上面的 ⛔ 清单。如果指“字面每一个工具、每一个外部集成都真实跑过”，则还需要：

1. 一套商业模型测试 Key（至少 OpenAI 兼容 + 一个国产 provider）。
2. 一台带桌面会话的 Windows/Linux 机器，用于 desktop/WebView。
3. VS Code + JetBrains + Neovim 的真实客户端环境。
4. PostgreSQL + Keycloak 测试实例。
5. GitHub/GitCode/Gitee 测试 token（只读 + 一个可写测试 PR）。
6. 一个 OTel/Langfuse collector 与一段会议音频。

在这些条件到位前，正确说法是“核心功能已真实验收；外围集成按矩阵标注受阻”，而不是“全部功能都做过”。
