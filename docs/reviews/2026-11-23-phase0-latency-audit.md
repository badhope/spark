# Phase 0 延迟与评测口径审计（2026-11-23）

## 结论先行

云端首帧慢的主因不在 Spark 的 SSE 拼装，而在用户提供的聚合网关本身：同一
`glm-5.3-flash` 请求，直连 curl 的首条 SSE 数据在 3.2–43.2 秒之间跳变；换
HTTP/1.1、HTTP/2、Python httpx、OpenAI SDK 都复现了同样的长尾。Spark 的
`ttft_ms` 记录的是首个流式 delta（含 reasoning），不是假数，但它把网关的长尾
如实带了进来。

真正能在 Spark 侧修掉的是上下文税：默认 75 个工具、43,237 字符 schema，约
13.5k token，每轮都随请求重发。新增 `agent.tool_preset = "coding"` 后收敛到
21 个工具、11,551 字符，约 3.6k token。同一个 `bugfix-calc-add` 任务，输入
token 从 46,606 降到 14,732（−68.4%），端到端从 99.7s 降到 66.1s（−33.7%）；
同期网关仍抖动，所以延迟数字只作方向证据，token 数字是确定性的。

## 模型选择：先用最便宜的

公开价目（2026-11-23 检索）：

- `qwen-flash` / `qwen3.7-flash`：输入约 $0.03 / 百万 token，输出约 $0.13；
- `glm-5.3-flash`：输入约 $0.15，输出约 $0.50；
- `kimi-k3`：输入约 $3.00，输出约 $15.00，是前者的几十倍。

因此批量矩阵用 `qwen-flash`；`kimi-k3` 的进程已停，不再消耗额度。
`glm-5.3` 只保留 10 任务的能力对照（10/10 PASS），不拿它跑全量。
来源：<https://openclawdatabase.com/tools/model-pricing/>、
<https://www.qwencloud.com/pricing/api>。

## 实测证据

### 1. 网关首帧长尾

对 `https://api.qs.al/v1` 直接发最小流式请求，按首条 `data:` 计时：

| 方式 | 首条数据 | 说明 |
| --- | --- | --- |
| curl HTTP/2 | 0.3–17.8s | 同一请求重复跑，长尾可到 17s+ |
| curl HTTP/1.1 | 6.2–43.2s | 明显更差，但不能稳定复现 |
| httpx 直连 | 3.4s / 19.3s | 两次请求同一客户端，差异来自网关 |
| OpenAI SDK | 5.6–14.2s | 与 LiteLLM 路径一致 |
| Spark provider | 5.6–26.7s | 包含 LiteLLM 解析，量级相同 |

`ttft_ms` 与 `provider_runs[].first_delta_ms` 对得上：首任务首轮 21.1s 的
TTFT，对应 provider 首轮 18.9s 的首 delta。Spark 没有把网关时间算成自己的
渲染时间，`render_ms` 仍为 `null`。

### 2. 工具面税

默认工具面：

- 75 个工具，schema 43,237 字符，估算 13.5k token；
- 系统提示词 6,882 字符，估算 2.1k token；
- 用户消息还没发，单轮已约 15.7k token。

`coding` 预设保留 21 个工具：读写、检索、shell、测试、git、计划、交互，
砍掉浏览器 21 个、LSP 9 个、PR 5 个、工作流/后台/
notebook/MCP 等大块 schema。

同一任务 A/B：

| 指标 | full | coding | 变化 |
| --- | ---: | ---: | ---: |
| 工具数 | 75 | 23 | −69% |
| schema 字符 | 43,237 | 12,463 | −71% |
| 输入 token（4 轮） | 46,606 | 14,732 | −68.4% |
| 端到端 | 99.7s | 66.1s | −33.7% |
| 轮次 / 工具调用 | 4 / 3 | 4 / 3 | 不变 |
| 通过 | PASS | PASS | 不变 |

延迟变化混合了网关抖动，不能全部归因于工具面；token 降幅是确定的。
A/B 跑在 23 工具版本（尚含 `web_search`/`web_fetch`）。随后实测发现便宜模型
会拿这两个工具反复搜“Python dataclass 怎么写”，把编码任务拖成搜索循环，因此
已把 web 移出 `coding` 预设，当前是 21 工具 / 11,551 字符，token 还会再低一点。

### 3. 成本口径

`glm-5.3-flash` 在该聚合端点没有价目表，`session_token_usage()` 返回
`cost_usd = null`。旧评测汇总把 `None` 当 0 累加，输出了 `$0.0000`，等于把
“不知道多少钱”伪装成“免费”。已改为：

- 有定价的 turn 才累加；
- 一个都没定价时 `cost_usd = null`，并给出 `priced_turns` / `unpriced_turns`；
- 工作台成本显示 `—`，不再显示 `$0.0000`。

### 4. pass@1 与调用失败

旧汇总的分母是全部任务，provider 502 导致没跑完的 turn 也被算进 pass@1，
会把“网关没响应”误读成“模型不会做”。已拆出：

- `errors`：turn 级错误数；
- `error_rate`；
- `pass@1` 只用 `total - errors` 作分母；全挂时返回 `null`。

`deepseek-v4.1-flash` 在本次运行中反复返回
`SUBSCRIPTION_NOT_FOUND`（HTTP 502），属于聚合端点订阅组问题，不是 Spark
工具链失败，已单列而不混入模型能力结论。

### 5. qwen-flash 30 任务结果

`cloud-qs-qwen` 在 21 工具预设、600s 单任务超时下跑完 30/30：

- pass@1 **1.000**，errors **0**，error_rate **0.0**；
- TTFT p50 **4.151s**、p95 **7.818s**；
- turn p50 **35.786s**、p95 **113.767s**；
- 30 任务输入/输出 token 约 **1,536,731 / 20,152**，按公开价目估算约
  **$0.049**；网关没有账单，报告里的 `cost_usd` 仍是 `null`。

这组结果已经**饱和**（30/30），不能拿它单独证明“第一梯队”；它能证明的是：
最便宜档模型在 21 工具面上能完成当前 30 个任务，真正的区分度要等更难的
SWE-Lite / 多文件重构集。此前 23 工具版本出现过的 `feature-plugin-registry`
（写坏 `registry.py`）和 `refactor-point-dataclass`（联网搜索循环）已不作为
当前 21 工具版本的失败证据。

### 6. 文档定位冲突

`docs/ARCHITECTURE.md`、`docs/REMAINING.md`、`docs/competitive-comparison.md`
仍写“API-first、本地不是卖点”“不做 NES”。已改为“本地优先、可审计、模型
自由”，并把 NES 从“明确不做”改为“Phase 3 已交付入口，完整 Tab 补全过门禁
再决定”。

## 已改代码

- `src/spark/config.py`：`agent.tool_preset`（`full` / `coding`）。
- `src/spark/tools/registry.py`：`CODING_TOOL_ALLOWLIST` 与
  `apply_tool_preset()`；显式 allowlist 不被覆盖。
- `src/spark/core/loop.py`：构造时应用预设。
- `scripts/run_competitive_evals.py`：评测使用 `coding`；成本 null 口径；
  error/valid 分母；新增 `cloud-qs-glm` / `cloud-qs-deepseek` /
  `cloud-qs-kimi` / `cloud-qs-glm-pro` profile。
- `web/src/components/WorkbenchPanel.tsx`：未知成本显示 `—`。
- `tests/test_tool_preset.py`：3 项。
- `docs/CONFIG.md`、`docs/en/CONFIG.md`：补 `tool_preset`。

## 验证

- `pytest tests/test_tool_preset.py tests/test_tools.py tests/test_loop.py`
  → 15 passed（含 3 项新增）。
- `ruff` 对改动文件通过。
- `web: npx tsc --noEmit` 通过；`npm run build` 通过（i18n 1,052 key、
  1,001 个 `t()` 调用）。
- 真实云端单任务：`bugfix-calc-add` PASS，coding 预设，4 轮 3 工具。

## 未完成

- 30 任务 × 3 模型 × 2 轮矩阵仍在跑；本文不写未产生的 pass@1。
- 本地 1B 仍是 0/3，只能证明它不适合当主力，不是准确率结论。
- 网关首帧长尾无法由 Spark 修复；如果要求 p50 ≤2.5s，需要换端点或在
  Spark 侧做模型路由/降级，不能靠调 SSE。
