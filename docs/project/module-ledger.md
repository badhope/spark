# 模块台账（31 模块）

> 规则：每个模块只有一个 accountable owner；同一人可兼多个角色。模块变更必须同时更新本台账与契约测试。
> 状态口径：`稳定` = 有契约测试；`演进中` = 有实现但仍在迁移；`缺口` = 未达到五件套。

| ID | 模块 | 主要路径 | 对外契约 | 契约/测试面 | Owner | 状态 |
|---|---|---|---|---|---|---|
| M01 | CLI 与入口 | `src/spark/cli.py`、`__main__.py`、`brand.py` | `spark <cmd>`、`--version` | `tests/test_cli_*`、`test_startup_imports.py` | badhope | 稳定 |
| M02 | 配置与首次启动 | `config.py`、`onboard.py` | config.toml / 环境变量 / `spark onboard` | `test_config*`、`test_cli_onboarding.py` | badhope | 稳定 |
| M03 | Agent Loop | `core/loop.py`、`core/context_window.py`、`core/turn_control.py`、`core/tool_runner.py`、`core/subagent_runner.py` | `AgentLoop.iter_turn_sync()` / 事件流 | `test_loop*`、`test_harness_upgrades.py`、`test_agent_runtime.py` | badhope | 演进中（已拆 4 mixin，loop 736 行；待 M0 收口） |
| M04 | 上下文与 Token | `core/context.py`、`core/tokens.py` | 压缩阈值 / token 估算 | `test_context*`、`test_tokens*` | badhope | 稳定 |
| M05 | 回合存储与检查点 | `core/turn_store.py`、`core/checkpoints.py` | turn/checkpoint 持久化 | `test_turn_store*`、`test_checkpoints*` | badhope | 稳定 |
| M06 | Provider 层 | `providers/` | `BaseProvider.stream()` | `test_providers*`、`test_llm*` | badhope | 稳定 |
| M07 | 模型目录与路由 | `model_catalog.py`、`providers/routing.py` | 模型能力矩阵 / fallback | `test_model_catalog*`、routing 契约 | badhope | 演进中（路由调用方待接） |
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
| M25 | ACP / A2A | `acp_server.py`、`a2a_server.py` | ACP / A2A 协议 | `test_acp*`、`test_a2a*` | badhope | 稳定 |
| M26 | VS Code 扩展 | `vscode-spark/` | VSIX / Web 启动 | 扩展构建与冒烟 | badhope | 缺口（版本落后） |
| M27 | Control / Office | `control/app.py`、`control/agent_runner.py`、`control/models.py` | control task / office API / AgentLoop run | `test_control_plane.py`、`test_control_office.py` | badhope | 稳定（双内核已接） |
| M28 | Authz / 审计 | `audit.py`、control authz | 权限 / 审计日志 | `test_authz*`、`test_audit*` | badhope | 稳定 |
| M29 | 自动化 / Git 记忆 | `automations.py`、`git_memory.py` | cron/automation / git memory | `test_automations*`、`test_git_bg_persist.py` | badhope | 稳定 |
| M30 | 评测与可观测 | `evals/`、`otel.py` | eval runner / OTel span | `test_evals*`、`test_otel*` | badhope | 演进中（覆盖不足） |
| M31 | 发布 / 文档 / 品牌 | `updater.py`、`deploy/`、`docs/`、`brand/`、`.github/` | 包 / 升级 / CI / 品牌 | CI、打包冒烟 | badhope | 演进中 |

## 契约规则

1. **M20/M21 是 Web 契约的唯一入口**：前端只通过 `web/src/api.ts` 调 `/api`，不得直接拼 URL；新增接口必须同时补 `tests/test_routes_sweep.py` 或契约测试。
2. **M08/M14 是工具执行唯一入口**：任何工具不得绕过 `ToolRegistry` 的 schema、审批、沙箱检查。
3. **M03 是回合唯一入口**：control、自动化、子 Agent 都必须最终走 `AgentLoop`，不得各自维护第二套工具循环。
4. **M06/M07 是模型唯一入口**：新增 provider 先接 `BaseProvider`，再由路由/目录暴露；禁止在业务层直接实例化 SDK。
5. **M15 是持久化唯一入口**：会话、消息、检查点、后台任务只能经 `store.py`；迁移必须进 `migrations/versions/`。
