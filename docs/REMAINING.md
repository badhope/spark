# 剩余能力清单（可追踪）

> 这份文件是 `docs/competitive-comparison.md` 的**执行视图**：矩阵回答"差在哪"，
> 这里回答"还剩哪些、为什么没做、做了算完成的标准是什么"。
> 每次关闭一项就在这里改状态，避免"文档说要做什么"和"实际做了什么"再次分叉。

状态：✅ 已交付 ｜ 🟡 部分交付 ｜ 🚧 进行中 ｜ 📋 已排期 ｜ ⏸ 需产品决策 ｜ ❌ 明确不做

> **2026-11-23 定位修订**：本文是能力对账视图，保留历史清单。现行定位是
> **浏览器原生、跨平台、开源可扩展的 AI 编程与办公工作台**（唯一口径见
> `docs/PRODUCT_POSITIONING.md`）。核心模块差距见 `docs/CORE_CAPABILITY_GAPS.md`，
> 外部开源/MCP 接入见 `docs/INTEGRATION_MAP.md`。下文“明确不做”的旧结论中，
> 与“本地优先”绑定的部分不再有效；仍有效的门禁（如完整 Tab 补全、强隔离边界）保留。

## 已关闭（本轮及之前）

| 能力 | 状态 | 落点 |
| --- | --- | --- |
| 会话归档/恢复 | ✅ | `store.archive_session()` + `sessions.archived_at`（v6）；`/api/sessions/archive` 仅本机/`device_admin`；Web 侧栏“已归档”分组、归档/恢复、归档当前会话自动切换；`tests/test_session_archive.py` 2 项 |
| 插件代码包签名 | ✅ | `package_digest()` 对 manifest 外所有文件做路径+SHA256 总摘要；`PluginManifest.artifact_sha256`；`verify_manifest(root=...)` 缺摘要/包变更均 invalid；`install_local()` 在 staging 校验并拒绝篡改/撤销包；`tests/test_plugin_trust.py` 7 项 |
| Claude skills/commands 兼容 | ✅ | `skills.discover_skills()` 扫 `.claude/skills`、`~/.claude/skills`；`slash.discover_commands()` 扫 `.claude/commands`、`~/.claude/commands`；同名 `.spark` 原生优先，只读不写；`tests/test_skills.py` / `test_profiles_and_commands.py` |
| TUI `/editor` / `/vim` | ✅ | `slash.KNOWN_COMMANDS` 注册 `editor`/`vim`；TUI `_run_editor_slash` 经 sandbox 解析路径后挂起 `self.suspend()`，调 `$VISUAL`/`$EDITOR`（`/vim` 强制 vim），退出恢复；`tests/test_tui_app.py` / `test_tui_pilot.py` / `test_tui_welcome.py` 回归通过。**边界**：这是外部编辑器，不是 composer 内 Vim 键位模式 |
| OTel 全链路（trace/metric/log） | ✅ | `otel.py` 有 `spark.approval`、`spark.workflow`/`.step`，metric（token/延迟）与显式审计 log（审批决定/工具失败/工作流结束）走 OTLP gRPC；`tests/test_otel.py` 6 项通过。**边界**：不是 Python logging 全量桥接，HTTP OTLP 未接 |
| 交付账本真实性 | ✅ | `deliveries` 增 `diff_json/metrics_json/privacy_json`（v5 迁移）；`list_tool_events()` 取最新 500 条再反转；自动入账按 `since_id`、写工具路径与真实测试命令归属；导出补 Diff/成本；`tests/test_workbench.py` |
| 定时任务 | ✅ | `automations.py` + 设置→定时任务 |
| 写后格式化 | ✅ | `tools/formatting.py`，`agent.format_on_write` |
| 交付账本自动入账 | ✅ | `web/_handlers_chat.py` 在写文件/提交后自动 `build_delivery()`；失败回合写 `rejected`，只读回合保留手动入口 |
| 语义索引后台构建 | ✅ | `core/loop.py` `_start_semantic_build`；首轮不阻塞，次轮起可用；手动重建仍保留 |
| 插件签名策略 | ✅ | `[plugins].require_signature` + 设置页开关 + `/api/plugins action=policy`；仅本机/`device_admin` |
| 额外目录 `--add-dir` | ✅ | `sandbox.py:extra_roots`，`agent.extra_dirs` |
| 断网执行 | ✅ | `agent.network_access` + bwrap 空网络命名空间 |
| 自动审查 | ✅ | `core/review.py`，`agent.auto_review` |
| 符号定位 | ✅ | `tools/symbols.py`，`find_symbol` |
| 配色主题 | ✅ | `theme.ts` + `index.css`，5 套与明暗正交 |
| 调试诊断 | ✅ | `web/_handlers_debug.py` + 设置→诊断 |
| 权限/审批治理写接口 | ✅ | `web/_handlers_governance.py` |
| 消息级 undo 带文件还原 | ✅ | `/api/messages/truncate` `restore_files` |
| 子代理独立模型 | ✅ | `DeclaredAgent.model` |
| 子会话导航（子代理树） | ✅ | `subtask_start` 事件携带 `agent`/`prompt`（`core/loop.py`）+ ToolBlock 子代理轨迹卡片（声明名/任务描述/进度条） |
| `spark uninstall` 卸载命令 | ✅ | `cli.py uninstall_cmd`：清单 + 确认 + 家目录守卫；`--list` / `--yes`；文档见 INSTALL.md |
| 包管理器分发（brew/scoop） | ✅ | `bucket/spark.json`（scoop bucket，带 autoupdate，哈希取自 Release asset digest）；brew 需独立 tap 仓库，仍未做 |
| 状态栏定制（TUI） | ✅ | `agent.status_items` 有序项（`tui/welcome.py:status_text`），未知项跳过、目录空自动省略 |
| 压缩提示词可编程干预 | ✅ | `.spark/prompts/compact.md`（项目）→ `~/.spark/prompts/compact.md`（全局）覆盖内置提示词，摘要与合并两路径都生效 |
| A2A Server（Agent2Agent 协议） | ✅ | `spark a2a-serve`：Agent Card + message/send + tasks/get（`a2a_server.py`，9 测试）；流式列为 ⏸ |
| A2A 流式（message/stream SSE） | ✅ | capabilities.streaming=true；SSE 逐帧下发 TaskStatusUpdateEvent + TaskArtifactUpdateEvent（终帧全量替换），非流式 message/send 复用同一执行路径 |
| 侧聊 / 旁支（`/side`） | ✅ | `/side <问题>`：辅助模型（small_model 优先）一次性作答，带主对话最近请求作背景；不污染主对话历史，UI 旁支卡片 |
| Goal 长程模式（`/goal`） | ✅ | `/goal <目标>`：goals 表持久化 + 目标契约注入系统提示词 + `goal_update` 工具（**证据门槛**：complete 需至少一条 done 且带可核查 evidence，否则拒绝）；`agent.goal_auto_turns`（默认 2）在同一 SSE 流内自动续跑，任一回合失败立即熔断；`/api/history`、切换/分叉回显活跃 Goal，刷新/重启后聊天页显示“继续推进”横幅。**边界**：跨重启是可见 + 一键续跑，不是无确认的后台自动开跑 |
| 主 agent 切换（build/plan） | ✅ | `/plan`（切 plan-only 只读档）/`/build`（恢复并重套审批档案）；复用审批档案 override 机制，零配置突变 |
| 插件市场 v1（策划式） | ✅ | `spark mcp search/add/list/remove` CLI 与 Web 注册表同源（live + 离线策划索引 + 中国可用性标注）；签名分发仍列在高成本项 |
| 手机端首页与底部导航（RC-03） | ✅ | `web/src/components/MobileShell.tsx` + `useIsMobile.ts`：<768px 独立手机壳，首屏待审批/当前会话/新任务，底部固定导航与安全区；审批卡片“查看并审批”两步完成；`Composer` 浏览器 SpeechRecognition 语音输入，失败回落文本；普通访问注册 PWA `sw.js`；`tests/test_ui_a11y.py` 390×844/1440×900 无横向溢出 8 项、`tests/test_ui_browser.py` 9 项通过 |
| 限时 device_admin（RC-05） | ✅ | `remote.py` 的 scope/到期 + `device_admin.py` 字段与路径边界 + `/api/remote/admin`；本机授权 30 分钟，非 shell 设置/插件可用，shell/PTY/remote link 拒绝；到期自动降权且重新配对才能再申请；`tests/test_device_admin.py` 3 项、远程/路由/SSE 137 项回归通过 |
| 本地模型目录扩展 + Mock 展示分离 | ✅ | `local_models.py` 内置 5 个 GGUF（MiniCPM5-1B/2B、Qwen3.5-2B/4B、BotPsy-0.8B），ModelScope + hf-mirror 双直链、精确体积与 SHA256 sidecar；`providers/factory.py` 缺 Key 返回 `UnavailableProvider` 而非 Mock，`/api/demo` + 设置页一键进入/退出展示模式并保存/恢复真实档案，真实错误（缺 Key、模型未下载、连接失败）按原样显示 |
| i18n key 对齐门禁（EX-03） | ✅ | `web/scripts/check-i18n.mjs` + `check:i18n` 进 build：zh/en 1007 key 对齐、en 词典无中文、957 个 `t()` 调用必须命中、JSX/字符串中文硬编码失败；补齐 provider/MCP/本地模型英文数据；`tests/test_ui_i18n.py` 浏览器验证英文主界面和模型设置 0 中文 |
| 竞品矩阵逐行核对（DOC-01） | ✅ | 86 个 ✅ 行全部有代码/测试落点；36 个原先无落点的行已补齐；核对发现“归档”只有删除、无归档/恢复，已降为 ⚠️；记录见 `docs/reviews/2026-11-22-matrix-audit.md`，矩阵复测日期更新为 2026-11-22（第八轮） |
| 测试收口（全量回归 + UI 全交互） | ✅ | 非浏览器全量 1859 passed / 16 skipped；`tests/test_ui_full_interaction.py` 8 项、UI 七文件 34 项通过；修复 `update_plan` status 可选、4 个旧断言、Chromium 崩溃兜底、真实模型下载与预期 409 按钮跳过；记录见 `CHANGELOG.md` |
| 会话租约与接管（RC-04） | ✅ | `web/state.py` 的 `SessionRuntime` 租约 + `web/lease.py` 写入门 + `/api/session/lease`；设备 chat/stream/browser 自动认领，审批/问答/取消只认持有设备或本机；心跳续租、120 秒释放；`tests/test_session_lease.py` 3 项、远程/路由定向回归 134 项通过 |
| 远程控制 2.0（扫码/在线/审计/推送） | ✅ | `web/remote_access.py` + `web/remote.py` + `web/web_push.py`：二维码一次性链接、7 天设备令牌、LAN/公网入口、60 秒在线心跳、`remote-audit.jsonl`、审批 Web Push；详见 `docs/REMOTE.md`，测试 `tests/test_remote_access.py` / `test_remote_presence.py` / `test_web_push.py` |
| 项目知识库混合检索 | ✅ | `knowledge.py`：BM25 始终兜底，开启 `memory.embedding_enabled` 后叠加向量召回；`vectors.json` sidecar、旧索引首次检索回填、embedding 失败/维度不一致降级；`tests/test_knowledge.py` 36 项通过 |
| 内置可驱动浏览器 | ✅ | `tools/browser.py` 21 个 `browser_*` 工具，Playwright 后台线程、ref 快照、点击/输入/上传/截图/PDF/控制台/网络；Chromium/Chrome/Edge/Firefox/WebKit 五引擎、持久化 Profile、CDP 接管已运行浏览器；右侧“网页”工作台复用同一浏览器 |
| Git PR 工作流 | ✅ | `pr.py` + `tools/pr_tools.py`：`pr_context` / `pr_view` / `pr_diff` / `pr_checks` / `pr_review`，`spark pr publish --confirm` 才写评论；GitHub / GitCode / Gitee |
| IDE/ACP 深度集成 | ✅ | `vscode-spark/` VSIX（聊天面板、选区上下文、内联 diff、诊断）、`spark ide install` 覆盖 Zed / JetBrains 2025.3+ / Neovim；Neovim 额外带 `<leader>si` 选中区内联桥，JetBrains 用 `spark inline` External Tool 桥；真实客户端冒烟仍标注为环境限制 |

## 收口计划（2026-11-22，接在现有轨道上）

> 这不是新项目：不新开仓库、不新起服务、不另起编排引擎、不做第二套移动端。
> 每条都接回现有模块和设置页；新 API 只在现有 `/api/*` 命名空间内增补。
> 成本：S ≤ 1 天，M 2–4 天，L 1–2 周，XL 需要外部条件或产品决策。
>
> 进度 2026-11-22：**DOC-02 ✅**（知识库 docstring 统一）；**AQ-01 ✅**（BM25 + 可选向量混合、`vectors.json` sidecar、旧索引回填、36 项知识库测试）；**AQ-02 ✅**（5 段摘要缺段校验/补齐、工具输出 2k/8k 三档裁剪、压缩前历史按 `compact_from` 有界召回并注入；29 项压缩/上下文测试、116 项定向回归通过，全量 pytest 未跑）；**AQ-03 ✅**（JSON Schema 子集校验、未知/禁用/白名单外工具审批前降级、参数 2 MiB 上限、一次修正、按任务裁剪 schema；61 项 AQ-03 测试、10 文件 158 项定向回归通过）；**RC-01 ✅**（relay 支持 SSE/chunked 流式转发）；**RC-02 ✅**（relay 根路径可代理真实 Spark Web/API/SSE，配对链接复用 relay base URL，7 项 relay 测试通过）；**RC-04 ✅**（会话租约、设备冲突拒绝、审批/问答/取消限持有设备、心跳续租与 120 秒释放、双设备真实 HTTP 测试 3 项通过）；**RC-05 ✅**（一次性 30 分钟 `device_admin`、字段级 shell/审批/沙箱拦截、插件启停、到期自动降权与重新配对限制、授予/到期/收回审计；3 项管理档测试通过）；**RC-03 ✅**（<768px 独立手机壳、审批优先首页、固定底部导航、安全区、浏览器语音输入与文本降级、PWA 通用 Service Worker 注册；390×844 首屏见待审批/当前会话/新任务，审批两步内完成，无横向溢出；UI 布局 8 项与交互 9 项通过）；**EX-03 ✅**（i18n 门禁进 `npm run build`：1007 个 key 中英对齐、957 个 `t()` 调用可解析、英文词典无中文、JSX/字符串硬编码中文失败；补齐 5 个本地模型英文说明、provider/MCP 预设英文字段，浏览器实测英文主界面与模型设置 0 个中文；`tests/test_ui_i18n.py` 1 项通过）；**DOC-01 ✅**（86 个 ✅ 行逐行核对并内联代码/测试落点，36 个空落点补齐；“归档”由假 ✅ 降为 ⚠️，矩阵复测日期更新为 2026-11-22 第八轮）；**测试收口 ✅**（非浏览器全量 1859 passed / 16 skipped，UI 全文互 8 项与七文件 34 项通过，修复 `update_plan` schema、4 个旧断言与 Chromium 启动兜底）。

### A. 远程闭环（RC）

| ID | 优先级 | 差距 / 现状 | 接回哪里 | 完成标准 | 成本 / 条件 |
| --- | --- | --- | --- | --- | --- |
| RC-01 | P0 | **✅ 2026-11-22 已完成**。原差距：relay 只做缓冲式 HTTP，SSE/流式会被整包等待 | `src/spark/relay.py`、`cli.py relay`、`docs/RELAY.md` | `httpx.stream` + chunked 转发 + SSE 事件透传；经 relay 转发 SSE 端点时逐 token 刷新，审批/工具事件不延迟到回合结束；新增 relay 流式测试。`RelayAgent.forward_stream` 按 headers/chunk/end 事件回传，`/v1/channels/{id}/stream` 内部端点 + `/call {"stream":true}` chunked 客户端；`tests/test_relay.py` 的流式/roundtrip 用例通过 | M |
| RC-02 | P0 | **✅ 2026-11-22 已完成**。原差距：relay 不自带 Web 入口/静态代理，手机仍需自建 HTTPS 反代 | `relay.py`、`web/server.py`、`remote_access.py` | `spark relay serve --channel/--key` 根路径代理 `/`、`/assets/*`、`/sw.js`、`/api/*`，SSE 走 chunked；`spark web --public-url https://relay` 生成 relay 配对链接，手机扫码打开 relay 根并领取一次性配对码；单频道自托管，官方托管 relay/账号体系仍 ⏸。`tests/test_relay.py` 6 项 + `tests/test_relay_web.py` 1 项通过 | M–L；需自托管域名/TLS |
| RC-03 | P0 | **✅ 2026-11-22 已完成**。原差距：手机端只是响应式桌面 Web，没有底部导航、审批优先首页、语音输入 | `web/src/App.tsx`、`web/src/components/MobileShell.tsx`、`useIsMobile.ts`、`Composer.tsx`、`ApprovalModal.tsx` | 390×844 独立手机壳：首屏直接看到待审批/当前会话/新任务；审批卡片“查看并审批”后进入详情，两步内完成；底部固定四栏导航（首页/对话/新建/设置）并留出 `safe-area-inset-bottom`；页面级无横向滚动；PWA manifest + 通用 `sw.js` 注册，`Page.getInstallabilityErrors` 为空；语音走浏览器 `SpeechRecognition`（`voice.ts`），不支持/拒绝/无结果时 toast 提示并保留键盘输入；`tests/test_ui_a11y.py` 8 项、`test_ui_browser.py` 9 项通过 | M |
| RC-04 | P1 | **✅ 2026-11-22 已完成**。原差距：只有 `turn_lock` 串行和在线状态，没有会话 owner/租约/接管 | `web/state.py` 的 runtime、`web/lease.py`、`_handlers_chat.py`、`_handlers_sessions.py`、`remote_api.py`、`routes.py` | `SessionRuntime` 增加 `lease_owner`/`lease_until`/`lease_claim`/`lease_renew`/`lease_release`；`/api/session/lease` 支持 status/acquire/renew/release/takeover；chat/stream/browser 写自动认领，审批/问答/取消只认持有设备或本机；心跳续租、120 秒自动释放；`tests/test_session_lease.py` 3 项覆盖 TTL、接管、双设备 409 冲突与本地绕过 | M |
| RC-05 | P1 | **✅ 2026-11-22 已完成**。原差距：只有 device 只读/运行与 local 全控，没有限时管理档 | `remote.py`、`remote_api.py`、`device_admin.py`、`routes.py`、设置/插件处理器 | `device_admin` scope 一次性 30 分钟：本机 `/api/remote/admin` 授予/收回；非 shell 设置与插件启停可用，`shell`/PTY/remote link 明确拒绝，`approval`/`sandbox`/`hooks`/`mcp` 字段级 403；到期自动降权、同一配对不可再申请；授权/到期/收回与每次写操作均审计；`tests/test_device_admin.py` 3 项通过 | S–M |

### B. Agent 质量（AQ）

| ID | 优先级 | 差距 / 现状 | 接回哪里 | 完成标准 | 成本 / 条件 |
| --- | --- | --- | --- | --- | --- |
| AQ-01 | P0 | `KnowledgeBase.search()` 实际只有 BM25；模块头说复用向量、类注释说刻意不用，文档自相矛盾；跨语言/同义改写召回弱 | `knowledge.py`、`config.py`、`memory/store.py` 的 embedding 函数 | BM25 + 向量混合召回（向量可选、BM25 永远兜底）；向量存知识库现有索引的 sidecar，不新建数据库/服务；英文 query 能命中中文规范，同义 query 能命中；补回归测试，统一 docstring | M |
| AQ-02 | P1 | **✅ 2026-11-22 已完成**。原差距：压缩已有 opencode 5 段结构化摘要与 prior-summary 合并，但仍可能丢旧工具输出/文件路径；压缩后的历史不可检索回填，摘要字段也没有硬校验 | `core/compact.py`、`core/context.py`、`core/loop.py` | 摘要字段硬校验（Objective/Details/Work State/Next Move/Files 缺段可检测）；工具输出分层摘要；压缩历史可检索回填；40+ 回合长任务不丢关键路径与未完成项；token 预算不超 | M |
| AQ-03 | P1 | **✅ 2026-11-22 已完成**。原差距：小模型已有 XML 解析和 1B schema 裁剪，但错误工具选择/长参数仍无强校验与重试 | `providers/llama_cpp.py`、`tools/validation.py`、`tools/registry.py`、`core/tool_runner.py` | JSON schema 校验、未知工具降级、失败只重试一次、按任务裁剪 schema；20 条工具选择回归集；非法参数不执行；1B 算术题不再调 `run_tests`；不出现重试死循环。新增 `tests/test_tool_validation.py` 61 项，工具链 10 文件定向回归 158 项通过 | M |
| AQ-04 | P2 | 只有 task 并行子代理和 workflow DAG，没有 Qoder Experts/Team Canvas 级 Lead+专家团队 | `core/workflow.py`、`core/subagent_runner.py`、声明式 `agents` | 专家角色复用声明式 agent，Lead 负责拆解/派发/验收；复用现有 workflow 引擎，不写第二套编排器；执行图显示 handoff；带模型/工具白名单与成本上限 | L；需产品决策是否产品化 |

### C. 分发、安全与可观测性（DS）

| ID | 优先级 | 差距 / 现状 | 接回哪里 | 完成标准 | 成本 / 条件 |
| --- | --- | --- | --- | --- | --- |
| DS-01 | P1 | Windows 产物未签名，SmartScreen 拦截；更新只校验 SHA256 | `.github/workflows/release.yml` Windows job、`install.ps1`、更新器 | Authenticode 签 exe/zip 内二进制并加时间戳；`signtool verify /pa` 通过；更新器拒绝坏签名；发布说明写清证书指纹 | S 代码；需代码签名证书 |
| DS-02 | P1 | macOS 只有 `arm64 tar.gz`，无 `.app/.dmg`、无 codesign/公证 | `release.yml` macOS job、`spark.spec` | 产出 `.app` + `.dmg`；codesign + notarytool + staple；`spctl --assess` 通过；Gatekeeper 无警告；SHA256 与签名都校验 | M；需 Apple Developer 账号 |
| DS-03 | P2 | Windows 只有 Job Object 进程树 + 内存上限，无文件系统/网络隔离 | `sandbox.py` Windows 后端 | AppContainer 或受限令牌 + 文件 ACL 边界；断网时真断网；越界写/读测试被拒；做不到就在 payload 明确标注隔离等级，不冒充 | L–XL |
| DS-04 | P2 | **✅ 2026-11-23 交付**（可选依赖边界）。turn/工具/审批/工作流 trace + metric + 显式审计 log；`SPARK_OTEL_BRIDGE_LOGGING=1` 桥接 stdlib logging；`SPARK_OTEL_SAMPLE_RATIO` 采样；审计 attributes 对 api_key/password/secret/authorization/cookie 统一脱敏；安装 `opentelemetry-exporter-otlp-proto-http` 时 `http(s)://host:4318` 自动走 HTTP `/v1/traces|metrics|logs`，否则回退 gRPC。**边界**：HTTP 是可选依赖，不强行打进默认安装；采样是 trace 级，不是按属性动态采样 | `otel.py`、审计模块 | 观测闭环；剩余是 collector 侧策略，不是客户端能力 | M |

### D. 生态与交互（EX）

| ID | 优先级 | 差距 / 现状 | 接回哪里 | 完成标准 | 成本 / 条件 |
| --- | --- | --- | --- | --- | --- |
| EX-01 | P2 | **🟡 2026-11-23 部分交付**。原差距：没有公共/个人市场与签名分发。现已支持 manifest + 代码包摘要签名、篡改包拒绝安装、trust/revoke、`require_signature`；公共市场索引、上架审核、撤销列表治理仍未做 | 现有 `mcp` 注册表、插件安装器、设置→插件 | 已满足：签名/篡改拒装/本地信任；未满足：索引列出/上架审核/撤销治理/公共市场 | L；需治理规则 |
| EX-02 | P2 | **🟡 2026-11-23 部分交付**。`/editor [path]` / `/vim [path]` 挂起 TUI 调外部编辑器；Web composer 已加可选 Vim 键位（Esc→Normal，i/a/I/A→Insert，h/j/k/l、w/b、0/$、x、dd、o，`VIM · N/I` 状态，localStorage 记忆）。未做宏、寄存器、可视模式与外部编辑器结果回填 composer | `tui/`、`web/src/components/Composer.tsx` | 已满足：外部编辑器可达、基础 Vim 键位；未满足：完整 Vim 状态机、回填输入框 | S–M |

| EX-03 | P2 | **✅ 2026-11-22 已完成**。原差距：词典和组件接入已铺开，但缺漏词门禁；矩阵里“仅少量组件接入”已过时 | `web/src/i18n.ts`、`web/scripts/check-i18n.mjs`、组件、构建脚本 | zh/en key 对齐、en 词典无中文、`t()` key 必存在、JSX/字符串硬编码中文失败，全部进 `npm run build`；补齐 provider/MCP/本地模型英文展示；`tests/test_ui_i18n.py` 断言英文主界面与模型设置无中文 | S |
| EX-04 | P2 | **✅ 2026-11-23 已完成**。原差距：Claude Code 兼容只到声明式 agents | `skills` 加载器、`slash` 命令发现 | 只读导入 `.claude/skills` / `.claude/commands` / `~/.claude/{skills,commands}`；同名 `.spark` 原生优先；`tests/test_skills.py` / `test_profiles_and_commands.py` 通过 | S–M |

| EX-05 | P2 | **✅ 2026-11-23 交付**（边界明确）。空闲队列现有：本地日任务配额 `queue_daily_limit`、本地日估算成本配额 `queue_daily_cost_usd`（`agent_queue.cost_usd` 含失败重试成本）、单任务墙钟超时回 `queued`、1–4 个 worker 并行（`queue_workers`，重启生效）；队列会话标 `origin=queue`，不把彼此当用户回合阻塞。**边界**：成本按公开价目估算，不是账单；多 worker 仅限进程内，不是分布式队列；worker 数修改需重启 | `task_queue.py`、`store.py`、设置/成本台账 | 本地节流闭环；剩余是分布式/多机调度，不在当前进程内队列范围 | M |

### E. 文档真实性与门禁（DOC）

| ID | 优先级 | 差距 / 现状 | 接回哪里 | 完成标准 | 成本 |
| --- | --- | --- | --- | --- | --- |
| DOC-01 | P0 | **✅ 2026-11-22 已完成**。原差距：竞品矩阵多次漂移，远程/PR/浏览器已修，TUI/i18n/插件/遥测仍是旧判断；没有“文档说 ✅ 但代码没有”的检查 | `docs/competitive-comparison.md`、本文件、CI | 86 个 ✅ 行全部内联代码/测试落点；原 36 个无落点行补齐；逐行核对发现“归档”只有删除、无归档/恢复，已降 ⚠️；矩阵复测日期更新为 2026-11-22 第八轮；核对记录 `docs/reviews/2026-11-22-matrix-audit.md` | S |
| DOC-02 | P0 | 模块 docstring 与实现矛盾（知识库最典型：头说向量、类说不用） | `knowledge.py` 等源码 docstring | 注释只保留一种事实；与 AQ-01 一起改；review 时把 docstring 漂移当缺陷 | S |

### 执行顺序（不并行硬塞）

1. **先对账**：DOC-01 ✅ / DOC-02 ✅，矩阵与 docstring 的假话已清掉；这一步不改行为，避免后面继续按错地图施工。
2. **先做代码闭环**：AQ-01 → AQ-02 → AQ-03；RC-01 → RC-04 ✅ → RC-05 ✅。全部有单测/定向回归，不依赖外部账号。
3. **再做手机形态**：RC-03 ✅ 已接在 RC-01/RC-04 之后；独立手机壳复用现有会话、租约、SSE 与审批链路，不另起第二套移动端。
4. **再做分发**：DS-01 / DS-02 需要证书与 Apple 账号，代码可以先写好，签名放在有凭据的环境执行；DS-03 单独立项，不混进发布。
5. **最后做生态**：EX-01 / EX-02 / EX-03 / EX-04 / EX-05、AQ-04、DS-04；市场、专家团队、配额都涉及产品决策，不先斩后奏。

### 明确不新开

- 不新建仓库、不新起独立服务、不新增第二套数据库；知识库向量复用现有 embedding 路径和现有索引文件。
- 不另起移动端框架；手机远控是 `web/` 内的一套响应式布局，不是新 App。
- 不另起编排引擎；专家团队复用 `workflow` + `task` + 声明式 agents。
- 不另起遥测后端；全链路观测扩展 `otel.py`。
- 不为每个平台写独立 UI；设置页、审批卡、审计列表共用现有组件。
- 不把“明确不做”的云端沙箱/账号体系/移动 App、LSP 绕过写路径重新塞进计划；编辑器桥接按 Phase 3 门禁推进，不再以“没有编辑器本体”为由排除。

## 待做（按"能不能直接动手"排序）

### 可以直接做（无产品决策、成本可控）

| 能力 | 状态 | 完成标准 |
| --- | --- | --- |
| 见上方 RC / AQ / DS / EX / DOC 编号项 | 📋 | 每项完成标准已写在对应表；按执行顺序推进，不另开清单 |

### 需要产品决策（会改变交互形态）

| 能力 | 状态 | 为什么不能自己决定 |
| --- | --- | --- |
| RC-02 官方托管 relay / 账号入口 | ⏸ | 需要公网域名、TLS、托管成本与隐私边界；自托管 Web 代理可先做 |
| AQ-04 专家团队编排 | ⏸ | 会改变交互形态与成本模型，需决定是否产品化 |
| EX-01 插件/技能市场签名与上架 | ⏸ | 涉及签名信任链、审核与撤销治理 |
| EX-05 免费额度 + 超时回队列 | ✅ | 已用本地日任务数/估算成本配额 + 墙钟超时 + 1–4 worker 落地；无需定价，按公开价目估算并明确不是账单 |

### 成本明显更高

| 能力 | 状态 | 估算 |
| --- | --- | --- |
| Repo Wiki（生成式代码文档） | ✅ | `spark wiki` CLI + 设置→代码文档：tree-sitter 结构事实（模块/符号/真实行号）、按内容哈希增量、`verify_citation` 拒绝悬空 `file:line`、`--summarize` 可选且失败降级为纯结构；产物落 `.spark/wiki/`（已 gitignore），19 条测试 |
| 三平台 OS 级沙箱 | 🟡 v1 已落地 | Linux bwrap（既有）+ Windows Job Object（进程树 kill-on-close + 8GiB 内存上限，**不做**文件系统/网络隔离——能力边界如实标注在工具结果 payload.sandbox）+ macOS sandbox-exec（写面收敛 workdir/临时目录 + 可断网）；真做 Windows AppContainer 级文件隔离仍列高成本 |

## 已知依赖告警（上游约束，非本仓库可修）

| 告警 | 数量 | 为什么留着 | 解除条件 |
| --- | --- | --- | --- |
| `chromadb` 服务端注入 / RBAC（上游无修复，已登记豁免） | 4 类 advisory | 1.5.9 是最新版且无修复版本；spark 只用嵌入式 `PersistentClient`/`EphemeralClient`，从不启动 Chroma HTTP 服务，服务端攻击面不存在 | 上游发布修复版本 |

> `filelock` 3.32.7 的 13 条告警已在 2026-11-20 关闭：`litellm` 1.110.1 将约束放宽到
> `filelock>=3.16.1,<5`，根锁升级到 `filelock 4.2.12`，`pyproject.toml` 下限同步到
> `filelock>=4.2.1`。`tests/test_workflow.py` 与全量测试验证 FileLock API 兼容。

> 另：`data-agent/` 与 `api/` 下清单文件在 v0.9.6 前已从仓库删除，GitHub 不会自动回收；
> 对应 50 条 open 告警已按 "not_used" 批量关闭并注明陈旧清单原因；
> `.github/dependabot.yml` 同时用 `exclude-paths` 排除 `api/**` 与 `data-agent/**`，
> 阻止 Dependabot 继续生成指向已删除清单的失败任务。
>
> Dependabot 自动合并现在会先等全部 PR 检查通过再 squash；major 版本（例如
> `agent-client-protocol` 0.12 → 1.0）只留人工审核，且仅主仓 `badhope/spark`
> 执行，避免主分支先合并、CI 后失败以及镜像仓自行分叉。

## 明确不做

| 能力 | 理由 |
| --- | --- |
| 云端沙箱 / 托管账号体系 / 第二套原生移动 App | 当前不做托管账号与多租户；移动端走 Web 响应式与远程接管，不新起第二套原生 App。浏览器五引擎、Profile 与 CDP 已落地；扩展桥与按域名授权继续推进 |
| 完整 Tab 补全 | Phase 3 先做 NES 与内联编辑；完整 Tab 补全要过“真实模型接受率 ≥25%、p95 ≤1.5s”门禁后再决定，不先斩后奏 |
| 允许 LSP 绕过 Spark 写路径 | 语言服务器只能返回 WorkspaceEdit；实际写入必须走 sandbox/policy/approval |

## 维护约定

1. 关闭一项时：改本表状态 + 在 `competitive-comparison.md` 同步矩阵格 + CHANGELOG 记一行。
2. 新增能力前先在这张表里加一行，写清"完成标准"；没有标准的不算排期。
3. 标 ⏸ 的项只有在用户明确拍板后才允许开工。
