# Autopilot 压力矩阵（2026-11-24）

> 目的：在没有真实模型的情况下，把运行器可枚举的失败模式全部注入，验证“不会假成功、不会无限跑、不会丢状态”。
> 原始结果：`docs/reviews/2026-11-24-autopilot-stress.json`。
> 脚本：`scripts/autopilot_stress.py`。

## 1. 结果

- 用例：**120**
- 通过：**120**
- 覆盖：10 类场景 × 12 组边界参数
- 耗时：3.689 秒
- 断言：状态符合预期；轮次不超过 `max_turns`；通过必须 `test_ok=true`；无进展/预算/无变更不得通过；失败必须有原因。

## 2. 场景矩阵

| 场景 | 注入内容 | 预期 |
|---|---|---|
| `pass_first` | 首轮测试通过 + 有 diff | passed（≥2 轮，含复核） |
| `repair_once` | 首轮失败，修复后通过 | passed（≥3 轮） |
| `repair_twice` | 连续两次失败，第三次通过 | passed（≥4 轮） |
| `no_progress` | 测试输出与 diff 指纹都不变 | failed（无进展熔断） |
| `max_turns` | 每轮都有失败、diff 变化 | failed（最大轮次） |
| `model_errors` | 每轮 provider error | failed（连续错误） |
| `budget` | 单轮成本超过上限 | failed（成本上限） |
| `timeout` | 任务已超过总时限 | failed（总时限） |
| `no_change` | 测试通过但无代码变更 | failed（无变更交付门禁） |
| `error_recover` | 首轮 transient error，后续成功 | passed（≥2 轮） |

边界参数：`max_turns` 2–8、`no_progress_limit` 1–3、`cost` 0 / 0.5。

## 3. 未覆盖（必须说清楚）

这不是“100% 编程场景覆盖”。它只覆盖**运行器状态机**的可枚举失败模式。以下仍未验证：

- 真实模型在真实仓库中的多轮行为；
- 进程在某一轮中途被 `kill -9` 的恢复；
- 并发多任务争用同一工作区；
- 超大仓库（>2500 文件）索引与测试超时；
- 模型返回恶意/畸形工具参数时的端到端表现（工具校验已有独立测试，但没进本矩阵）。

真实覆盖率以 `AUT-05` 外部仓库基准为准，不能用这 120 例替代。
