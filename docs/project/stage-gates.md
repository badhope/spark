# 阶段门 M0–M4

> 阶段门不通过，不启动下一阶段；只允许“范围/人力/日期”三选一的恢复计划，不静默延期。

## M0（第 1–6 周）

- [x] `ruff` / `mypy` / `bandit` / `lint-imports` 全绿（`b650ecc`）
- [x] W0：SSE envelope（v/session_id/turn_id/seq）+ SessionRuntimeRegistry（`e40b6a3`）
- [x] `GET /api/sessions/{id}/pending`、`GET /api/sessions/{id}/events?after_seq=` 可用（`e40b6a3`）
- [x] `GET /api/sessions/{id}/stream?after_seq=` 断线重放/续流（`0d197c2`）
- [x] 流中切会话：旧流继续、审批/提问不跨会话；前端 per-session 缓存与徽标（`9448d3c`、`1e63da0`）
- [x] W0.5a：`loop.py` 从 2035 行拆到 736 行（`context_window` / `turn_control` / `tool_runner` / `subagent_runner` 四个 mixin），核心 79 项回归通过
- [x] W0.5b：`POST /api/v1/tasks/{id}/run` 走 `ControlAgentRunner -> AgentLoop`；`tasks.session_id`/`artifacts.session_id` 迁移与契约测试通过
- [x] 32 模块台账 + owner + `docs/project/roles-raci.md` 真实 owner（`module-ledger.md`）
- [x] `perf/baseline.json` 可对比（CLI p50 155.8ms / p95 170.9ms）
- [x] 旧 SSE 客户端兼容开关 `SPARK_WEB_LEGACY=1` 可用（裸 `data:` 帧，无 v1 envelope/id）

## M1（第 7–13 周）

- [x] W9a：前端 `sessionRuntime`；切会话 0 污染；后台会话“生成中/待审批/待回答”徽标（`9448d3c`、`1e63da0`）
- [x] W1：definition/references/hover/symbols/diagnostics/rename/code action + 影响面（`src/spark/lsp/`，`tests/test_lsp_tools.py` 12 项）
- [x] W2：Anthropic/OpenAI/Gemini 原生预设；routing 有调用方；成本面板
- [x] 前端首屏 gzip ≤220KB（当前主包 76.8 KB gzip）
- [x] Workbench 5 tab：文件 / 变更 / 终端 / 网页 / 追踪（`web/src/components/RightPanel.tsx`；`tests/test_ui_browser.py::test_workbench_five_tabs_render`；`GET /api/git/changes` 提供只读变更视图）
- [x] 设置 5 页：模型 / 外观 / 工作区 / 扩展 / 系统（`SettingsModal` 一级页 + 页内细分页签；`tests/test_ui_browser.py::test_settings_all_tabs_render_without_errors`、`test_settings_controls_are_clickable`）
- [x] SSE 契约漂移 CI 阻断（`tests/test_sse_contract.py`）

## M2（第 14–21 周）

- [x] W3：VS Code 0.15.2（diff 审批、会话同步）+ JetBrains 2025.3+ 原生 ACP 配置 + Neovim CodeCompanion 模块；配置层单测与 Lua 语法检查通过。真实 JetBrains / Neovim 冒烟待有客户端环境
- [x] W4：插件子进程隔离、权限拒绝路径、技能 6 目录导入（W4-b hook 隔离/权限门、W4-c 工具/git 安装/commands/市场索引、official bundle + 仓库内官方命令插件、Web 本机开关已落地）
- [ ] W5：浏览器工具、PR 工作流（浏览器工具、PR 多平台只读上下文、review brief、`publish --confirm` 与 `/spark review` Action 已落；真实仓库验收待做）
- [x] 插件崩溃不拖主进程；官方插件包可单关（`tests/test_plugins.py::test_plugin_hook_crash_becomes_deny`、`test_official_bundle_members_disable_independently`）

## M3（第 22–27 周）

- [ ] W6：子 Agent/Workflow；并发上限；成本上限（W6-a..g 声明层、执行器、AgentLoop 适配、暂停/续跑/重跑、Web 图、成本上限、worktree 隔离已落；真实模型端到端待验）
- [ ] W7：中继、PWA/IM、可选云 runner（PWA manifest/图标、短时配对码、设备令牌、远程解锁页、空闲任务队列（Web/CLI/设置页）已落；中继、IM Bot、云 runner 未开始）
- [ ] 远程执行仍走 policy + sandbox + approval（设备令牌最小权限已落：可发起/审批/取消/切会话与读文件 diff，不可改设置、装插件、直接 shell 或管理配对；`tests/test_remote_token_scope.py`）

## M4（第 28–31 周）

- [ ] W8：签名、灰度、SBOM、评测（评测 CI + 12 场景基线门 + 每日夜间跑已落；`spark sbom` + release SBOM + 发布资产/哈希校验 + GitHub build provenance 已落；真实 50 任务、代码签名、百分比灰度未做；`spark update --channel canary` 已提供预发布通道）
- [ ] W9b：zh/en/ja/es；8 屏视觉回归；a11y 0 Critical（a11y 关键项巡检 + axe WCAG A/AA 亮/暗 0 serious/critical + 8 屏×2 视口布局回归已落；日语/西语、像素基线未做）
- [ ] 性能回归：启动/首屏/SSE/内存均在预算内（本地 `scripts/check_perf_budget.py` 已覆盖 CLI p50/p95、首屏 gzip、核心 API p95；`.github/workflows/ci.yml` 已接独立 perf job；SSE/内存预算待接）
- [ ] 三平台打包 + 升级/回滚演练通过（Linux 本地 PyInstaller 包已产出并冒烟：`dist/spark/spark --version`、`/`、`/api/status` 200；macOS/Windows 与升级/回滚演练待做）
