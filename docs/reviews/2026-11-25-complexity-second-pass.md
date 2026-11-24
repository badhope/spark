# 全仓复杂度清扫（第三轮）

> 日期：2026-11-25
> 范围：`0.15.6` 发布后的未提交改动；不改变对外行为，作为 `0.15.7` 补丁发布。
> 原则：先用现有回归或补最小回归锁行为，再拆纯逻辑与入口编排；流式、进程、并发核心只在证据足够时动。

## 1. 结果

`radon cc src/spark -s -n D` 的历史债从 **70（53 D / 10 E / 7 F）** 降到 **58（53 D / 5 E / 0 F）**。

- F 级清零；
- E 级只剩 5 个：`workflow.run_workflow`、`core/loop.AgentLoop._llm_round`、`core/subagent_runner.spawn_subtasks_parallel`、`tools/shell.run_shell`、`tui/app.SparkApp._render`；
- D 级数量未变，但本轮拆掉的是原先 D 以上的入口，新的小函数没有越级；剩余 D 清单可在仓库根跑 `uv run --with radon radon cc src/spark -s -n D` 查看。

## 2. 拆分清单

| 文件 | 原函数 | 拆分 |
|---|---|---|
| `src/spark/pr.py` | `build_context` F(41) | `_diff_from_files`、`_compact_comments`、`_pr_stats` |
| 同上 | `review_brief` E(37) | `_review_scope`、`_coverage_finding`、`_risky_path_finding`、`_size_finding`、`_ci_finding`、`_draft_finding`、`_conflict_finding` |
| `src/spark/workflow.py` | `load_workflow` F(43) | `_read_workflow_data`、`_parse_role`、`_parse_roles`、`_parse_step`、`_parse_steps`、`_validate_dependencies` |
| `src/spark/providers/errors.py` | `classify_provider_error` F(52) | `_ProviderErrorClassifier` 有序规则链，13 个规则方法逐个判定 |
| `src/spark/tools/repo_context.py` | `repo_context` F(60) | `_ScanResult`、`_top_level_entries`、`_scan_repository`、`_package_manager`、`_git_snapshot`、`_render_repo_context` |
| `src/spark/web/_handlers_settings.py` | `_restore_redacted_mcp_headers` F(43) | `_mcp_http_snapshot`、`_same_mcp_destination`、`_restore_mcp_env`、`_restore_mcp_headers` |
| 同上 | `_apply_validated_settings` E(38) | `_apply_provider_settings`、`_apply_agent_settings`、`_apply_optional_sections`、`_apply_queue_limits` |
| 同上 | `_handle_settings_locked` F(52) | `_PreparedSettings`、`_display_flags`、`_protected_paths`、`_validate_provider_agent_browser`、`_unchanged_mcp`、`_prepare_settings`、`_rollback_settings`、`_commit_settings` |
| `src/spark/workbench.py` | `delivery_markdown` E(32) | `_delivery_files_section`、`_delivery_diff_section`、`_delivery_tests_section`、`_delivery_approvals_section`、`_delivery_privacy_section`、`_delivery_cost_section` |
| `src/spark/knowledge.py` | `KnowledgeBase.search` E(32) | `_bm25_scores`、`_hybrid_scores` |
| `src/spark/web/_handlers_models.py` | `_handle_models_save_locked` E(33) | `_parse_model_request`、`_update_model_profile`、`_new_model_profile` |
| `src/spark/web/_handlers_chat.py` | `_handle_chat_stream` F(42) | `_ChatRequest`、`_prepare_chat_request`、`_run_goal_auto_turns`、`_finalize_stream_turn` |

同时删除了 `repo_context` 中只累加、从未读取的 `ext_counts`，以及 `_finalize_stream_turn` 中未使用的 `turn_loop` / `collect_event` 参数。

## 3. 行为保持与新增回归

这些拆分保持原有校验顺序、错误文案、回退分支和副作用顺序。没有为了降复杂度改变默认值或放宽校验。

新增 `tests/test_mcp_settings_restore.py` 4 项，覆盖 5 类 secret 恢复边界：

- 同命令 env 恢复 redacted 值；
- 跨命令不恢复 env；
- 同 URL 恢复 secret header；
- 跨 URL 丢弃 secret header；
- 非 dict 的 MCP 配置原样保留。

其中“跨命令/跨 URL 不泄露 secret”是本轮补测的重点：原来的恢复逻辑只比对字段是否存在，容易把上一个 MCP server 的密钥带到另一个目标。

## 4. 复杂度门禁（CODE-02）

新增 `scripts/check_complexity.py` 与 `scripts/complexity-baseline.json`：

- 基线固定当前 58 个 D/E/F 块，CI 里任何新增 D/E/F 直接失败；
- 已有函数复杂度比基线增长超过 3 点失败，防止“拆了外层、内层继续长”；
- 重构后主动执行 `--write-baseline`，基线 diff 随 PR 审阅；
- CI `quality` job 安装 `radon` 并运行该脚本。

## 5. 验证

| 项目 | 结果 |
|---|---|
| 定向回归 | 246 项通过（PR 11、workflow 15、provider 22、MCP 4、repo_context 3、workbench 5、knowledge 36、settings 65、模型档案 55、聊天流 30） |
| `ruff check src/spark tests scripts` | 通过 |
| `mypy src/spark --ignore-missing-imports` | 190 files，0 error |
| 复杂度门禁 | 58 个历史块，无新增 |
| 全量 pytest | 见下方最终记录 |

最终全量 pytest：**2006 passed、13 skipped、0 failed，14 warnings，1380.11s（2026-11-25 0.15.7 收口）**。

## 6. 仍未完成

- 5 个 E 级入口保留：`run_workflow` 的暂停/续跑/并发状态、`_llm_round` 的流式工具循环、`spawn_subtasks_parallel` 的并发取消、`run_shell` 的进程/网络沙箱、`_render` 的 TUI 布局，都需要真实链路回归，不能靠纯静态拆分。
- 53 个 D 级函数仍在，主要是 CLI 命令、协议解析、内存/索引检索与 relay 处理器。
- `vulture` 的 6 条协议签名仍保留，没有为了清零删参数。
