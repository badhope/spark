# 全仓债务清扫（第二轮）

> 日期：2026-11-24
> 范围：`0.15.5` 发布后的全部未提交改动；前端、后端、测试、脚本与文档。
> 原则：先修真实行为问题，再拆有回归保护的复杂度；不为静态指标硬拆安全关键函数。

## 1. 前端：从 lint 到真实交互

第一轮 `npm run lint` 有 7 个 error、2 个 warning：

- `scripts/check-i18n.mjs` 使用 Node 的 `process`/`console`，但 ESLint 只加载了浏览器 globals；
- 同一脚本里 `stripCjk` 已无调用；
- `RemoteUnlock.tsx`、`RightPanel.tsx` 的 effect 缺 `lang` 依赖。

处理结果：为 `scripts/**/*.mjs` 单独声明 Node globals，删除死函数，补齐依赖。`npm run lint` 与 `npx tsc -b` 通过。

lint 清零后继续审阅，发现三处“没有报错但实际会咬人”的问题：

1. `FileViewer` 与 `RightPanel` 各有一份代码高亮。`RightPanel` 的逐行重建没有重开上一行未闭合的 `<span>`，跨行块注释、多行字符串会丢色或串色。已抽出 `web/src/components/CodePreview.tsx`，统一“行首重开未闭合标签、行尾补齐闭合”的实现；`RightPanel` 用 `compact` 保持原字号，`FileViewer` 用默认字号。重复实现删除。
2. `RemoteUnlock` 的自动配对 effect 依赖 `lang`。用户在配对码有效期内切换语言，effect 会重启并再次调用一次性 `claimPairing`，可能把同一个码消费两次。改为 `langRef` 读取当前语言，effect 只依赖 `autoCode`。
3. `RightPanel` 的浏览器截图轮询 effect 依赖 `lang`。切换语言会重建 interval 并立刻重抓截图。改为 `langRef` 取错误文案，轮询只依赖 `open/tab/liveMode`。

另外给 `web/src/api.ts` 的 `followSessionEvents` 加了坏帧容错：每个 `data:` 帧独立 `JSON.parse`，代理注入的非 JSON 内容只丢该帧；`reset_required` 仍抛出以触发重载。此前一个坏帧会让整条 SSE 流断掉。

最终 `npm run build` 通过：i18n gate `1119 keys aligned, 1069 t() calls resolved`。

## 2. 队列与 Autopilot：越界写、会话泄漏、账本覆盖

### 2.1 `state_path` 可写出工作区

旧实现只把相对路径拼到 workdir；绝对路径或 `../` 可以让队列任务把状态 JSON 写到工作区外任意位置。现在：

- CLI `spark queue add --state-path` 只接受 `.json` 文件名，带目录、绝对路径、`..` 直接报错；
- 服务端 `_queued_autopilot_state_path` 再次校验，最终路径 `resolve()` 后必须仍位于真实 workdir 的 `.spark/autopilot` 下；
- 即使 `.spark/autopilot` 被软链到外部，也会拒绝。

### 2.2 队列重试泄漏空会话

普通队列任务在 turn lock 争抢失败时，旧逻辑会新建 session 后直接 requeue，留下一个空会话。现在 `_discard_queued_session` 会 drop runtime + delete session，requeue 结果不再带 `session_id`。Autopilot 首次状态在等锁前落盘，重试复用同一 session，不再每次新建。

### 2.3 失败账本覆盖

终态失败重试时，旧账本归档为 `.attemptN.json`；同名文件已存在会覆盖旧诊断。现在追加短 uuid，首次失败（`attempts <= 1`）保留账本不归档。

### 2.4 复杂度与参数校验

- `_run_queued_autopilot` 从 F(45) 拆为 `_queued_autopilot_spec` / `_queued_autopilot_limits` / `_queued_autopilot_state_path` / `_load_queued_autopilot_state` / `_prepare_queued_autopilot_state` / `_execute_queued_autopilot` / `_finish_queued_autopilot`，主函数只做编排。
- `_run_queued_task` 从 D(27) 拆为 `_queued_agent_target` / `_prepare_queued_runtime` / `_collect_queued_turn` / `_record_queued_delivery` / `_discard_queued_session`。
- `max_cost_usd` 先拒绝 `NaN/Inf` 再归一；`test_timeout_sec` 钳制到 30–7200；`state_path` 的 `OSError/ValueError` 转 blocked，不再变成可重试异常。

`radon` 复查：`web/state.py` 新增函数均在 C 及以下。

## 3. 安全：同主机跨端口不再交出 API Key

`web/utils._probe_target_allowed` 原来只比较 hostname。`https://api.example` 改成 `http://api.example:8080` 仍会收到已保存密钥；本地端点也会把 key 交给同机另一端口。

现在比较精确 origin `(scheme, hostname, port)`，默认端口归一：`https://host` 与 `https://host:443` 同源；不同 scheme/port 必须显式带 key。`tests/test_adversarial_security.py` 已补回归。

## 4. 后端静态审查：只改真实风险，不为指标硬拆

- `vulture --min-confidence 80` 仍只有 6 条：`config.py` 的 `dotenv_settings`、`compact.py` 4 个 `formatted`、`providers/mock.py` 的 `raise` 后不可达 `yield`。均为协议签名/异步生成器形状，删除会破坏接口，保留并逐项判定。
- `policy.decide` 从 F(45) 拆为 `_scoped_rule_decision` / `_rule_decision` / `_browser_guard_decision` / `_mode_decision`，保持“任意层 deny 最高优先 → 浏览器域名 guard → scoped allow → allow_always → wildcard allow → 按模式兜底”的顺序。`test_policy.py`、`test_sandbox_policy.py` 共 21 项通过。
- 12 处 `zip()` 补 `strict=True`，长度不一致立即报错，不再静默截断：`automations`、`control/office`、`control/report`、`knowledge` 点积与批向量、`mcp_expose` 参数映射、`memory/service` 相似度与向量、`memory/store` 批量写入、`semantic_index` 向量写入。
- `ruff --select B007,B905` 清零：未使用循环变量改为 `_` 或索引循环。
- `.venv/bin/pytest` 原报 `ModuleNotFoundError: tests`，因为 `pythonpath` 只有 `src`；`pyproject.toml` 改为 `["src", "."]` 后 `pytest --collect-only` 收集 2014 项。CI 用 `python -m pytest` 不受影响。

## 5. 验证

| 项目 | 结果 |
|---|---|
| 队列 + Autopilot 定向 | 16 passed |
| policy / sandbox | 21 passed |
| 安全 + polish | 71 passed |
| zip 相关 7 文件 | 118 passed |
| 前端 lint / tsc / build | 通过；i18n 1119 keys / 1069 calls |
| 全量 pytest | 见下方最终记录 |

最终全量 pytest：**2001 passed、13 skipped、0 failed，14 warnings，1153.21s（2026-11-24 0.15.6 收口）**。

## 6. 仍未完成（诚实记录）

- 本轮结束时全仓仍有 70 个历史 D/E/F 函数（53 D / 10 E / 7 F）；2026-11-25 第三轮已降到 58（53 D / 5 E / 0 F），`repo_context`、`_handle_settings_locked`、`classify_provider_error`、`load_workflow`、`_handle_chat_stream`、`build_context` 均已拆，详见 [`2026-11-25-complexity-second-pass.md`](2026-11-25-complexity-second-pass.md)。
- vulture 6 条作为协议签名保留，没有为了清零删参数。
- `CODE-02` 的“新增 D/E diff 门禁”在本轮尚未接入 CI；2026-11-25 已新增 `scripts/check_complexity.py` 基线门禁，见第三轮评审。
- Authenticode、macOS 公证需要证书/Apple 账号，不能用代码假装完成。
