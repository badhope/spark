# 模块台账（37 模块）

> 规则：每个模块只有一个 accountable owner；同一人可兼多个角色。模块变更必须同时更新本台账与契约测试。
> 状态口径：`稳定` = 有契约测试；`演进中` = 有实现但仍在迁移；`缺口` = 未达到五件套。

| ID | 模块 | 主要路径 | 对外契约 | 契约/测试面 | Owner | 状态 |
|---|---|---|---|---|---|---|
| M01 | CLI 与入口 | `src/spark/cli.py`、`__main__.py`、`brand.py` | `spark <cmd>`、`--version` | `tests/test_cli_*`、`test_startup_imports.py` | badhope | 稳定 |
| M02 | 配置与首次启动 | `config.py`、`onboard.py` | config.toml / 环境变量 / `spark onboard` | `test_config*`、`test_cli_onboarding.py` | badhope | 稳定 |
| M03 | Agent Loop | `core/loop.py`、`core/context_window.py`、`core/turn_control.py`、`core/tool_runner.py`、`core/subagent_runner.py` | `AgentLoop.iter_turn_sync()` / 事件流 | `test_loop*`、`test_harness_upgrades.py`、`test_agent_runtime.py` | badhope | 演进中（已拆 4 mixin，loop 736 行；待 M0 收口） |
| M04 | 上下文与 Token | `core/context.py`、`core/tokens.py` | 压缩阈值 / token 估算 | `test_context*`、`test_tokens*` | badhope | 稳定 |
| M05 | 回合存储与检查点 | `core/turn_store.py`、`core/checkpoints.py` | turn/checkpoint 持久化 | `test_turn_store*`、`test_checkpoints*` | badhope | 稳定 |
| M06 | Provider 层 | `providers/`（含 `presets.py`） | `BaseProvider.stream()`；8 种 provider；原生 OpenAI Responses / Anthropic prompt cache | `test_providers*`、`test_provider_presets.py` | badhope | 稳定 |
| M07 | 模型目录与路由 | `model_catalog.py`、`providers/routing.py` | 模型能力矩阵 / `spark --tier` / `spark route` / `/api/routing/preview` / 成本台账 | `test_model_catalog*`、`test_p1_10_routing.py`、`test_cost_accounting.py` | badhope | 稳定 |
| M08 | 工具注册表 | `tools/registry.py`、`tools/base.py` | 工具 schema / 审批 / 沙箱 | `test_tools*`、`test_policy*` | badhope | 稳定 |
| M09 | 文件与搜索工具 | `tools/files.py`、`tools/search.py`、`repo_map.py` | read/write/patch/grep/glob | `test_tools_files*`、`test_search*` | badhope | 稳定 |
| M10 | Shell / PTY / 进程 | `tools/shell.py`、`pty/`、`proc.py` | run_shell / bg_* / pty stream | `test_shell*`、`test_pty*`、`test_proc*` | badhope | 稳定 |
| M11 | 联网工具 | `tools/web.py` | web_search / web_fetch | `test_web_tools*`、`test_tools_web*` | badhope | 稳定 |
| M12 | MCP 双向桥 | `tools/mcp_bridge.py`、`mcp_registry.py`、`mcp_expose.py` | MCP client/server | `test_mcp*` | badhope | 稳定 |
| M13 | 编辑与语法门 | `edit/` | apply_patch / AST check | `test_edit*`、`test_apply_patch*` | badhope | 稳定 |
| M14 | 沙箱与审批策略 | `sandbox.py`、`policy.py` | 四档权限 / allow-prompt-deny | `test_sandbox*`、`test_policy*` | badhope | 稳定 |
| M15 | 持久化 | `store.py`、`migrations/` | sessions/messages/checkpoints/bg_jobs | `test_store*`、迁移测试 | badhope | 稳定 |
| M16 | 记忆系统 | `memory/`、`git_memory.py` | memory_search / auto-extract | `test_memory*` | badhope | 稳定 |
| M17 | 知识与 Wiki | `knowledge.py`、`wiki.py` | knowledge / wiki API | `test_knowledge*`、`test_web_wiki_api.py` | badhope | 稳定 |
| M18 | Skills / Hooks / 插件 | `skills.py`、`hooks.py` | skill 扫描 / hook 生命周期 | `test_skills*`、`test_hooks*` | badhope | 演进中（插件执行面需隔离） |
| M19 | 子 Agent | `agents.py`、`tools/task.py`、`core/subagent_runner.py` | task / subtask 事件 | `test_agents*`、`test_subagents*`、`test_p1_04_declsubagent.py` | badhope | 稳定 |
| M20 | Web 运行时 / SSE | `web/state.py`、`sse.py`、`session_stream.py` | SessionRuntime / SSE v1 / replay / `SPARK_WEB_LEGACY` 裸帧兼容 | `test_routes_sweep.py`、`test_web_sse.py` | badhope | 稳定 |
| M21 | Web API / Handler | `web/routes.py`、`web/_handlers*.py` | `/api/*` JSON/SSE | `test_routes_sweep.py`、`test_e2e_server.py` | badhope | 稳定 |
| M22 | Web UI | `web/src/` | 会话/审批/设置/工作台 | `test_ui_browser.py`、前端 `tsc`/`eslint` | badhope | 稳定 |
| M23 | TUI | `tui/` | 终端交互 | `test_tui*` | badhope | 稳定 |
| M24 | 桌面壳 | `desktop.py`、`desktop_main.py` | 桌面启动 / 托盘 | `test_desktop_main.py` | badhope | 稳定 |
| M25 | ACP / A2A / IDE 接入 | `acp_server.py`、`a2a_server.py`、`ide.py` | ACP / A2A 协议；`spark ide install zed\|jetbrains\|neovim` | `test_acp*`、`test_a2a*`、`test_ide_install.py`（16 项） | badhope | 稳定（Zed / JetBrains 原生 ACP / Neovim 模块） |
| M26 | VS Code 扩展 | `vscode-spark/` | 编辑器内聊天 / 选区上下文 / 内联 diff 审批 / 诊断回显 / file:line 跳转 / VSIX | `vscode-spark/test/*.test.js`、`scripts/verify_vscode.py` | badhope | 稳定（0.15.4） |
| M27 | Control / Office | `control/app.py`、`control/agent_runner.py`、`control/models.py` | control task / office API / AgentLoop run | `test_control_plane.py`、`test_control_office.py` | badhope | 稳定（双内核已接） |
| M28 | Authz / 审计 | `audit.py`、control authz | 权限 / 审计日志 | `test_authz*`、`test_audit*` | badhope | 稳定 |
| M29 | 自动化 / Git 记忆 | `automations.py`、`git_memory.py` | cron/automation / git memory | `test_automations*`、`test_git_bg_persist.py` | badhope | 稳定 |
| M30 | 评测与可观测 | `evals/`、`otel.py` | eval runner / OTel span | `test_evals*`、`test_otel*` | badhope | 演进中（覆盖不足） |
| M31 | 发布 / 文档 / 品牌 | `updater.py`、`deploy/`、`docs/`、`brand/`、`.github/` | 包 / 升级 / CI / 品牌 | CI、打包冒烟 | badhope | 演进中 |
| M32 | 代码智能 / LSP | `src/spark/lsp/`（client / servers / manager / workspace / impact / tools） | LSP definition/references/hover/symbols/diagnostics/rename/code action；`affected_tests` | `tests/test_lsp_tools.py`（12 项，含 fake stdio server、dry-run/落盘、跨文件、降级、沙箱、六语言探测） | badhope | 稳定 |
| M33 | 插件运行时 | `plugins.py`、`spark plugins` | manifest 校验/发现/本地安装/卸载；`[plugins].enabled` + `hooks`/`tools` 权限门；hook 与 tool 独立子进程（超时/无 stdin/环境变量清洗）；工具命名空间注册进 ToolRegistry；Web 本机 enable/disable（远程只读）、命令、市场索引、版本检查/升级 | `tests/test_plugins.py`（38 项）、`tests/test_official_plugins.py`（2 项） | badhope | 演进中（W4-a/W4-b/W4-c tools+git 安装+commands+市场索引+updates+official bundle+官方命令插件+Web 开关完成） |
| M34 | PR 上下文 | `pr.py`、`tools/pr_tools.py`、`docs/PR_REVIEW.md` | GitHub/GitCode/Gitee REST：PR 元数据/文件/diff/评论/CI；origin 解析；平台 token 可选；CLI `spark pr view/diff/checks/review/publish`；`publish --confirm` | `tests/test_pr.py`（9 项，mock transport + 平台覆盖） | badhope | 稳定（W5-b review 闭环） |
| M36 | Workflow DSL / 文件锁 | `workflow.py`、`examples/workflows/` | YAML roles/steps/DAG、拓扑顺序、`spark workflow validate\|graph`；跨进程路径锁 | `tests/test_workflow.py`（9 项，含双进程无丢更新与 DAG 执行器） | badhope | 演进中（W6-a 声明层 + W6-b 引擎 + W6-c AgentLoop 适配器 + W6-d 暂停/续跑/重跑 + W6-e Web 图 + W6-f 成本上限 + W6-g worktree 隔离已完成；真实模型端到端待验） |
| M35 | 浏览器工具 | `tools/browser.py`、`web/browser_api.py` | 24 个 `browser_*`：Bing 默认首页/搜索、文档代际 ref、点击/输入/按键/悬停/下拉/滚动/历史/多标签/等待/截图/PDF/下载/视口/JS/文件上传/控制台/网络/对话框、trace 起停、Chromium 性能指标；iframe frame_index；Playwright 后台线程；公网 http(s)/工作目录 `file://`；按域名授权与敏感站点确认；untrusted 文本 | `tests/test_browser.py`（20 项，真实 Playwright 本地页，含失效 ref、frame、日志、dialog、PDF、点击下载、trace、性能指标、站点规则、CLI 列表解析） | badhope | 稳定（Bing 默认 + 24 工具 + 侧栏实时浏览器） |
| M37 | 出站中继 | `relay.py`、`spark relay` | 频道注册/长轮询/请求转发/响应回传；SHA-256 频道密钥；本地 Spark token 注入；JSON 与二进制 body | `tests/test_relay.py`（4 项，本地 relay/upstream 真实 HTTP） | badhope | 演进中（W7-a HTTP MVP；SSE 流式中继与 IM Bot 待做） |

## 契约规则

1. **M20/M21 是 Web 契约的唯一入口**：前端只通过 `web/src/api.ts` 调 `/api`，不得直接拼 URL；新增接口必须同时补 `tests/test_routes_sweep.py` 或契约测试。
2. **M08/M14 是工具执行唯一入口**：任何工具不得绕过 `ToolRegistry` 的 schema、审批、沙箱检查。
3. **M03 是回合唯一入口**：control、自动化、子 Agent 都必须最终走 `AgentLoop`，不得各自维护第二套工具循环。
4. **M06/M07 是模型唯一入口**：新增 provider 先接 `BaseProvider`，再由路由/目录暴露；禁止在业务层直接实例化 SDK。
5. **M15 是持久化唯一入口**：会话、消息、检查点、后台任务只能经 `store.py`；迁移必须进 `migrations/versions/`。
