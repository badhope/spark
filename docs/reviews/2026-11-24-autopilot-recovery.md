# Autopilot 崩溃恢复与三路指纹（2026-11-24）

> 范围：`src/spark/autopilot.py`、`src/spark/cli.py`、`tests/test_autopilot.py`、`tests/test_cli_autopilot.py`。
> 目标：把 Stage B 的“崩溃恢复 + 心跳 + 无进展指纹”从文档条目变成可复验行为。

## 1. 已交付行为

| 机制 | 实现 |
|---|---|
| 轮次开始前落盘 | 写 `turn_in_progress=true`、`active_prompt`、`turn_started_at`、`pid`、`heartbeat_at` |
| 中断轮恢复 | `AutopilotState.prepare_resume()` 把 `turn_in_progress` 标为中断、计数 `recovered_interruptions`、恢复原 `active_prompt`，不跳过未验证轮次 |
| 审计中断 | 若中断发生在 `audit` 阶段，恢复时强制 `audit_done=false`，重新复核 |
| 长轮次心跳 | `--heartbeat-interval` 默认 30 秒；只更新状态文件 mtime 与内存时间戳，不并发重写 JSON 账本 |
| 三路无进展 | diff 指纹 + 测试输出指纹 + 工具调用指纹（工具名 + 参数摘要 SHA256）同时不变才累计；模型换一句总结不算进展 |

## 2. 验证

定向：

```text
tests/test_autopilot.py tests/test_cli_autopilot.py tests/test_autopilot_e2e.py
27 passed in 23.20s
```

其中真实 `SIGKILL` 测试：fork 子进程进入模型轮次，父进程确认状态文件已是
`turn_in_progress=true` 且 `pid` 匹配后发送 `SIGKILL`；重新加载状态、
`prepare_resume()`、再跑一个假 turn 与独立 verifier，最终 `passed`，且
`recovered_interruptions=1`。

全量：

```text
pytest -q
2005 items: 1989 passed, 16 skipped, 0 failed
974.11s
```

静态：

```text
ruff check src/spark/autopilot.py src/spark/cli.py tests/test_autopilot.py tests/test_cli_autopilot.py
mypy src/spark --ignore-missing-imports
mkdocs build -q
```

均通过。

## 3. 边界

- 已验证的是 runner 级 POSIX `SIGKILL`，不是 CLI + 真实 AgentLoop + 外部仓库长跑的外部杀进程；真实工具可能已部分写盘，恢复后靠独立 verifier 重新判断，不承诺回滚半成品。
- 心跳只证明进程还在触碰状态文件，不证明模型在有效推进；推进会由三路指纹与测试结果判断。
- 无进展指纹的“工具调用”只含名称与参数摘要，不保存完整参数内容；这是审计需要，不是模型上下文。
