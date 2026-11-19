# WBS、依赖与容量

## 工作包（W0–W9）

| ID | 工作 | 依赖 | 出口 |
|---|---|---|---|
| W0 | Web 迁 FastAPI + 会话运行时 | 无 | SSE envelope、SessionRuntimeRegistry、pending/events、旧 server 可回退 |
| W0.5 | 工程基线 + 双内核收口 | W0 的 OpenAPI/SSE 契约 | 门禁全绿、loop <800、control 走 AgentLoop、性能基线 |
| W1 | 代码智能 LSP/重构 | W0 的 fs API | definition/references/hover/rename/影响面 |
| W2 | 模型与成本 | W0 的 config API | 5 家原生预设、routing 有调用方、成本预算 |
| W3 | IDE 深度 | W0 OpenAPI + W1 | VS Code 0.15.2、diff 审批、会话同步 |
| W4 | 插件/技能生态 | W0.5 工具契约 | 插件运行时、技能导入、官方包 |
| W5 | 浏览器 + PR | W0.5 审批/沙箱 | 浏览器工具、PR 工作流 |
| W6 | 多 Agent 编排 | W0.5 拆 loop | 子 Agent、Workflow、并发上限 |
| W7 | 远程/云/队列 | W0 会话 runtime | 中继、PWA/IM、可选云 runner |
| W8 | 发布硬化 + 评测 | W0–W7 | 签名、灰度、SBOM、评测 |
| W9 | 前端/品牌/四语言 | W0 SSE/OpenAPI | 一壳四区、5 页设置、移动端、a11y |

> 细分 PF/PM/MD 工作包见主计划 10.10 / 11.10 / 12.9，本文件不重复 43 行清单。

## 依赖矩阵（关键边）

| 前置 | 后继 | 说明 |
|---|---|---|
| W0 SSE envelope | W9a sessionRuntime | 没有 session_id/seq，前端无法做 per-session |
| W0 SessionRuntimeRegistry | W7 远程/队列 | 后台会话和远程恢复都依赖它 |
| W0.5 AgentLoop 拆分 | W6 多 Agent | 上帝对象不拆，子 Agent 会继续堆在 loop |
| W0.5 工具契约 | W4 插件 | 插件必须走同一个 ToolRegistry |
| W1 LSP | W5 PR 审查 | PR 影响面需要符号/引用 |
| W2 provider | W6 编排 | 多 Agent 需要成本/能力路由 |
| G6 性能基线 | 所有 W | 每个 release 对比 |

## 关键路径

**W0 → W0.5 → W1 → W6 → W7 → W8**

可并行：W4/W5 与 W1/W2；W9a 与 W0.5 后半段；W3 依赖 W0 OpenAPI。

## 容量规则

- 功能 60% / 治理 15% / 质量安全 15% / 缓冲 10%。
- 每人 WIP=2；功能连续两个迭代 >70% 冻结新需求一个迭代。
- 净新增 >20 人日或 >4 周触发 CR 重基线。
- 每周至少 10% 治理工时；G6/G7 可从质量/安全/缓冲池借，但功能不得低于 60%。

## 当前基线

- 计划版本：v3.5（2026-11-19）。
- 代码基线：`main@d0ea33e` / v0.15.2。
- 性能基线：`perf/baseline.json`（PF-01 生成）。
