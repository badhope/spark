# Spark 项目治理（PMO-lite）

本目录是 Spark 的**执行层治理文件**；产品/功能/架构主计划在项目工作区的《Spark-开发总纲-2026-11-19.md》，这里不重复正文。

## 文件

| 文件 | 用途 | 对应计划 |
|---|---|---|
| `roles-raci.md` | 角色、owner、决策权 | 11.1 / 11.7 |
| `wbs-dependencies.md` | WBS、依赖、关键路径、WIP | 11.2 / 11.4 / 11.10 |
| `stage-gates.md` | M0–M4 阶段门证据清单 | 11.3 / 11.9 |
| `templates/weekly-status.md` | 每周一页状态 | 11.6 |
| `templates/change-request.md` | CR 变更申请 | 11.3 |
| `templates/adr.md` | 架构决策记录 | 8.3 / 11.7 |

## 规则

1. **唯一来源**：性能数字看 `perf/budgets.json`；模块边界看 `docs/ARCHITECTURE.md` 与 `.importlinter`；本目录只放治理动作，不另立一套架构。
2. **一个模块一个 accountable owner**；小团队可兼任，但不能无人负责。
3. **阶段门不跳过**：M0–M4 证据不齐，不启动下一阶段。
4. **变更走 CR**：范围/排期/预算变更必须写影响、替代方案、决策人、生效版本。
5. **缺口与技术债分账**：缺口是“没做”（第 8 章 G-01..G-16），技术债是“做了但难改”（11.8）。
