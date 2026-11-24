# 全仓复杂度清零（第四轮）

> 日期：2026-11-25
> 范围：`0.15.7` 发布后的未提交改动；不改变对外行为，作为 `0.15.8` 补丁发布。
> 原则：先读原函数与调用点，保持校验顺序、错误文案、回退分支和副作用顺序；每组拆完立即跑静态检查与定向回归，最后跑全量。

## 1. 结果

`radon cc src/spark -s -n D` 与 `-n E` 均为空输出，全仓历史 D/E/F 从 **58（53 D / 5 E / 0 F）** 降到 **0**。

- 5 个 E 级入口全部拆掉：`run_shell`、`run_workflow`、`AgentLoop._llm_round`、`spawn_subtasks_parallel`、`SparkApp._render`。
- 51 个 D 级函数覆盖 CLI 命令、Web handlers、会话事件流、内存/索引检索、Provider 适配、relay、wiki、review、selfheal、workbench 等。
- `scripts/complexity-baseline.json` 重建为 `functions: {}`。门禁语义从“允许 58 个旧债、拦截新增”切换为“任何新增 D/E/F 直接失败”。

## 2. 五个 E 级入口

| 文件 | 原函数 | 拆分 |
|---|---|---|
| `src/spark/tools/shell.py` | `run_shell` E(36) | `_ShellPlan`、`_ShellOutcome`、`_prepare_shell_cwd`、`_build_shell_plan`、`_collect_shell_process`、`_format_shell_result` |
| `src/spark/workflow.py` | `run_workflow` E(34) | `_WorkflowEngine`：取消、依赖输出、单步执行、ready 计算、失败依赖阻塞、budget/stop_after 三出口 |
| `src/spark/core/loop.py` | `_llm_round` E(36) | `_RoundBuffer`、`_round_preflight`、`_round_messages`、`_stream_round`、`_tool_guard_events` |
| `src/spark/core/subagent_runner.py` | `spawn_subtasks_parallel` E(32) | 模块级 `_ParallelFanout`：收集参数、runner、事件打标、drain/flush、start/end 事件、run |
| `src/spark/tui/app.py` | `SparkApp._render` E(31) | `_render_reasoning`、`_render_tool_start`、`_render_tool_end`、`_render_plan`、`_render_context`、`_render_compaction`、`_render_error` |

`spawn_subtask` 与并发分支重复的 sub-loop 构建同时提取为 `_create_sub_loop(owner, ...)`，title 参数区分 `subtask` / `subtask-{index}`。

## 3. D 级拆分

- **工具/协议**：`_strip_jsonc`、`build_wiki`、`_commands_for`、`grep_tool`、`validate_arguments`、`parse_mcp_servers`、`parse_registry_servers`。
- **CLI**：`_exec_async`、`_workflow_run_async`、`autopilot_cmd`、`replay_cmd`、`doctor_cmd`、`mcp_add_cmd`、`queue_cmd`。
- **Web**：`_handle_skills` / `_handle_prompts` / `_handle_plugins` / `_handle_knowledge` / `_handle_queue`、`_history_messages`、`_handle_session_lease`、`_extract_request_fields`、`_resolve_provider_fields`、`_handle_wiki`、`serve_session_events`、`_SSESession._run`。
- **核心/服务**：`WorkflowRun.from_state`、`MeetingMinutesService`、`ReportService`、`TurnReplayRecorder.observe`、`ToolRunnerMixin._run_tool_body`、`resolve_operations`、`_similarity_clusters`、`MemoryStore.search`、`_restore_snapshot`。
- **Provider/索引/交付**：`llama_cpp._generate` / `stream`、`OpenAICompatProvider._stream_impl`、`analyze_impact`、`build_delivery`、`guard`、`diagnose`、`parse_review`、`_score_chunks`、`stream_turn`、`_RelayHandler.do_POST`。

## 4. 拆分边界修复

拆分没有停留在“把行数挪走”，以下边界是在回归中实际抓出并修掉的：

- `_llm_round` 拆分时漏掉 `begin_provider_round()`，导致 metrics rounds=0；已补在 `_stream_round` 进入 try 之前。
- `_create_sub_loop` 初版写成模块级却保留 `self`，运行时 `AgentLoop object has no attribute _create_sub_loop`；改为显式 `owner` 参数。
- `_knowledge.ensure_embeddings` 把 embedding 失败当成“0 条新增”会继续下一批；改为 `_store_batch` 返回 `None` 时 `break`。
- `_history_messages` 拆分后 assistant 普通文本消息一度丢掉 `id/feedback`；已补回。
- `_run_task_tool` 初版漏掉 `_finish_tool_execution`；已补 `started/args_summary` 并调用。
- `_SSESession._run` 把 pending text 提升为实例字段后 mypy 报重复定义；已补类级注解。
- CLI `_workflow_run_async` 替换边界曾误匹配到后面的 `_make_wiki_summarizer`，误删 585 行；已 `git checkout -- src/spark/cli.py` 恢复后以函数头为界重做。
- `tests/test_p1_03_testcmd.py` 仍用 `_exec_async` 源码找 `handle_test`；拆分后该分支在 `_run_test_mode`，已把见证升级为模块源码并增加 helper 断言。

## 5. 验证

| 项目 | 结果 |
|---|---|
| `ruff check src` | 通过 |
| `mypy src --ignore-missing-imports` | 190 source files，0 error |
| 复杂度扫描 | `-n D` / `-n E` 均空 |
| 复杂度门禁 | `0 known D/E/F functions, no new hotspots` |
| 定向回归 | shell 18 passed / 5 skipped；workflow 15；loop/metrics 4；subagent 4 文件 94；TUI 29；CLI test 18 |
| 全量 pytest | **2006 passed, 13 skipped, 0 failed, 14 warnings, 1121.79s** |

## 6. 最终记录

全量 pytest（最终代码，含版本号同步与可编辑包重装）：

```text
2006 passed, 13 skipped, 14 warnings in 1121.79s (0:18:41)
```

- `0 failed`，`0 error`。
- `ruff check src` 全通过；`mypy src --ignore-missing-imports` 190 files 0 error。
- `radon cc src/spark -s -n D` / `-n E` 均空；`scripts/check_complexity.py` 输出 `0 known D/E/F functions, no new hotspots`。
- `tests/test_version_consistency.py` 5 passed（重装可编辑包后 `spark.__version__` = `0.15.8`）。
