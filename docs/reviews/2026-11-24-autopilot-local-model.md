# Autopilot 真实本地模型验收（2026-11-24）

> 目标：不是合成故障注入，也不是 MockProvider，而是**真实本地 GGUF 模型 + 真实 Python 仓库 + 真实 pytest**跑通 `spark autopilot`。
> 这不是 `AUT-05` 的替代：只有一个仓库、一个任务、4 轮，不足以证明 20+ 回合外部仓库的稳定性。
>
> 最终代码上又重跑了一次干净仓库验收：4 轮、8 次工具调用、passed；证据 `...-rerun.ndjson` 与 `...-rerun-state.json`。

## 1. 环境

| 项 | 值 |
|---|---|
| 模型 | MiniCPM5-2B-Q4_K_M（本地 GGUF，`llama_cpp` 提供方） |
| 仓库 | `/home/user/Doubao/autopilot-demo-local-2b`（独立于 Spark 仓库） |
| 初始状态 | `def add(a, b): return a - b`，测试期望 `add(2,3)==5`，初始 pytest 失败 |
| 命令 | `spark autopilot "修复 add..." --provider llama_cpp --model ... --max-turns 6 --json` |
| 沙箱 | 默认 `workspace`，工具无网络，`full-auto` |
| 结果 | **passed，4 轮，11 次工具调用** |
| 退出码 | `0` |

## 2. 实际轨迹

| 轮 | 阶段 | 工具调用 | 测试 | 结果 |
|---|---|---|---|---|
| 1 | build | 5 | fail | 读仓库、定位 `add`、给出修复方向 |
| 2 | repair | 2 | fail | 继续修改，但该轮 diff 指纹未变化 |
| 3 | repair | 2 | **pass** | `add` 改为 `a + b`，独立 pytest 通过 |
| 4 | audit | 2 | **pass** | 复核需求覆盖与边界，确认无额外改动 |

最终代码：

```python
def add(a: int, b: int) -> int:
    return a + b
```

独立测试命令由运行器检测为 `python -m pytest -q`，不是模型提供。测试通过后仍进入 audit 轮，audit 再跑一次独立测试，才置 `done`。

## 3. 这证明了什么

- `spark autopilot` 的 CLI → AgentLoop → 工具 → 沙箱 → 独立 verifier → 状态落盘链路能在真实模型下跑通；
- 模型能通过真实工具修改真实文件，不靠 MockProvider 脚本；
- 测试失败会进入 repair，测试通过不会直接交付，而是进入 audit；
- 状态文件记录了每轮工具调用数、diff、测试结果，可审计。

## 4. 没有证明什么

- **不是 20+ 回合长任务**：这里只有 4 轮；长上下文、跨文件重构、依赖安装、并发任务都未覆盖。
- **不是外部仓库**：仓库是专门构造的最小复现，不含真实项目的复杂度和噪声。
- **不是 1B 模型能力**：同一任务用 MiniCPM5-1B 跑 8 轮，工具调用为 0，未完成。小模型对 Autopilot 的长契约提示词仍不适应；这是模型能力边界，不是运行器假通过。
- **不是 100% 场景覆盖**：只覆盖“单文件 Bug 修复 + 单测试”这一类。

## 5. 复现

```bash
mkdir -p /tmp/autopilot-demo/tests
cat > /tmp/autopilot-demo/calculator.py <<'PY'
def add(a: int, b: int) -> int:
    return a - b
PY
cat > /tmp/autopilot-demo/tests/test_calculator.py <<'PY'
from calculator import add

def test_add_positive_numbers():
    assert add(2, 3) == 5
PY
cd /tmp/autopilot-demo && python -m pytest -q   # 先看到失败

spark autopilot "修复 calculator.py 的 add 函数，使 tests/test_calculator.py 通过；不要修改测试文件" \
  --workdir /tmp/autopilot-demo \
  --provider llama_cpp \
  --model /path/to/MiniCPM5-2B-Q4_K_M.gguf \
  --max-turns 6 --json
```

注意：`/tmp` 在部分自动化环境会被清理；复现时把工作区放在持久目录，避免跑到一半文件消失。

**不要**把 `--json` 的输出重定向到工作区内的文件（例如 `> run.ndjson`）。运行器把工作区文件计入差分指纹，日志自身增长会被误判成代码变更，也会把模型注意力引到无关文件上。重跑时把输出写到工作区外，才得到干净结果。
