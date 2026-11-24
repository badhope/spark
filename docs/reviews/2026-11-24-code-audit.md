# 代码合规审计（2026-11-24）

> 范围：`src/spark` 全量源文件。目标不是“把指标刷绿”，而是找出 AI 生成代码的典型通病：死代码、无效分支、重复逻辑、复杂度堆积，并把确实有风险的点修掉。
> 原始输出：`docs/reviews/2026-11-24-vulture.txt`、`docs/reviews/2026-11-24-radon-cc.txt`。
> 后续更新（同日第二轮债务清扫）：`policy.decide` 已按规则族拆分并保持原决策顺序，队列 Autopilot 与 `_run_queued_task` 也已拆分；详见 [`2026-11-24-debt-sweep.md`](2026-11-24-debt-sweep.md)。

## 1. 工具结果

| 检查 | 结果 |
|---|---|
| `ruff check src tests scripts` | 通过（0 error） |
| `mypy src/spark` | 通过（见本轮最终回归记录） |
| `vulture --min-confidence 80` | 6 条，全部为协议签名/异步生成器形状，无可删除死代码 |
| `radon cc -n D` | 70 个 D/E/F 级函数（历史基线；新增代码已不再引入 D/E） |

## 2. vulture 6 条逐项判定

| 位置 | 判定 | 处理 |
|---|---|---|
| `config.py:408 dotenv_settings` | Pydantic `settings_customise_sources` 的**关键字参数**，删掉/改名会破坏配置源 | 保留，标注为协议参数 |
| `compact.py:306/319/330/347 formatted` | LlamaIndex `complete`/`stream_complete` 协议参数 | 保留，标注为协议参数 |
| `providers/mock.py:23` `yield` after `raise` | `async def` 必须是异步生成器；删掉 `yield` 会让 `async for` 调用方直接 TypeError | 保留，标注为生成器形状 |

**结论**：这 6 条不是死代码，是“接口形状要求”。如果为了 vulture 清零而删参数，会造成真实回归。

## 3. 本轮实际修复

### 3.1 删除/收敛的真实问题

- 新增 `Autopilot` 时清理了占位字段与未使用导入；所有新文件 ruff 通过。
- `semantic_index` 删除了未使用的 `CHUNK_OVERLAP`，索引版本从 1 升到 2，避免旧 schema 混用。
- `semantic_index.refresh()` 从 E(32) 拆成 `_file_pairs` / `_cache_is_current` / `_build_chunks` / `_refresh_vectors`，目前只剩 `_score_chunks` D(21)。
- `AutopilotRunner.run()` 从 D(27) 拆成 `_limit_reason` / `_execute_turn` / `_after_turn`，`run()` 已不在 D/E 列表。
- `CodeIndex.search()` 从 E(37) 拆成 `_score_chunks` / `_query_vector` / `_blend_vectors` / `_select_hits`，检索主函数不再超阈值。

### 3.2 能力升级（不是“重构表演”）

- 符号感知分块：按顶层 `class/def/function/...` 切，不再把类方法切碎；长函数超预算才续窗，并保留 symbol。
- 增量索引：按文件内容 SHA256 复用未变文件的 chunks；只对新增/变更 chunk 做 embedding。
- 向量复用：只保留仍存在的 `path:start_line` 向量；行号漂移的旧向量主动丢弃，避免“向量挂错位置”。
- 混合重排：BM25 + exact phrase + path + symbol exact + optional cosine；同 symbol 最多保留 2 个窗口，避免重复上下文。

## 4. 剩余高复杂度清单（未在本轮全量拆完）

按 radon 输出，剩余 D/E/F 主要集中在历史模块：

| 模块 | 代表函数 | 风险 | 处理策略 |
|---|---|---|---|
| `policy.py` | `decide` F(45) | 安全关键，拆错会放行/误拒 | ✅ 已按规则族拆分；21 项决策测试通过 |
| `tools/repo_context.py` | `repo_context` F(60) | 仓库探测入口，影响首轮理解 | 拆“语言/命令/布局/入口”四个采集器；需真实仓库回归 |
| `web/_handlers_settings.py` | `_handle_settings_locked` F(52) | 设置写入 + 脱敏 + 校验耦合 | 按“解析→校验→落盘→回滚”拆；需 Web 集成测试 |
| `workflow.py` | `load_workflow` F(43)、`run_workflow` E(34) | DSL 校验与执行耦合 | 抽 validator / scheduler / persistence |
| `providers/errors.py` | `classify_provider_error` F(52) | 错误分类长链 | 规则表化，保留现有测试 |
| `tools/shell.py` | `run_shell` E(36) | 安全边界 | 只做无行为变化的提取，先补负向测试 |

**诚实结论**：全仓 70 个 D/E/F 函数没有在本轮全部重构完。把安全关键函数为了降分而硬拆，会比保留复杂度更危险；本轮先把新增代码做到不引入 D/E，并把代码索引这条主链路拆干净。剩余项进入 `PROJECT_BASELINE` 的 `CODE-02`，必须带回归测试后再拆。

## 5. 门禁建议

```bash
ruff check src tests scripts
mypy src/spark
vulture src/spark --min-confidence 80   # 人工判定，不直接失败
radon cc -n D -s src/spark             # 新增 D/E 必须解释或拆分
```

CI 不应把 vulture 直接设为失败（会逼人删协议参数）；应把“新增 D/E 函数”作为 diff 门禁，而不是全仓绝对值。
