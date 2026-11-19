# spark agent evals 基线（P1-6）

> 目标：让「agent 改对了没有」从主观感觉变成可回归数字。有 eval 才有迭代依据；
> 国产模型差距也用 eval 量化，而不是凭印象。

## 1. 现状：接入方案探测结论（2026-10 实测）

| 候选 | 实测结果 | 结论 |
|---|---|---|
| `deepeval==4.2.8` | 全新 venv `uv pip install deepeval==4.2.8` 成功，`import deepeval` 正常（直接依赖 31 个，新增 grpcio/aiohttp/posthog 等闭包约 50MB） | **不接入 CI**。原因：①其评判层（GEval 等 LLM-as-judge）必须调 LLM API，mock 模式 CI 里无 key 无法生效；②SWE-bench 风格「检出仓库跑 FAIL_TO_PASS pytest」它本身不提供，仍需手写；③每次 CI 装 50MB 闭包对 mock 零收益。留作文档化的可选 LLM-judge 路径。 |
| `promptfoo`（npm） | 同为 LLM-judge 工具，依赖 Node 环境 + 模型 key | 同上，收益在真实模型对比阶段再引入。 |
| **自研轻量 harness**（本项交付） | 纯标准库 + MockProvider，fixture 驱动，全离线 | **采用**。harness 真实跑 AgentLoop，判定方式对齐 SWE-bench（pytest 验证）与 τ-bench（工具轨迹合规）。 |

降级是诚实的：真实数据集求解（clone astropy/django at base_commit 跑
FAIL_TO_PASS）需要 Docker/重型 Python 环境，不属于 CI 范畴，写在 §5 手动路径。

## 2. 数据集与下载情况（如实记录）

| 数据源 | 可达性 | 下载情况 |
|---|---|---|
| HuggingFace `princeton-nlp/SWE-bench_Lite`（test split，300 实例） | 匿名可达（HTTP 200；datasets-server `first-rows` 返回 JSON） | 已拉取 **8 行**（4 astropy + 4 django），落盘 `src/spark/evals/data/swe_lite_real_subset.json`（约 93KB，含 problem_statement、FAIL_TO_PASS、gold patch 参考）。 |
| `sierra-research/tau-bench` / `tau2-bench-*` | 匿名 **401 gated**，未拉取 | τ-bench 场景以其轨迹判定范式手写为 fixtures（`tau-toy-order-refund`、`tau-toy-shipment-lookup`）；拿到 HF token 后可按 §4 扩展。 |

## 3. 场景子集

| suite | 场景数 | 判定方式 |
|---|---|---|
| `swe-lite-style` | 2（toy 仓库修 bug） | harness 真实跑 `pytest`（对齐 FAIL_TO_PASS）+ 文件内容检查 |
| `tau-bench-style` | 2（客服工具轨迹） | 工具调用序列顺序/参数 + 最终文本包含 |
| `swe-lite-real` | 8（真实 SWE-bench Lite 行） | **ingest smoke**：真实 problem_statement 能被 harness 消费、loop 能跑完；不求解 issue |

## 4. 运行方式

### 本地（开发机，秒级）

```bash
pip install -e ".[dev]"

# 全量（内置 4 + 真实子集 8 smoke）
python -m spark.evals

# 只跑某一类
python -m spark.evals --suite swe-lite-style
python -m spark.evals --suite tau-bench-style
python -m spark.evals --id swe-toy-calc-add

# 报告落盘
python -m spark.evals --json evals-report.json

# 手动刷新真实子集（需联网，约 10 行/次）
python -m spark.evals --refresh-real
```

退出码：全部通过 = 0；任一失败 = 1（CI 据此变红）。

### CI

`.github/workflows/evals.yml`：push/PR + 每天 18:00 UTC（02:00 Asia/Shanghai）跑
`python -m spark.evals --json evals-report.json`；失败即红，报告作为 artifact
留存 14 天。CI 全程 mock，不联网、不烧 token，分钟内完成。

每次运行还会对比 `evals/baseline.json`：

- 通过数比基线下降超过 2 点 → 阻断；
- 基线里原本通过、现在失败的场景 → 即使总数没掉也阻断（防止“修一个坏一个”）。

```bash
python scripts/check_eval_regression.py evals-report.json evals/baseline.json 2
```

基线更新是显式动作：确认掉点是预期变更后，重新生成 `evals/baseline.json`
并随变更一起评审，不允许 CI 自动改写基线。

## 5. 如何扩展场景

在 `src/spark/evals/scenarios.py` 追加 `Scenario`：

- `setup_files`：预先铺进临时 workdir 的文件（带 bug 的源码/测试/数据）。
- `script`：MockProvider 逐轮剧本（text / tool_call）——离线复现「agent 会怎么干」。
- `judges`：判定器，全部通过才算 pass：
  - `run_command`（`{python}` 占位 = 当前解释器）：跑 pytest / 任意命令，对齐 SWE-bench 验证；
  - `tool_sequence`：工具调用序列（顺序 + 参数包含），对齐 τ-bench；
  - `file_contains` / `file_equals` / `files_exist`；
  - `final_text_contains`；
  - `statement_min_chars`：真实数据集行的 ingest smoke。

真实 τ-bench 数据：拿到 HF token 后，参照 `download_real.py` 的写法从
`datasets-server.huggingface.co` 拉行 JSON 落盘即可（无需 parquet 依赖）。

## 6. 国产模型差距量化用法（路线）

当前 harness 的 `script` 是 MockProvider 剧本——它回归的是 **harness + AgentLoop
行为正确性**，不是模型能力。要量化国产模型差距：

1. 在 CI 之外建手动目标：把 `MockProvider` 换成真实 provider（DeepSeek-V3.2 /
   Qwen3-Coder / GLM-4.5 / Kimi K2，走现有 `openai_compat.py`）；
2. 同一批场景（尤其 `swe-lite-style` 的 toy 修复任务）跑 N 个模型，
   记录每 suite 通过率；
3. 需要 LLM-judge 时再引入 `deepeval`（已验证可装），把 GEval 接到
   OpenAI 兼容端点；
4. 报告形态已就绪：`evals-report.json` 的 per-scenario pass/fail 可直接
   做模型间横向对比表。

> 诚实边界：本仓库当前 **没有** 跑过任何真实模型的通过率数字——那是下一步
> 手动目标的事，不在 CI 内自动发生。
