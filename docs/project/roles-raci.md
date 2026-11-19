# 角色与决策权（RACI-lite）

## 角色

| 角色 | 负责范围 | 默认 owner |
|---|---|---|
| Core Owner | config / proc / policy / loop / graph / context / checkpoints / agents / automations | badhope |
| Provider Owner | providers / model_catalog / memory / 路由与成本 | badhope |
| Tool Owner | tools / registry / edit / pty / knowledge | badhope |
| Extension Owner | skills / plugins / hooks / mcp | badhope |
| Surface Owner | cli / tui / web / acp / a2a / vscode / desktop | badhope |
| Control Owner | control / authz / office | badhope |
| Quality Owner | evals / CI / 覆盖率 / 契约测试 | badhope |
| Release Owner | 打包 / updater / 通道 / 回滚 | badhope |
| Security Owner | 威胁模型 / 密钥 / SBOM / 审计 / 脱敏 | badhope |
| Docs Owner | docs / brand / i18n / 术语表 | badhope |

> 当前为单人维护：`badhope` 兼任全部角色；每个模块仍只有一个 accountable owner（见 `module-ledger.md`）。

## 决策权

| 决策 | 批准 | 必须咨询 | 否决权 |
|---|---|---|---|
| 架构/契约变更 | Core Owner | RFC 评审组 | Security Owner（安全面） |
| 发布 | Release Owner | Quality/Support | Quality Owner（门禁） |
| 范围/日期 | 项目 Owner | 全体 Owner | 项目 Owner |
| 安全例外 | Security Owner | Core/Release | Security Owner |
| 数据迁移 | Core Owner | Release/Security | Release Owner（不可回滚） |
| 插件上架 | Extension Owner | Security/Quality | Security Owner |

## 会议与产出

- 每周：模块健康周会（CI/覆盖率/flaky/性能/P0-P1/缺口）。
- 每两周：G6 性能评审 + release train。
- 每月：G7 项目状态评审 + 架构评审。
- 每季度：安全/许可/依赖复审 + 路线图重基线。

所有跨模块决策记录进 `docs/decisions/`（ADR）；无 ADR 的跨模块决策视为未决策。
