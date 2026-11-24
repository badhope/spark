# Autopilot：端到端无人值守编程

> 定位：把“需求→计划→编码→独立测试→失败回灌→复核”做成一个可恢复、可熔断、可审计的运行器。
> 入口：`spark autopilot "你的编程需求" --workdir .`
> 代码：`src/spark/autopilot.py`；提示词：`src/spark/prompts/autopilot_*.md`；测试：`tests/test_autopilot.py`、`tests/test_cli_autopilot.py`。

## 1. 它是什么

Autopilot 不是第二个 Agent Loop。每一轮仍由现有的 `AgentLoop` 执行，复用审批、沙箱、工具校验、检查点与模型路由。Autopilot 只负责四件事：

1. **状态机**：build → repair → audit → done；
2. **独立验证**：测试由运行器自己跑，不采信模型“已完成”的自述；
3. **失败回灌**：把测试输出、错误输出原样塞回下一轮；
4. **熔断与恢复**：超时、成本、最大轮次、连续无进展、连续模型错误都能停；状态每轮落盘。

## 2. 快速开始

```bash
# 首次运行（默认 full-auto + workspace 沙箱 + 无网络）
spark autopilot "为 auth 模块补一个请求频率限制，并加回归测试" --workdir .

# 限制成本与轮次
spark autopilot "修复 issue #42 的空指针" --workdir . \
  --max-turns 6 --turn-timeout 600 --task-timeout 2400 --max-cost-usd 2.0

# 进程中断后继续（状态文件保留会话、轮次与未完成轮次标记）
spark autopilot --resume .spark/autopilot/<run-id>.json --workdir .

# 长轮次心跳（默认 30 秒；只更新状态文件 mtime，不并发重写 JSON）
spark autopilot "重构 parser" --workdir . --heartbeat-interval 15

# 机器可读事件（NDJSON，适合 CI/队列）
spark autopilot "重构 parser" --workdir . --json
```

退出码：`0` = passed；`1` = failed/blocked；`2` = 参数/安全边界错误。

## 3. 状态机

```
build ──测试失败──> repair ──测试通过──> audit ──测试通过──> done
  │                    ↑                    │
  └────测试通过────────┘                    └──测试失败──> repair
```

- **build**：读仓库、判断场景、给计划、开始实现；不是只输出方案。
- **repair**：把独立测试的失败输出、上一轮摘要、当前 diff 回灌；要求修根因、不跳测试。
- **audit**：测试已绿后做一次需求覆盖复核；只补缺口，不扩范围。
- **done**：独立测试通过 + 有真实代码变更 + 复核完成。

## 4. 提示词体系

| 文件 | 用途 |
|---|---|
| `prompts/autopilot_contract.md` | 总契约：先读后改、最小 diff、验证独立、失败回灌、安全边界、场景矩阵 |
| `prompts/autopilot_build.md` | 首轮：仓库理解 → 场景判断 → 计划 → 实现 → 目标测试 |
| `prompts/autopilot_repair.md` | 修复轮：根因分类 → 读失败链路 → 最小修复 → 回归测试 |
| `prompts/autopilot_verify.md` | 复核轮：需求覆盖、边界、权限、回滚、最终测试 |

场景矩阵覆盖：新功能、Bug 修复、重构、测试补强、性能、安全、文档/配置、环境/依赖。矩阵是**入口分类**，不是“100% 场景覆盖”的证明；真实覆盖率由仓库任务基准与压力矩阵分别度量。

## 5. 可靠性边界

| 机制 | 行为 |
|---|---|
| 单轮超时 | `--turn-timeout` 交给 `iter_turn_sync`；超时记为该轮失败，下一轮可继续 |
| 任务总时限 | `--task-timeout` 到点即停，不无限跑 |
| 最大轮次 | `--max-turns` 硬上限，50 封顶 |
| 成本上限 | `--max-cost-usd` 或 `config.agent.max_session_cost_usd`；按轮累计 |
| 连续模型错误 | 默认 2 次后 failed |
| 连续无进展 | 默认 2 轮：diff 指纹 + 测试输出指纹 + 工具调用指纹（名称/参数摘要）三路同时不变才停 |
| 无代码变更 | 测试通过但 diff 为空 → failed，不把“没改代码”当交付 |
| 崩溃恢复 | 每轮开始前先落盘 `turn_in_progress` / `active_prompt` / `pid`；`--resume` 检测到未完成轮次时标记为中断、保留原提示词重试该轮，不跳过；审计轮中断会强制重审 |
| 心跳 | 长轮次每 `--heartbeat-interval` 秒更新状态文件 mtime；只触碰时间戳，不并发重写 JSON 账本 |
| 沙箱 | 默认 `workspace`；`unrestricted` 必须显式 `--allow-unrestricted` |
| 网络 | 默认关闭；`--network` 才放开工具网络 |
| 审批 | 只允许 `full-auto`；需要人工审批请用 `spark exec` / `spark web` |
| 运行输出 | `--json` / 日志请写到工作区外；写进工作区会进入差分指纹，造成假变更并干扰模型 |

## 6. 已验证 / 未验证

已验证（本轮）：

- 22 项 `tests/test_autopilot.py` 单元测试；其中包含“轮次开始前写入 turn_in_progress”的崩溃标记测试、`prepare_resume` 重试未完成审计、工具调用参数指纹、三路指纹无进展判定，以及 POSIX 子进程在轮次中被真实 `SIGKILL` 后恢复并 passed 的测试；
- 4 项 `tests/test_cli_autopilot.py` CLI 边界测试；
- 1 项 `tests/test_autopilot_e2e.py`：真实 `AgentLoop` + `write_file` + 真实 `pytest` + 独立 verifier，完成 build/audit 并 passed；
- 2 次真实本地模型验收：MiniCPM5-2B + 真实独立仓库 + 真实 pytest。第一轮 4 轮、11 次工具调用；最终代码干净仓库重跑 4 轮、8 次工具调用；均为 build→repair(2)→audit 后 passed。记录 `docs/reviews/2026-11-24-autopilot-local-model.md`，原始 NDJSON `...-local-model.ndjson` 与 `...-local-model-rerun.ndjson`；
- 120 例合成压力矩阵：10 类场景 × 12 组边界，全部通过（见 `docs/reviews/2026-11-24-autopilot-stress.json`）；
- 非 Git 工作区文件指纹、Git 工作区 status/diff 指纹；
- 无变更、无进展、超时、成本、连续错误、恢复等失败路径。

未验证（必须如实标注）：

- **真实模型 + 外部仓库的长任务验收**：已完成一次 MiniCPM5-2B + 最小真实仓库的 4 轮验收，但没有外部仓库、≥20 回合、跨文件重构与依赖安装的基准；`AUT-05` 仍未关闭。
- **100% 场景覆盖**：合成矩阵只覆盖运行器可枚举的失败模式，不等于编程场景全覆盖；真实覆盖率要靠 `AUT-05` 外部仓库基准。
- **CLI + 真实 AgentLoop 的外部 `kill -9` 长跑验收**：已在测试中真实 `SIGKILL` 子进程，验证状态文件保留 `turn_in_progress` / `active_prompt` / `pid`，恢复后重试并 passed；但还没有对“真实模型 + 外部仓库 + 真实工具副作用”的长跑做外部杀进程验收。真实工具可能已部分落盘，恢复时靠独立 verifier 重新判断，不承诺回滚半成品；`AUT-04/05` 仍需补。

## 7. 下一步

1. 接入 `task_queue`，让无人值守任务可排队、重试、配额；
2. 建 3 个外部仓库、20+ 回合真实基准，记录 pass@1、人工介入、恢复成功率、单任务成本；
3. 在临时仓库做真实 `kill -9` 恢复测试，验证中断轮重试与心跳可观测；
4. 把 `--max-cost-usd` 与 provider 成本台账对账；
5. 前端加 Autopilot 运行视图（阶段、轮次、测试、成本、停止/恢复），不再只靠 CLI。
