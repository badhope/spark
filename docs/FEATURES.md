# 功能总览（以实现为准）

> 本文替代以往"愿景式"功能列表：每条都能在代码里找到落点，并在
> `tests/` 中有对应断言。数字会变，能力不会。

## 一、能做什么

| 能力 | 实现落点 | 怎么用 |
| --- | --- | --- |
| 与模型对话（流式） | `providers/openai_compat.py` | Web / TUI / CLI 任一入口输入即可 |
| 原生/国产模型一键切换 | `providers/presets.py` + 设置→模型 | 12 个预设（5 家原生 + 5 家国产 + Ollama/Mock）；`spark providers` 可列 |
| 中英双语与构建门禁 | `web/src/i18n.ts` + `web/scripts/check-i18n.mjs` + `npm run build` | 1007 个 key 中英一一对齐；en 词典禁止中文；957 个 `t()` 调用必须命中 key；JSX/字符串硬编码中文直接失败；provider/MCP/本地模型预设含英文展示字段；`tests/test_ui_i18n.py` 浏览器验证英文主界面/模型设置 0 中文 |
| 内置本地模型目录 | `local_models.py` + 设置→模型→本地模型 | 5 个可一键下载的 GGUF：MiniCPM5-1B（轻量）、MiniCPM5-2B（平衡）、Qwen3.5-2B、Qwen3.5-4B（4B 稳定档）、BotPsy-0.8B（英文实验）；均校验精确体积、GGUF 文件头与 SHA256 sidecar，支持断点续传、ModelScope/HF 镜像与一键启用 |
| Mock 展示模式与真实模式分离 | `providers/factory.py` + `/api/demo` + 设置→模型 | 只有显式选择 Mock 才是展示模式，回复由脚本生成并明确标注；真实模式缺 Key 返回 `UnavailableProvider` 并阻止回合，不生成假回答。设置页「进入/退出 Mock 展示模式」一键切换，进入前保存真实配置、退出原样恢复；缺 Key、模型未下载、连接失败按真实错误呈现 |
| 成本与缓存面板 | `store.token_usage` + `GET /api/cost` + 设置→模型 | 每会话 token、cache hit、按模型成本、预算上限；未知价格显示 — |
| 让模型读写文件 | `tools/fs.py`、`edit/` | 模型自行调用；写入受沙箱与审批约束 |
| 让模型执行命令 | `tools/shell.py` | 命令先过 `sandbox.check_shell`，再按审批档放行 |
| 让模型跑测试并自我修正 | `tools/gitops.run_tests` + `core/loop.py` | 模型调用 `run_tests`，失败输出自动回灌 |
| 小模型工具调用护栏 | `providers/llama_cpp.py` + `tools/validation.py` + `tools/registry.py` + `core/tool_runner.py` | 12 个核心工具白名单再按任务裁成读/写/执行/联网子集；参数按 JSON Schema 子集校验（必填/类型/enum/范围/未知字段）；未知工具、禁用工具、白名单外工具在审批前降级；参数超 2 MiB 拒绝；非法调用只回传一次修正机会，同一工具第二次非法标 `retry_exhausted`，不执行 handler |
| 让模型操作 Git | `tools/gitops.py` | status/diff/log/branch/add/commit；push/pull 明确不提供 |
| 任务即分支（自动提交） | `git_memory.py` | `task_begin` → 绿测后 `task_step_commit` → `task_finish` |
| 联网查资料 | `tools/web.py` | `web_search` / `web_fetch`，含 SSRF 防护 |
| 浏览器操作 | `tools/browser.py` | 24 个 `browser_*`：Bing 默认首页/搜索引擎；ref 快照、点击/输入/按键/悬停/下拉/滚动/历史/标签页/等待/截图/PDF/下载/视口/JS/文件上传/控制台/网络/对话框、trace 起停、Chromium 性能指标；支持 iframe frame_index；Playwright 后台线程持有页面；只允许公网 http(s) 或工作目录内 `file://`；页面文本进 untrusted 围栏；截图落 `.spark/browser/`。**执行面已支持 Chromium / Chrome / Edge / Firefox / WebKit 五引擎**，可持久化 Profile 复用登录态，也可通过 CDP 接管已运行的 Chromium/Chrome/Edge；按域名授权、敏感站点确认、trace 与浏览器体检已落地；扩展桥为官方 Playwright MCP `--extension` 预设 |
| PR 审查上下文 | `pr.py` + `tools/pr_tools.py` | `pr_context` / `pr_view` / `pr_diff` / `pr_checks` / `pr_review`；GitHub / GitCode / Gitee REST，origin 自动解析，平台 token 可选；CLI `spark pr view/diff/checks/review`，`spark pr publish --confirm` 才写评论；review brief 给风险、测试缺口与 CI 清单；PR 文本进 untrusted 围栏 |
| 接第三方能力 | `tools/mcp_bridge.py` | stdio / sse / streamable_http 三种传输；也可从内置 registry 一键添加，工具以 `mcp__<server>__<tool>` 出现 |
| 多 Agent 工作流 | `workflow.py` + `spark workflow` | YAML roles/steps/DAG 校验、拓扑顺序、`workflow graph`；`WorkspaceLocks` 跨进程路径锁；`run_workflow` 有界并发、重试、失败阻断与取消；`AgentWorkflowRunner` + `spark workflow run` 已接 AgentLoop 子代理；状态 JSON、暂停续跑、单节点重跑、Web 执行图读取最近运行；子代理 usage/cost 回传、`--max-cost-usd` 成本上限；`--isolate-worktree` 整次运行隔离到任务分支 |
| 子代理并行 | `core/loop.py:spawn_subtasks_parallel` | 模型调用 `task`，最多 4 路并行 |
| 声明式专家代理 | `agents.py` + `.claude/agents/*.md` | 放一个 md 文件即多一个可委派的专家 |
| 项目技能 | `skills.py` + `.spark/skills/*.md` | 放文件即生效，模型按需 `load_skill`；设置→技能可增删改 |
| 自定义斜杠命令 | `slash.py` + `.spark/commands/*.md` | `/name 参数` 展开为提示词；设置→提示词可管理 |
| 工作区插件 | `plugins.py` + `spark plugins` | `spark-plugin.json` 校验（permissions/tools/commands/hooks）、本地/git 安装、卸载/备份、Web 本机 enable/disable（远程只读）；`[plugins].enabled` + 对应权限双重 opt-in 后，hook/tool 走独立子进程（超时/无 stdin/环境变量清洗），插件工具以 `plugin__<plugin>__<tool>` 命名空间注册进 ToolRegistry 并走正常审批；插件命令、市场索引、版本检查/升级、`official` bundle 一次装全（成员仍可单独 disable）已接，仓库内 `review-checklist` / `ship-checklist` 为无 hook/tool 权限的官方命令示例 |
| 长期记忆 | `memory/` | 自动抽取 + 关键词/可选向量检索 + 网页面板管理 |
| 项目知识库 | `knowledge.py` | 导入文档（md/txt/rst/json/yaml/csv/toml）；BM25 始终可用，开启 `memory.embedding_enabled` 后叠加向量召回并按轮注入只读参考；向量存 `knowledge/<project>/vectors.json` sidecar，embedding 不可用时自动退回 BM25 |
| 会话全文检索 | `store.search()` + FTS5 | 侧栏搜索命中聊天正文，结果带片段 |
| 长上下文压缩 | `core/compact.py` + `core/context_window.py` + `store.search_session_history()` | opencode 5 段结构化摘要（Objective/Important Details/Work State/Next Move/Relevant Files）缺段自动补 `(not provided)` 并告警；工具输出按 2k/8k 分三档留头尾；压缩后按当前问题在 `compact_from` 之前做会话内有界召回，注入只读片段，失败静默降级 |
| 回滚 | `core/checkpoints.py` | 每轮写操作后自动快照，可整体回滚 |
| 崩溃恢复 | `core/turn_store.py` + `core/runtime.py` | 审批中断的 turn 落盘，重启后仍可列出并恢复 |
| 可观测 | `otel.py` | `SPARK_OTEL_ENDPOINT=...` 后 trace=turn / span=tool + approval/workflow，metric 与显式审计 log 一并走 OTLP |
| 逐轮复盘 | `core/replay.py` | 每 turn 一行 JSON，CLI 与 Web 都可查 |
| 定时任务 | `automations.py` + 设置→定时任务 | cron 触发一段提示词；进程不在线时下次启动补跑一次；忙则跳过不排队 |
| 额外目录 | `sandbox.py:extra_roots` | `--add-dir DIR` 或配置 `agent.extra_dirs`；只放行文件读写，不放宽 shell |
| 写后格式化 | `tools/formatting.py` | `agent.format_on_write = true`；只调用项目已有的 ruff / prettier，失败不阻断写入 |
| 符号定位 | `tools/symbols.py` | `find_symbol` 工具：tree-sitter 找定义 + 文本扫引用，免审批只读；无 LSP 时的降级路径 |
| 代码智能（LSP） | `src/spark/lsp/` | 9 个工具：definition/references/hover/symbols/diagnostics/rename/code action/affected_tests；LSP 优先，无服务器显式降级；rename 默认 dry-run，落盘走沙箱+审批 |
| VS Code 深度集成 | `vscode-spark/` | 编辑器内聊天面板、选区上下文、SSE 流式、内联 diff 审批、诊断回显、`file:line` 跳转、任务状态；VSIX 与 package.json/pyproject 三向版本锁 |
| IDE 一键接入 | `ide.py` + `spark ide install` | Zed（`agent.custom_servers`）、JetBrains 2025.3+（原生 `~/.jetbrains/acp.json`）、Neovim（CodeCompanion 独立 Lua 模块）；先备份、未知键保留、坏配置拒绝覆盖 |
| 内联编辑桥 | `inline_edit.py` + `spark inline` | VS Code 原生 inline provider；Web 工作台预览 + `base_sha256` 哈希守卫 + 显式应用；Neovim `<leader>si` 选中区；JetBrains/其他编辑器走 `spark inline --json` External Tool；默认不写盘 |
| 自动审查 | `core/review.py` | `agent.auto_review`：写/执行前用独立模型审一次，block 即拒绝；默认关 |
| 断网执行 | `tools/shell.py` | `agent.network_access = false`：bwrap 下跑空网络命名空间；不可用时如实告警 |
| 评测回归门 | `evals/baseline.json` + `scripts/check_eval_regression.py` | mock eval 12 场景基线；掉 2 点或任一原通过场景转失败即阻断；push/PR + 每日夜间跑 |
| UI 可访问性/布局 | `tests/test_ui_a11y.py` | 6 屏关键项 + 真 axe-core WCAG A/AA 亮/暗双主题 0 serious/critical + 8 屏 × 桌面/手机视口无横向溢出；像素基线仍待做 |
| 性能预算门 | `scripts/check_perf_budget.py` + `perf/budgets.json` | 本地校验 CLI 启动 p50/p95、首屏 JS gzip、`/api/status/sessions/history/fs/tree` p95；`.github/workflows/ci.yml` 独立 perf job 已接，SSE/内存预算待接；`spark --version` 走轻量入口，当前 p50 约 150ms |
| 空闲任务队列 | `task_queue.py` + `agent_queue` 表 + `spark queue` | SQLite 单表、单 worker；只在无会话忙碌时跑；持久暂停/恢复；进程崩溃把 running 恢复为 queued；失败最多重试 3 次；Web 设置页可入队/暂停/取消，CLI 可脚本化 |
| 远程控制 | `web/remote_access.py` + `web/remote.py` + `web/web_push.py` + `/api/remote/*` | 设置→系统→远程控制一键生成链接/SVG 二维码；局域网按需开启临时监听，公网用 `spark web --public-url https://你的域名`；手机打开 `#pair=<6 位码>` 自动配对，5 分钟一次性码换 7 天设备令牌；二维码只含一次性 URL，不含长期密钥；支持刷新轮换、在线状态（60 秒心跳/120 秒窗口）、设备列表、单台吊销、停止全部远程访问。HTTPS/localhost 页面可开启 Web Push，锁屏接收审批摘要（`web/public/sw.js`，VAPID 与订阅存 `$SPARK_HOME`，0600）；远程写操作与配对/吊销/停止写入 `remote-audit.jsonl`，`GET /api/remote/audit` 仅本机可读。设备令牌默认只读/运行：可发消息、审批、取消、切会话、看文件与执行图；改设置、插件、PTY、配对管理默认限本机。单会话租约：第一台写设备自动认领，其他设备写 `409 lease_held`，审批/问答/取消只认持有设备或本机；心跳续租，120 秒无心跳释放，支持显式 takeover。限时 `device_admin`：本机可授予一次 30 分钟管理档，开放非 shell 设置与插件启停；`shell`/PTY/remote link 仍拒绝，字段级拦截 approval/sandbox/hooks/mcp，到期自动降权且需重新配对才能再申请，授予/到期/收回/每次写操作均审计。详见 `docs/REMOTE.md` |
| 供应链 SBOM | `sbom.py` + `spark sbom` | 生成 CycloneDX 1.5 JSON（完整 Python 环境 + Spark 组件）；发布流水线生成 `spark-sbom.json` 并用 GitHub build provenance 做来源证明 |
| 发布资产校验 | `scripts/check_release_assets.py` | 发布前校验 3 平台包 + SBOM 是否齐全、SHA256 是否匹配、SBOM 是否 CycloneDX 1.5；缺文件/多文件/坏哈希都阻断 |
| 发布通道 | `updater.py` + `spark update --channel` | stable 默认走 latest；canary 显式选预发布 tag；安装仍强制 SHA256SUMS 校验与安全解压 |
| 出站 relay | `relay.py` + `spark relay serve/connect/call` | 本机主动连出中继，远端按 channel/key 调 `/api/*`；普通请求缓冲回传，`{"stream":true}` 走 `/v1/channels/{id}/stream` + HTTP chunked，SSE/审批/工具事件逐块到达；`--channel/--key` 时 relay 根路径代理 `/`、`/assets/*`、`/sw.js`、`/api/*`，配对链接/二维码直接用 relay base URL；单频道自托管，relay key 不下发浏览器 |
| 手机端布局（RC-03） | `web/src/components/MobileShell.tsx` + `useIsMobile.ts` + `App.tsx` | <768px 切独立手机壳：审批优先首页固定显示待审批/当前会话/新任务；底部固定首页/对话/新建/设置四栏并处理 `100dvh` 与安全区；审批卡片“查看并审批”进入详情，拒绝/允许两步内完成；对话页复用现有 SSE、会话租约与 `Composer`；`Composer` 增加浏览器 `SpeechRecognition` 语音输入，不支持/拒绝/无结果时提示并保留文本输入；`tests/test_ui_a11y.py` 390×844 无横向溢出，`test_ui_browser.py` 交互回归通过 |
| 手机/PWA 入口 | `web/public/manifest.webmanifest` + `web/index.html` + `web/public/sw.js` | 可安装的 standalone manifest、192/512 图标、apple-touch-icon；普通访问也注册 `sw.js`（scope `/`，只处理 push/notificationclick，不做页面缓存），Chrome `Page.getInstallabilityErrors` 为空 |
| 右侧工作台 | `web/src/components/RightPanel.tsx` + `GET /api/git/changes` | 单一 Workbench 壳，5 个 tab：文件（树+预览）、变更（Git 只读状态/diff）、终端（内嵌 PTY）、网页（阅读模式）、追踪（实时执行图/最近 workflow）；旧的文件/浏览器/终端/执行图独立弹层已从 App 入口移除，工具菜单统一打开工作台 |
| 配色主题 | `web/src/theme.ts` + `index.css` | 5 套强调色（Ember/Ocean/Forest/Violet/Mono），与明暗正交 |
| 代码文档（Repo Wiki） | `wiki.py` + `spark wiki` + 设置→代码文档 | 用 tree-sitter 从源码结构生成本地文档：每个模块一张符号表，行号可核对；按内容哈希增量，可重复可 diff；`--summarize` 才调模型 |
| 会议录音转写 | `control/audio.py` + `/api/v1/office/audio/transcribe` | faster-whisper 本地 CPU 推理，音频不出机器；返回带时间戳文本，可直接生成纪要；上限 200MB / 3 小时 |
| 文档品牌化 | `control/branding.py` + `SPARK_CONTROL_BRAND_*` | 企业名称、主色、logo 写入 PDF/DOCX/PPTX/XLSX 版式；内容、来源行号、审批流程不受影响 |

## 二、形态与入口

```
spark                     # 终端 TUI
spark web --workdir DIR   # 浏览器 UI（默认只监听 127.0.0.1）
spark desktop             # 原生窗口（pywebview）
spark enterprise          # 企业控制面 /api/v1 与 /office 工作台
spark exec "任务"          # 一次性执行，适合脚本/CI
spark pr view 123         # PR 元数据/文件/diff/评论/CI（只读）
spark pr review 123       # 风险/测试缺口/CI 清单 review brief
spark pr publish 123 --body-file review.md --confirm  # 显式发布评论
spark acp                         # 作为 ACP agent 供 Zed / JetBrains / Neovim 拉起
spark ide install zed             # Zed ACP 一键接入
spark ide install jetbrains       # JetBrains 25.3+ 原生 ACP 一键接入
spark ide install neovim          # CodeCompanion ACP 模块（不改 init.lua）
spark mcp-serve           # 把内置工具暴露给别的 MCP 客户端
spark a2a-serve           # A2A 协议服务（Agent Card + message/send + 流式）
spark --version           # 版本号（与 pyproject / PyPI 同源）
```

各形态实测状态（2026-11-15，均由真实客户端驱动，不是测试替身）：

| 形态 | 实测方式 | 结果 |
| --- | --- | --- |
| TUI | PTY 内启动、键入任务、回车提交、读屏 | 输入 → 工具调用 → 结果渲染 → 回复，全链路可用 |
| Web | Playwright（Chromium/Chrome/Edge/Firefox/WebKit，支持 Profile 与 CDP） | 设置 5 页（模型/外观/工作区/扩展/系统，页内 16 个细分页签）、30+ 控件、Workbench 5 tab、会话/工具/文件树/终端/命令面板 |
| 桌面 | pywebview + WebView2 | 起服务、加载完整前端、窗口创建 |
| CLI | `spark exec --json` | NDJSON 事件流，`final` 收尾 |
| 企业控制面 | 真实 PostgreSQL + Keycloak | OIDC 鉴权、Casbin 授权、会议/报告/审计、全格式导出 |
| MCP | 官方 Python SDK 客户端 | 37 个工具，`list_dir` 真实执行 |
| ACP | 官方 SDK `spawn_stdio_connection` | `initialize` 返回 agentInfo，`session/new` 返回会话与六档审批 |
| VS Code | Extension Host + 真实 `spark web` | 面板 SSE、选区上下文、`vscode.diff` 审批、Problems 诊断、file:line 跳转；0.15.4 VSIX 与源码同版 |
| JetBrains | 原生 ACP 配置写入 + JSON 校验 | 25.3+ `~/.jetbrains/acp.json` 合并/备份/幂等；真实 IDE 冒烟待有客户端环境 |
| Neovim | 生成 Lua 模块 + `luac -p` | CodeCompanion ACP 适配器、独立文件、不覆盖 `init.lua`；真实 Neovim 冒烟待环境 |
| A2A | 真实 HTTP | Agent Card、`message/send`、`message/stream` 四帧协议 |


## 三、安全模型（三层，逐层收窄）

1. **审批档**：六档，从 `plan-only`（只出计划）到 `full-auto`。
   默认 `suggest`：写文件与执行命令都要确认。
2. **沙箱档**：`sandbox-only` / `workspace`（默认）/ `full-access` / `unrestricted`。
   路径越界、受保护路径（系统目录、`~/.spark`、Spark 自身源码、`.git/hooks`）一律拒绝。
3. **权限规则**：`[[permission_rules]]` 按"工具 + 资源"通配匹配，
   支持三层作用域（user / project / local），**deny 跨层优先**，
   克隆下来的仓库只能收紧权限、无法给自己放行。

补充约束：

- 审批授权绑定到 `(工具, 参数)` 的签名，批准一次某命令不会连带批准另一条。
- shell、worktree、task 类工具**永不允许"始终允许"**。
- 项目级 `.spark.toml` 会被剥离 MCP / hooks / 模型档案 / 权限规则 / 凭据。
- 只有 `$SPARK_HOME/.env` 能提供配置，仓库里自带 `.env` 无效。
- 所有对外输出（SSE、错误文案）经过脱敏：密钥、家目录、堆栈。

> 边界诚实说明：shell 检查是 token 级启发式，能挡住常见的越界与危险命令，
> 但挡不住刻意构造的绕过（例如用解释器动态拼接路径）。
> 对凭据敏感的场景请用 `suggest` / `auto-edit`，不要开 `full-auto`。

## 四、Windows 与 Linux 的差异处理

差异集中收敛在 `src/spark/platform_compat.py`，其余模块只调用它：

| 关注点 | Windows | Linux / macOS |
| --- | --- | --- |
| 进程树终止 | psutil 递归 terminate/kill；缺失时 `taskkill /F /T` | psutil；缺失时 `start_new_session` + `killpg` |
| 子进程黑框 | `CREATE_NO_WINDOW` | 无此概念（标志为 0） |
| 默认 shell | `%COMSPEC%`（cmd.exe） | `$SHELL`，回退 `/bin/sh` |
| Python 可执行名 | `python` | `python3`（自动回退） |
| 内置终端 | `pywinpty`（ConPTY），真实 PTY | `ptyprocess` |
| 保留文件名 | 拒绝 `CON/PRN/AUX/NUL/COM1-9/LPT1-9` | 同样拒绝（保证可移植） |
| 破坏性命令 | 额外匹配 PowerShell 别名（`ri`/`del`/`rd` → `remove-item`） | POSIX 名称 |

**安装与克隆**：仓库中不存在任何 Windows 保留文件名（`aux.py` 已改名
`auxiliary.py`），Windows 上可直接 `git clone`。

## 五、界面

- **三态主题**：亮 / 暗 / 跟随系统，切换即时生效并记住。
- **响应式**：≥1024px 侧栏常驻且可拖拽调宽（双击复位，方向键微调）；
  窄屏侧栏变抽屉，设置页 tab 变横向可滚动。
- **按需加载**：终端（xterm）、执行图（xyflow）、设置、回放等重面板首次打开才加载；
  首屏 JS 从 680 KB 降到 141 KB。
- **键盘**：`Ctrl+K` 命令面板、`Ctrl+N` 新会话、`Ctrl+\` 切换侧栏、
  `Ctrl+` 加反引号开终端、`Esc` 停止生成、`?` 帮助。六个全局键均可在
  设置→快捷键里改键并做冲突检测。
- **审批卡片**：展示工具名、摘要、diff，并可展开查看完整参数。
- **多级导航**：会话侧栏 → 会话 → 消息内工具块折叠 → 设置分 4 组 16 页签
  （核心：模型/Agent/权限/展示 · 工作区：项目/记忆/知识库/代码文档/定时任务 ·
  扩展：MCP/技能/提示词/插件 · 系统：快捷键/调试/诊断），默认只呈现一层；
  分组标题可折叠，同一时刻只展开一组。
- **会话检索**：侧栏搜索走 FTS5（trigram，中文子串友好），命中消息正文时
  在标题下显示片段。

## 六、如何验证它真的能用

```bash
# 离线：全部单测（约 2.5 分钟）
python -m pytest tests -q

# 静态门禁
ruff check src tests
mypy src/spark --ignore-missing-imports
bandit -r src/spark -ll -ii -c bandit.yaml
lint-imports

# 真实模型全链路（需要你自己的 key，不会写进任何文件）
export SPARK_TEST_BASE_URL=https://your-endpoint/v1
export SPARK_TEST_API_KEY=sk-...
export SPARK_TEST_MODEL=your-model
python scripts/live_smoke.py
```

`live_smoke.py` 分 6 个阶段各自独立超时：非流式补全、流式增量、
工具写文件、工具执行命令、审批拒绝生效、上下文计数。
任一阶段卡住不会拖垮整轮，最终给出 `x/6` 的结论。
