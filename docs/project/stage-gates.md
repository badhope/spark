# 阶段门 M0–M4

> 阶段门不通过，不启动下一阶段；只允许“范围/人力/日期”三选一的恢复计划，不静默延期。

## M0（第 1–6 周）

- [x] `ruff` / `mypy` / `bandit` / `lint-imports` 全绿（`b650ecc`）
- [x] W0：SSE envelope（v/session_id/turn_id/seq）+ SessionRuntimeRegistry（`e40b6a3`）
- [x] `GET /api/sessions/{id}/pending`、`GET /api/sessions/{id}/events?after_seq=` 可用（`e40b6a3`）
- [x] `GET /api/sessions/{id}/stream?after_seq=` 断线重放/续流（`0d197c2`）
- [x] 流中切会话：旧流继续、审批/提问不跨会话；前端 per-session 缓存与徽标（`9448d3c`、`1e63da0`）
- [x] W0.5a：`loop.py` 从 2035 行拆到 736 行（`context_window` / `turn_control` / `tool_runner` / `subagent_runner` 四个 mixin），核心 79 项回归通过
- [ ] W0.5b：`control` 走 AgentLoop；`tasks.session_id`/`artifacts.session_id`
- [x] 31 模块台账 + owner + `docs/project/roles-raci.md` 真实 owner（`module-ledger.md`）
- [x] `perf/baseline.json` 可对比（CLI p50 155.8ms / p95 170.9ms）
- [ ] 旧 server 回退开关 `SPARK_WEB_LEGACY=1` 可用

## M1（第 7–13 周）

- [x] W9a：前端 `sessionRuntime`；切会话 0 污染；后台会话“生成中/待审批/待回答”徽标（`9448d3c`、`1e63da0`）
- [ ] W1：definition/references/hover/rename + 影响面
- [ ] W2：Anthropic/OpenAI/Gemini 原生预设；routing 有调用方；成本面板
- [ ] 前端首屏 gzip ≤220KB；Workbench 5 tab；设置 5 页
- [ ] SSE 契约漂移 CI 阻断

## M2（第 14–21 周）

- [ ] W3：VS Code 0.15.2；diff 审批、会话同步
- [ ] W4：插件子进程隔离、权限拒绝路径、技能 6 目录导入
- [ ] W5：浏览器工具、PR 工作流
- [ ] 插件崩溃不拖主进程；官方插件包可单关

## M3（第 22–27 周）

- [ ] W6：子 Agent/Workflow；并发上限；成本上限
- [ ] W7：中继、PWA/IM、可选云 runner
- [ ] 远程执行仍走 policy + sandbox + approval

## M4（第 28–31 周）

- [ ] W8：签名、灰度、SBOM、评测
- [ ] W9b：zh/en/ja/es；8 屏视觉回归；a11y 0 Critical
- [ ] 性能回归：启动/首屏/SSE/内存均在预算内
- [ ] 三平台打包 + 升级/回滚演练通过
