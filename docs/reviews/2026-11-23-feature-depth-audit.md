# 功能真实性与对标深度审计（2026-11-23）

> **定位说明（2026-11-23）**：本文写作于“本地优先”旧口径时期，该口径已废止。现行定位见 [PRODUCT_POSITIONING](../PRODUCT_POSITIONING.md)；本文仅作历史审计与事实记录，不再作为路线依据。


> 本轮不是按文档勾选，而是按 **“入口可达 → 真实调用 → 持久化 → API/UI 接线 → 测试”** 五段逐项抽样。
> 结论先行：发现并整改了一批“有代码、没生效/没落盘/没进入口”的虚表功能；Goal 自动续跑与队列每日配额已补，但 Spark 仍未达到成熟 AI 编程工具的高强度长程能力，差距集中在 Goal 跨重启续跑、队列成本额度/多 worker、Windows 隔离、OTLP 全量导出、公共插件市场与专家团队。

## 判定口径

| 等级 | 含义 |
| --- | --- |
| 深度完成 | 有真实入口；跨进程/重启后数据仍在；异常路径有明确行为；有自动化测试；边界写进文档，不靠 README 口嗨 |
| 初步可用 | 单轮能跑通，但需要人工推进或只覆盖单点，离“连续多步、可恢复、可治理”还有距离 |
| 外部桥接 | Spark 只能调外部工具/CLI，原生 UI 或平台能力不在仓库内 |
| 未完成 | 文档/UI 提过，但入口、持久化或执行链至少缺一段 |

## 一、查出并已整改的“虚表”清单

| # | 原问题（真实证据） | 整改落点 | 验证 |
| --- | --- | --- | --- |
| 1 | `deliveries` 只存 `files/tests/approvals/cost/checkpoint`；`build_delivery()` 返回的 `diff_summary/metrics/privacy` 写库后读回全丢，导出把本地模型误写成 `remote` | 建表 + v5 迁移加 `diff_json/metrics_json/privacy_json`；`save_delivery()` 写入、`_delivery_row()` 映射回来；导出补 `## Diff` / `## 成本` | `tests/test_workbench.py` |
| 2 | `list_tool_events()` 用 `ORDER BY id LIMIT 500`，长会话取到最早 500 条，账本窗口错位 | 改为 `ORDER BY id DESC LIMIT ?` 后在 Python 反转；新增 `latest_tool_event_id()` 给账本取 turn 起点 | 同上 |
| 3 | 自动账本的工具集合写了 `multi_edit`、`replace_in_file`，这两个工具在 `ToolRegistry` 里不存在；真会改文件的 `apply_patch`、`notebook_edit` 反而没接 | 改为 `write_file/apply_patch/apply_edit/notebook_edit/git_commit` | 同上 |
| 4 | 自动账本把整个 worktree 的 `git status` 都算到当前任务，`run_shell("ls")` 也被记成测试证据 | 加 `since_id` 窗口；只从写工具参数提取 touched paths；按路径过滤 diff；只认 `run_tests` 或命中测试运行器的 `run_shell`；无路径才回退全工作区，并写入 `attribution=tool_events|worktree` | 同上 |
| 5 | Web 工作台 metrics 取 `metrics[length-1]`，接口是 newest-first，实际显示最旧一轮；`workbench_snapshot` 的 `by_session` 保留同会话最旧 delivery | 改取 `[0]`；改为保留最新 delivery | 同上 |
| 6 | 插件签名只签 `spark-plugin.json`，替换 `entry.py`/hook 脚本后仍是 `signed`，安全功能“有签名、没保护代码” | `package_digest(root)` 对 manifest 外每个文件按“相对路径+SHA-256”算总摘要；manifest 增 `artifact_sha256`；`verify_manifest(root=...)` 必须同时校验签名与包摘要；`sign` 先算摘要再签；`install_local()` 在 staging 校验，`invalid/revoked` 拒绝安装 | `tests/test_plugin_trust.py` 7 项：manifest-only 不再通过、改 `entry.py` 变 invalid、篡改包安装被 `PluginError` 拒绝 |
| 7 | 内联编辑 `edit` 模式只有选中文本和指令，没有文件上下文；VS Code 在模态框期间文件被改后仍按旧 Range 应用，可能覆盖错位置 | prompt 注入 `<before_selection>/<after_selection>`；Web handler 在选中文本唯一时读文件并注入前后各 3000 字符与语言推断；VS Code 捕获 `document.version` 与起止 offset，版本变化直接取消，应用时重建 Range | `tests/test_inline_edit.py` 4 项；`tsc` 通过 |
| 8 | “归档”此前只有删除会话，没有归档/恢复；矩阵自己把它标成假 ✅ 后降级 | `sessions.archived_at`（v6）+ `archive_session()`；`/api/sessions/archive` 仅本机/`device_admin`；默认列表隐藏归档；归档当前会话自动切到下一个活跃会话；切到归档会话隐式恢复；Web 侧栏“已归档”分组/恢复按钮/`listSessions(true)` | `tests/test_session_archive.py` 2 项：隐藏/恢复/不删消息、归档当前会话自动切换 |
| 9 | Claude Code 兼容只到 `.claude/agents`，没有 `.claude/skills`/`commands` | `skills.discover_skills()` 扫 `.claude/skills`、`~/.claude/skills`；`slash.discover_commands()` 扫 `.claude/commands`、`~/.claude/commands`；同名 `.spark` 原生优先，只读 | `tests/test_skills.py`、`tests/test_profiles_and_commands.py` 通过 |
| 10 | TUI `/editor`、`/vim` 的代码写了但没进 `KNOWN_COMMANDS`、没进输入提交分支，属于死代码 | `KNOWN_COMMANDS` 加 `editor/vim`；`_run_editor_slash` 用 `$VISUAL`/`$EDITOR`（`/vim` 强制 vim），路径经 sandbox 解析，`self.suspend()` 挂起后 `subprocess.run`，退出恢复刷新；`SHORTCUTS` 补说明 | TUI 71 项定向回归通过 |
| 11 | `otel.py` 只有 turn/tool span，审批等待与工作流执行不可观测 | 新增 `spark.approval`（工具/摘要/决定/等待毫秒）、`spark.workflow` / `.step`（名称/版本/步数/并发、step/attempt/status/cost、失败步数/总成本）；`core/loop.py` 审批链与 `workflow.run_workflow` 已接线；同时接入 OTLP metric reader 与显式审计 log（审批决定/工具失败/工作流结束），不是只造 span | `tests/test_otel.py` 6 项通过，包含 metric/log in-memory 导出断言；`test_workflow.py` 20 项回归通过 |
| 12 | Goal 只有目标契约 + 证据门槛，完成后必须用户手动再发消息 | `agent.goal_auto_turns`（默认 2）：同一 SSE 流内自动续跑，序号连续不重置；任一回合失败立即熔断；前端 `goal_auto_continue` 通知；设置页可改 0–20 | `tests/test_web_slash_commands.py::test_goal_auto_continue_emits_extra_turns` 通过；67 项设置/队列/ slash 定向回归通过 |
| 13 | 空闲队列没有配额/超时，无人值守可无限消耗或卡死 | `agent.queue_daily_limit`（0=不限）按本地日统计 ok/failed/cancelled；`agent.queue_task_timeout_sec` 经 `AgentRuntime.iterate(max_seconds=...)` 总时长守卫，超时抛 `TimeoutError` 并回 queued 重试；设置页均可改 | `test_task_queue.py` 8 项通过（配额停止、timeout 传递）；`test_agent_runtime` 新增总时长超时取消慢生成器通过 |
| 14 | OTel 只造 span，没有真实 metric/log 导出；文档写 `http://:4318` 但 exporter 是 gRPC | 三路 provider 都自管：`InMemoryMetricReader`/`PeriodicExportingMetricReader` + `LoggerProvider`/`BatchLogRecordProcessor`；`_grpc_endpoint` 统一剥 `grpc://`/`http(s)://`；审批/工具失败/工作流结束写显式 log record | `tests/test_otel.py` 6 项通过；文档改为推荐 `grpc://host:4317` 并标注 HTTP/全量 logging 未接 |

> 说明：第 6 条会让“旧版只签 manifest”的插件显示 invalid，这是有意的安全收紧，不是回归。

## 二、逐项核验原对标清单

| ID | 原差距 | 当前结论 | 证据/边界 |
| --- | --- | --- | --- |
| RC-01 | relay 只做缓冲式 HTTP，SSE 会整包等待 | ✅ 完成 | `relay.py` `forward_stream` headers/chunk/end；`tests/test_relay.py` 流式用例 |
| RC-02 | relay 不自带 Web 入口/静态代理 | ✅ 自托管完成；官方托管 ⏸ | `relay serve` 代理 `/`、`/assets/*`、`/sw.js`、`/api/*`；官方托管需域名/TLS/成本决策 |
| RC-03 | 手机只是响应式桌面 Web | ✅ 完成 | `MobileShell.tsx`、`useIsMobile.ts`、审批两步、底部导航/安全区/PWA/语音降级；UI 布局与交互测试 |
| RC-04 | 只有 turn_lock，无会话 owner/租约/接管 | ✅ 完成 | `web/state.py` `lease_owner/until/claim/renew/release`、`web/lease.py`、`/api/session/lease`；`test_session_lease.py` |
| RC-05 | 只有 device 只读/运行与 local 全控 | ✅ 完成 | 30 分钟 `device_admin`、字段级拒 shell/PTY/remote link；授予/到期/收回审计；`test_device_admin.py` |
| AQ-01 | 知识库实际只有 BM25 | ✅ 完成 | BM25 + 向量混合、`vectors.json` sidecar、旧索引回填、失败降级；`test_knowledge.py` 36 项 |
| AQ-02 | 压缩丢旧工具输出/历史不可检索 | ✅ 完成 | 5 段摘要硬校验、工具输出三档裁剪、`compact_from` 有界召回；压缩/上下文测试 |
| AQ-03 | 错误工具选择/长参数无强校验 | ✅ 完成 | JSON Schema 子集、未知/禁用工具审批前降级、2 MiB 参数上限、一次修正、按任务裁剪 schema；61 项 |
| AQ-04 | 无 Lead+专家团队/Team Canvas | ⏸ 未完成 | 只有 task 子代理 + workflow DAG，没有 Lead 拆解/派发/验收与 Team Canvas；需产品决策 |
| DS-01 | Windows 无 Authenticode | ❌ 未完成 | 只有 SHA256 更新校验；需代码签名证书与 `signtool verify /pa` |
| DS-02 | macOS 无 `.app/.dmg`/公证 | ❌ 未完成 | 只有 `arm64 tar.gz`；需 Apple Developer 账号与 notarytool |
| DS-03 | Windows 无文件系统/网络隔离 | 🟡 部分 | 只有 Job Object 进程树 + 内存上限；无 AppContainer/受限令牌/文件 ACL，断网不真断 |
| DS-04 | 无 turn/tool/approval/workflow 全链路 | 🟡 部分 | 本轮补齐 approval/workflow/step span；仍缺 OTLP log/metrics 全量导出与独立审计导出 |
| EX-01 | 无公共/个人市场与签名分发 | 🟡 部分 | manifest+代码包摘要签名、篡改拒装、trust/revoke、`require_signature` 已有；公共索引/上架审核/撤销治理仍 ⏸ |
| EX-02 | TUI 只有 `/test`，无 `/editor`/Vim | 🟡 部分 | `/editor`/`/vim` 外部编辑器可达；composer 内 Vim 键位与编辑后回填未做 |
| EX-03 | i18n 无门禁 | ✅ 完成 | `npm run build` 内置 `check:i18n`；当前 1080 keys、1022 个 `t()`、无硬编码中文 |
| EX-04 | Claude 兼容只到 agents | ✅ 完成 | `.claude/skills`/`commands` + 用户级目录，原生优先 |
| EX-05 | 队列无免费额度 + 超时回队列 | ❌ 未完成 | `TaskQueue` 单 worker，只有暂停/重试/最大 attempts；无配额、无运行超时回收 |
| DOC-01/02 | 矩阵与 docstring 漂移 | 🟡 本轮重校 | 归档已从假 ✅ 修正为真 ✅；文档已同步本轮；但“所有 ✅ 行”仍需持续门禁，不靠一次人工核对 |

## 三、深度判定：哪些只是“能用”，哪些真做深了

**相对扎实、可称为深度完成的：**

- **Workflow DAG**：有界并发、文件锁、暂停续跑、单节点重跑、成本上限、worktree 隔离，且有 workflow/step span；这是当前最接近成熟工具编排的部分。
- **交付账本**：现在能跨重启读回 diff/metrics/privacy，能按 turn 窗口归属文件与测试，失败态区分 rejected，成本标 `scope=session`；审计口径比之前诚实得多。
- **插件安全**：从“签 manifest”升级到“签代码包”，安装期拒绝篡改/撤销包；这在本地插件模型里是实打实的防线。
- **归档/恢复**：不只是 UI 按钮，存储层保留消息与检查点，API 有权限边界，归档当前会话能自动切换，测试覆盖。
- **LSP / 浏览器 / relay / 租约 / 审批治理 / 移动壳**：入口、状态、异常路径基本闭环，属于可用且不浅。

**只是初步可用，离“成熟 AI 编程工具”还有距离的：**

- **Goal 长程模式**：目前是“目标契约 + 证据门槛”，不是自动长程执行；用户要手动继续，连续失败没有熔断/回滚策略。对标 ZCode/Codex 的 `/goal`，还差自动续跑与迭代验证。
- **空闲队列**：单 worker；每日配额与单任务超时已加（超时回 queued 重试），但成本额度、多 worker 并发仍未做；高强度批量任务仍会卡在单点。
- **TUI**：`/editor`/`/vim` 是挂起外部编辑器，不是 composer 内 Vim 模式；斜杠命令也远少于 codex 60+/opencode 全量。
- **OTel**：有 trace/span，但没有 OTLP log/metrics 全量导出与独立审计导出；只能算“可观测”，不是“可运维”。
- **Windows 沙箱**：只有进程树与内存上限，文件系统/网络隔离没做；不能把“本地优先”说成“强隔离”。
- **插件市场**：有签名与拒装，但没有公共索引、上架审核、撤销列表治理；离“市场”还有产品层工作。
- **专家团队/Team Canvas**：未做；只有子代理和 DAG，没有 Lead+专家角色编排。
- **完整 Tab 补全**：仍按门禁后置，当前只有 NES/内联编辑；这不是“没做”，但也不能算对标完成。

**高强度运行能力的真实标尺：**

- 30 任务矩阵 qwen 30/30 PASS，但这是**饱和矩阵**，不能证明复杂多文件重构能力；TTFT p50 4151ms / p95 7818ms，turn p50 35.8s / p95 113.8s，真实模型延迟仍是硬约束。
- 单 worker 队列、无超时回收、无 Windows 文件隔离，决定了它现在更像“本地优先、可审计的 AI 编程工作台”，不是“可无人值守跑一整天的高强度编程代理”。

## 四、验证记录

- 定向回归：79 passed（TUI app/pilot/welcome、workflow、otel、p0 telemetry、p1 testcmd、profiles/commands）。
- 上一批定向：42 passed（workbench、inline_edit、plugin_trust、session_archive、skills、profiles/commands、store 等）。
- 前端：`npx tsc --noEmit` 通过；`npm run build` 通过；`check:i18n` 1080 keys / 1022 `t()` / 0 硬编码中文。
- 全量 pytest 并行 4 worker：1916 passed / 16 skipped；5 个 UI 用例在并行下因 `can't start new thread`/Playwright 超时失败，**串行复跑 5/5 全部通过**（84.6s）。目标/队列/设置改动后，67 项定向回归再次通过。

## 五、第十轮增量（2026-11-23）

| 项 | 原状 | 本轮结果 | 真实性核验 |
|---|---|---|---|
| Goal 跨重启 | 只在同一条 SSE 流内自动续跑，刷新/重启后目标不可见 | `/api/history`、会话切换、分叉响应回显 `goal`；Web 聊天页显示活跃目标横幅，点“继续推进”注入带目标契约的提示 | 后端测试断言 history/switch 都返回 active goal；前端 `activeGoal` 由历史与 `goal_status` 事件同步 |
| EX-02 Composer Vim | 只有 TUI 外部 `/vim` | Web composer 可选 Vim 模式：Esc→Normal，i/a/I/A→Insert，h/j/k/l、w/b、0/$、x、dd、o；`VIM · N/I` 状态，localStorage 记忆 | 不是完整 Vim 状态机（无宏/寄存器/可视），但键位真实进入 textarea 键盘处理，不是装饰开关 |
| EX-05 队列 | 每日任务数配额 + 单任务超时，单 worker，无成本额度 | 加 `queue_daily_cost_usd`（按 `agent_queue.cost_usd` 累计，失败重试也计）、`queue_workers` 1–4；队列 runtime 标 `origin=queue`，多 worker 不互相阻塞 | 新增 5 项队列单测；成本写入 schema v7，`finish/requeue` 都累加；worker 数上限 4、重启生效 |
| DS-04 OTel | trace/span + 显式审计 log，无 stdlib 桥/HTTP/采样/脱敏 | `SPARK_OTEL_BRIDGE_LOGGING` 桥接 stdlib logging；`SPARK_OTEL_SAMPLE_RATIO`；审计 attributes 凭据脱敏；HTTP exporter 可选依赖自动启用，否则 gRPC 回退 | 新增 2 项 OTel 测试（日志桥真实落 exporter、api_key/password 脱敏）；HTTP 路径代码存在但本机未装可选依赖，未做真实网络断言 |
| 设置回显 | 新字段保存成功但 `/api/config` 不回显，UI 读回默认值 | `full_config()` 补 `goal_auto_turns` / `queue_daily_limit` / `queue_daily_cost_usd` / `queue_task_timeout_sec` / `queue_workers` | `test_settings_roundtrip.py` 3 项通过，动态遍历新增字段也保存往返 |
| 设置测试误判 | 说明文字含“失败/错误”被 `_save` 当成保存失败 | 测试只认“保存失败”正式提示，不再拿整段正文做关键词 | 3 项设置往返由失败转为通过；产品代码未为测试改语义 |

**更新后的深度判断：**

- 队列的“本地节流闭环”基本补齐：任务数、估算成本、单任务墙钟、进程内多 worker 都有；仍不是分布式队列，worker 数修改要重启。
- Goal 仍是“有人在场的长程模式”：跨重启可见、一键继续，但没有无确认的后台自动开跑；这是产品安全边界，不是漏做。
- Composer Vim 是“日常移动/删除/插入”级别，不是 codex 的完整 Vim；对外不应宣传为完整 Vim。
- OTel 客户端能力闭环，但 HTTP exporter 是可选依赖；默认安装仍以 gRPC 4317 为准。
- 仍无法在本机交付的：Windows Authenticode、macOS 公证、Windows 文件系统/网络强隔离、公共插件市场治理、官方托管 relay、JetBrains 原生 inline UI、AQ-04 专家团队。

### 第十轮验证（干净结果）

- 非 UI 全量（xdist，排除 `tests/test_ui_*.py`）：**1897 passed / 16 skipped**，184.76s，exit 0。
- UI 全量串行（7 个文件、36 项，含新增 Vim 与 Goal 刷新横幅）：**36 passed**，307.03s，exit 0。
- 前端：`npm run build` 通过；i18n 门禁 **1096 keys / 1040 个 `t()` / 0 硬编码中文**。
- 定向：设置往返/并发设置/队列/OTel/Goal 共 **35 passed**；其中设置往返 3 项、队列 12 项、OTel 8 项。
- 之前把 UI 用例放在 xdist 并行里会因 Playwright/线程资源出现假失败；本轮改为“非 UI 并行 + UI 串行”，不再用并行失败掩盖产品问题。
