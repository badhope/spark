# 竞品功能对比：spark vs Qoder / ZCode / opencode / Codex CLI

> 调研日期：2026-10-14；**spark 侧复测 2026-11-23（第九轮）：功能真实性整改后重校——归档/恢复已落地（v6 + API + Web 分组）、插件签名升级为 manifest+代码包摘要并拒绝篡改包、Claude skills/commands 兼容、TUI `/editor`//`/vim` 可达、OTel 增补 approval/workflow/step span；Goal 已加同流自动续跑（默认 2 回合、失败熔断）、队列已加每日完成配额；Goal 跨重启改为“可见 + 一键续跑”、队列成本额度/多 worker 已落地、Composer 基础 Vim 键位已落地；完整 Tab 补全仍按门禁后置**（逐项 grep 代码并实跑验证，
> 见 `docs/reviews/2026-10-22-full-audit.md` 与 `docs/reviews/2026-11-22-matrix-audit.md`）。竞品侧仍为公开文档/仓库声明。
> 目的：给出 spark 与主流 AI 编程 agent 的功能差距矩阵，服务于后续路线规划。
> 标注：✅ 已有 ｜ ⚠️ 部分/不完整 ｜ ❌ 缺失。
>
> **维护约定**：修改对应代码后必须同步本表并更新上面的复测日期；
> 本表与实现不一致按缺陷处理（审查发现 F-007 即此类）。

## 0. 对象与形态总览

| 产品 | 开发商 | 形态 | 开源 | 模型策略 |
|---|---|---|---|---|
| **spark** | badhope（本仓库） | TUI + Web + Desktop(EXE) + CLI + `mcp-serve` + `a2a-serve` + ACP | MIT | BYO（OpenAI 兼容 / Ollama / mock） |
| **Qoder** | 阿里 | VS Code 系 IDE + 独立桌面 App + CLI + Web + Mobile + JetBrains 插件 + Cloud Agents | 闭源 | 订阅 Credits；内置 Qwen/DeepSeek/GLM/Kimi 等 + BYOK |
| **ZCode** | 智谱 Z.ai | Desktop(Electron) + Web + CLI/TUI + 手机遥控 + 飞书/微信 Bot | Apache-2.0 | GLM 系为主 + 第三方 provider |
| **opencode** | sst | TUI + Web + 桌面 App + ACP(编辑器协议) | 开源 | models.dev 目录，任意 provider |
| **Codex CLI** | OpenAI | CLI/TUI + IDE 扩展 + 桌面 App + Cloud | 开源（客户端） | ChatGPT 订阅 / API key / Bedrock / Ollama |

## 1. 安装与分发

| 功能点 | spark | Qoder | ZCode | opencode | codex |
|---|---|---|---|---|---|
| 一键安装脚本 | ✅ `install.sh` / `install.ps1`（SHA256 校验、无 sudo） | 安装器 | dmg/exe/deb 包 | `curl … \| bash` | `curl … \| sh`（Win: irm \| iex） |
| 免安装绿色包 | ✅ `spark-agent-*-win64.zip` 解压即用（含 GUI 子系统 exe，双击无黑框） | 安装器 | 安装包 | — | — |
| 包管理器 | ✅ scoop bucket（`bucket/spark.json`，autoupdate + Release digest 哈希） | 安装器 | ❌ | brew/npm/choco/scoop/pacman… | npm/brew/Releases |
| 自动更新 | ✅ `spark update`（SHA256 + 原子替换 + 旧版备份）+ doctor 提示，可 `mode=off` | 内置 | 内置 | ✅ auto/notify 三态 | ✅ check_for_update_on_startup |
| 卸载命令 | ✅ `spark uninstall`（清单 + 确认 + 家目录守卫） | 安装器 | — | ✅ `opencode uninstall` | — |

**spark 缺口**：Windows 产物未 Authenticode 签名（SmartScreen 会拦），macOS 只有 `arm64 tar.gz`、无 `.app/.dmg`/公证；自动更新已有 SHA256 与旧版备份。建议 P1（EXE 用户体感关键），签名需要证书与 Apple 账号。

## 2. 认证 / 模型配置

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 登录方式 | 配置文件 + 环境变量 + `spark init` 向导 | Qoder/ZCode：账号登录（OAuth）；codex：Sign in with ChatGPT |
| 多 provider 预设 | ✅ 12 个（5 家原生：Anthropic/OpenAI/Gemini/Azure/Bedrock；5 家国产；Ollama/Mock）+ 自定义（`providers/presets.py` / `tests/test_provider_presets.py`） | Qoder 预设 9+；opencode models.dev 全目录 |
| 内置本地模型目录 | ✅ 5 个 GGUF（MiniCPM5-1B/2B、Qwen3.5-2B/4B、BotPsy-0.8B），双镜像直链、精确体积/SHA256、一键下载与启用（`local_models.py` / `tests/test_local_models.py`） | Qoder 需自配 Ollama；opencode 走 models.dev 目录但不代下权重 |
| Mock 与真实模式分离 | ✅ 仅显式 Mock 为展示模式；缺 Key 返回 `UnavailableProvider` 并阻止回合；`/api/demo` 一键切换且保存/恢复真实档案 | 多数同类工具把 mock 当默认离线兜底，易与真实回复混淆 |
| Prompt caching | ✅ Anthropic system + tools 稳定前缀 `cache_control`；命中/节省 token 可见 | Anthropic 原生缓存；opencode provider 能力 |
| 模型路由 | ✅ `spark --tier capability\|cost\|offline`、`spark route`、Web `/api/routing/preview`；按任务类型/预算决策，可查降级链 | 多为手动选模型 |
| 多模型档案切换 | ✅ `[[model_profiles]]` + Web 面板 | codex `--profile`；opencode agent 级 model |
| 热键循环最近模型 | ✅ `Ctrl+Alt+M` 循环模型档案 | opencode `F2/Shift+F2`；codex `/model` 弹窗 |
| 推理档（effort/variant） | ✅ effort 低/中/高（Web + 配置）（`config.py` + `providers/openai_compat.py` / `tests/test_provider_presets.py`） | opencode `ctrl+t` variant 循环；codex `/model` 带 reasoning 档 |
| 连通性自检 | ✅ `spark test` + `doctor --probe` | 都有对应（`/status`、doctor） |
| 小模型分工（标题/压缩） | ✅ `provider.small_model`：压缩摘要 + 会话标题/关键词 | opencode `small_model`；codex `review_model` |

## 3. 交互模式

| 功能点 | spark | 竞品代表 |
|---|---|---|
| TUI 快捷指令 | 🟡 `/test`、`/plan`、`/build`、`/side`、`/goal`、`/editor [path]`、`/vim [path]`；外部编辑器可挂起 TUI 并恢复；仍无 composer 内 Vim 键位与 60+ slash 全量 | codex 60+ slash；opencode 全量 slash（`/init /undo /share /compact /export /models /sessions /themes …`） |
| 自定义斜杠命令 | ✅ `.spark/commands/*.md` + 输入框 `/` 补全 | opencode markdown 命令（`$ARGUMENTS`/`!shell` 模板）；codex 经 hooks/skills |
| 内联 shell（`!cmd`） | ✅ Web 输入框 `!cmd`，结果本地卡片、不进模型上下文 | opencode ✅ |
| `/editor` 外部编辑器写 prompt | 🟡 TUI `/editor`/`/vim` 可挂起外部编辑器；Web composer 不负责回填 | opencode / codex ✅ |
| 消息回溯编辑 | ✅ 消息级删除 / 编辑重发 / 重新生成（`messages/delete`、`messages/truncate`） | codex **Esc Esc** 回溯改 prompt 分支重跑；opencode timeline 回填 |
| Vim 模式 | 🟡 Composer 可选基础键位：Esc/i/a、h/j/k/l、w/b、0/$、x、dd、o，`VIM · N/I` 状态；无宏/寄存器/可视模式 | codex `/vim` |
| 非交互 exec | ✅ `spark exec`（stdout 纯文本）+ `--json`（NDJSON 事件流，末行 `{"type":"final"}`） | codex `exec --json/--output-schema/--ephemeral`；opencode `run --format` |
| 代码审查命令 | ✅ `spark review [--commit/--base/--json]`，强制只读+仅沙箱，高危 findings 退出码 2 | codex `codex review --uncommitted/--base/--commit` |
| 计划模式 | ✅ `plan-only` / `approve-plan` 两档 | codex `/plan`；opencode plan agent；Qoder Plan-driven；ZCode `/plan` |
| Goal 长程模式 | 🟡 `/goal`：目标契约注入 + `goal_update` 证据门槛；`goal_auto_turns` 同流自动续跑（默认 2、失败熔断）；刷新/重启后回显活跃目标并可一键“继续推进”；无确认的后台自动续跑仍缺 | ZCode `/goal`（证据门槛 + 迭代验证）；Qoder `/goal` |

## 4. 会话管理

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 新建/恢复/切换 | ✅ TUI ctrl+r、Web 侧栏、`spark resume` | 全都有 |
| Fork | ✅ `POST /api/sessions/fork` + 顶栏按钮 + 侧栏入口 | codex `/fork`；opencode `--fork`；ZCode Edit History 分支 |
| 分享链接 | ✅ 单文件 HTML 离线分享（零 JS、零外链、消息内容转义）；当前不做绑定账号的云托管分享，可后续接自托管/对象存储（`web/_handlers_sessions.py` + `HeaderToolsMenu.tsx`） | opencode `/share` → `opncd.ai/s/<id>` |
| 导出/导入 | ✅ 导出 md/JSON/HTML；✅ 导入 spark 导出 JSON（`POST /api/sessions/import`，仅恢复 user/assistant 文本） | opencode `/export`（md/JSON）；codex `/export`；codex `/import` 从 Claude Code 迁移 |
| 归档 | ✅ `sessions.archived_at`（v6）+ `/api/sessions/archive` + Web“已归档”分组/恢复；归档当前会话自动切换；测试 `test_session_archive.py` | codex `/archive`；ZCode 自动归档候选（窗口可配） |
| 重命名 | ✅ 自动标题 + Web 手动改名（`Sidebar.tsx` / `web/_handlers_sessions.py`） | codex `/rename`；opencode ctrl+r |
| 会话内 token 用量 | ✅ ContextMeter（`ContextMeter.tsx`） | codex `/status`；ZCode Usage Stats 面板 |
| 消息级撤销/回滚 | ✅ 时间轴 undo，可选连文件一并还原到该消息前的检查点（`web/_handlers_sessions.py` / `tests/test_msg_ops.py`） | ZCode Undo/Reapply（全有或全无）；Qoder `/rewind`（会话/文件分别恢复）；codex `thread/revert` |
| 侧聊/旁支 | ✅ `/side <问题>`：辅助模型一次性作答，不污染主对话历史 | codex `/side`(`/btw`)；ZCode Side Conversation；Qoder Side task |

## 5. 上下文 / 记忆 / 知识

| 功能点 | spark | 竞品代表 |
|---|---|---|
| AGENTS.md | ✅ 项目级（可配文件名）（`core/context.py` / `tests/test_context.py`） | opencode 多级向上 + `~/.config` 全局；codex 逐级收集；ZCode 两源（全局+workspace） |
| 全局规则文件 | ✅ `~/.agents/AGENTS.md`（+CLAUDE.md，与项目/子目录三级叠加） | opencode `~/.config/opencode/AGENTS.md`；ZCode `~/.zcode/AGENTS.md` |
| Claude Code 兼容（CLAUDE.md/skills） | ✅ `.claude/agents/*.md` + `.claude/skills` + `.claude/commands` + 用户级 `~/.claude/*`；同名 `.spark` 优先 | opencode `CLAUDE.md` 回退 + `.claude/skills`；ZCode 一键导入 + 预装 CC marketplace |
| `@` 文件引用补全 | ✅ 输入框 `@` 列出工作区条目，方向键 + Tab/Enter | opencode `@` 模糊；codex `@` file-search；ZCode `@` 文件/文件夹 |
| 外部目录/仓库引用 | ✅ `--add-dir` / `agent.extra_dirs` | opencode `references`（别名+description）；codex `--add-dir` |
| 图片输入 | ✅ Web 粘贴/拖拽（`Composer.tsx` / `tests/test_web_attachments_feedback.py`） | opencode 自动缩放/5MB 上限；codex 剪贴板截图 + `view_image` |
| 自动压缩 | ✅ 85% 阈值 + 保留 8 条；摘要走 opencode 5 段结构化格式并支持 prior-summary 合并（`core/compact.py` / `tests/test_compaction.py`） | opencode 三态（auto/prune/reserved）；codex 双阈值 + pre/post_compact hooks |
| 压缩可编程干预 | ✅ `.spark/prompts/compact.md`（项目）→ `~/.spark/prompts/compact.md`（全局）覆盖，摘要+合并两路径 | opencode 插件可换压缩提示词；codex hooks |
| 仓库地图/索引 | ✅ tree-sitter 符号地图（6000 字符）（`repo_map.py` / `tests/test_p0_repo_map.py`） | opencode LSP 诊断；Qoder 云端向量索引（10 万文件）；ZCode Repo Wiki |
| Repo Wiki（生成式知识文档） | ✅ `spark wiki` / 设置→代码文档：tree-sitter 结构事实 + 真实行号 + 增量缓存 + 引用校验（无 Mermaid，仅静态符号表；不做云端生成） | Qoder（含 Citation 开关，50 credits/仓）；ZCode（产物本地、带 file:line 溯源、Mermaid 图） |
| 项目知识库（导入文档 RAG） | ✅ `knowledge.py`：md/txt/rst/json/yaml/csv/toml 导入；BM25 始终可用，开启 `memory.embedding_enabled` 后叠加向量混合召回；`vectors.json` sidecar、旧索引回填、embedding 失败降级；`tests/test_knowledge.py` 36 项 | 无完全同类；Qoder 云端代码库索引、ZCode Repo Wiki 都是代码索引，不是用户导入文档 RAG |
| 长期记忆 | ✅ 独立记忆库（embedding + auto_extract + Web 管理）（`memory/store.py` / `tests/test_memory.py`） | codex `/memories`（后台管线 + 合并）；ZCode Project Memory（默认关）；Qoder Auto-Memory |
| 记忆可视化管理 UI | ✅ Web 记忆面板（`MemorySection.tsx`） | Qoder `/memory manage`；ZCode 无管理页（手改文件） |

## 6. 工具集

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 读写/edit | ✅ 三种（整写/单处 SEARCH-REPLACE/aider 多块 + tree-sitter 语法门禁）（`tools/fs.py` + `edit/` / `tests/test_p1_02_searchreplace.py`） | opencode edit/write/apply_patch；codex apply_patch（专用 crate + diff 审批） |
| shell | ✅ 沙箱策略 + 超时/输出上限（`tools/shell.py` / `tests/test_sandbox_policy.py`） | codex unified exec（可后台）；opencode bash |
| 后台任务 | ✅ bg_start/output/kill/list（跨重启持久化）（`tools/bg.py` / `tests/test_git_bg_persist.py`） | codex `/ps` `/stop`；Qoder `/tasks` 面板 |
| grep/glob | ✅（`tools/search.py` / `tests/test_tools.py`） | opencode ripgrep 尊重 .gitignore + `.ignore`；codex file-search |
| **LSP 集成** | ✅ 9 个代码智能工具：definition/references/hover/symbols/diagnostics/rename/code action/affected_tests；LSP 优先，无服务器显式降级；编辑走沙箱+审批 | opencode 内建 40+ 语言 LSP（definition/references/hover/…） |
| 代码格式化 | ✅ `agent.format_on_write`（ruff/prettier，默认关） | opencode 30+ formatter（写后自动，默认关） |
| web 搜索/抓取 | ✅ Bing 搜索 + HTML→text（`tools/web.py` / `tests/test_search_web.py`） | codex 原生 web_search + browser_use/computer_use 配置 |
| 内置浏览器（agent 可驱动） | ✅ 21 个 `browser_*`：Bing 默认首页/搜索、ref 快照、点击/填表/按键/悬停/下拉/滚动/历史/多标签/等待/截图/PDF/下载/视口/JS/文件上传/控制台/网络/对话框；iframe frame_index；侧栏实时画面 | ZCode built-in browser；Qoder Browser Agent |
| Notebook | ✅ read + edit（cell 级）（`tools/notebook.py` / `tests/test_task_notebook.py`） | — |
| Git 工具 | ✅ status/diff/log/branch/add/commit/worktree + run_tests（测试回灌闭环） | 多数靠 bash；codex `/diff` + `codex apply`；`--worktree` |
| 任务即分支（绿测提交门控） | ✅ 已注册 `task_begin/task_step_commit/task_finish` | 无同类（独特能力） |
| 用户交互（提问工具） | ✅ `ask_user` 工具 + 问答卡（无交互通道时优雅报错） | opencode `question`；codex `request_user_input` |
| 子代理执行图（实时） | ✅ `主代理 → task → 子代理 → 工具 → 产出`，由 SSE 事件累积 | opencode 无（纯文本）；codex 无；ZCode 无 |
| 会话导出 | ✅ Markdown / JSON + 命令面板下载（`web/_handlers_sessions.py` + `CommandPalette.tsx` / `tests/test_content_handlers.py`） | opencode `/export`；codex `/export` |
| 环境自愈 | ✅ 诊断 + 一键修复（目录/配置/运行时），`doctor --fix` | opencode/codex 仅报错 |
| 数据备份 | ✅ `VACUUM INTO` 在线快照 + `doctor --backup` | 均无 |
| 快捷键帮助面板 | ✅ `?` 打开 | codex `?`；opencode 无 |
| 技能（Skills） | ✅ `.spark/skills/**` + `load_skill` | ZCode 全生态（导入 CC/Codex 等 + 插件分发）；opencode 6 目录 search；codex bundled + 动态选择器 |
| 插件系统 | ✅ MCP 注册表搜索 + `spark mcp` CLI 市场（live+离线策划索引+中国可用性） | opencode npm 插件（可注册覆盖内建工具）；codex plugin marketplace；ZCode Public/Personal marketplace |

## 7. 权限 / 沙箱 / 安全

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 审批档位 | ✅ 6 档（含 plan-only/approve-plan）（`config.py` + `policy.py` / `tests/test_policy.py`） | codex `-a on-request/never` + sandbox 双轴；opencode allow/ask/deny + `--auto`；Qoder 3 档；ZCode 4 档（Shift+Tab 循环） |
| 审批弹窗粒度 | ✅ 允许/始终允许/拒绝（相同签名复用）（`policy.py:call_signature` + `core/tool_runner.py` / `tests/test_policy.py`） | codex：命令前缀 / 文件集合两种粒度；ZCode：Allow/Always/Reject/Always Reject + 5 分钟倒计时（权限问题不受限） |
| 规则引擎 | ✅ `[[permissions]]` 通配 + 最后匹配生效 | opencode 13 键 pattern→动作；codex execpolicy `.rules` + `--ignore-rules` |
| OS 级沙箱 | ✅ 三平台 v1：bwrap（Linux）+ Job Object（Windows：kill-on-close+内存上限）+ sandbox-exec（macOS：写面收敛+可断网）；payload 如实标注隔离类型（`tools/os_sandbox.py` + `sandbox.py` / `tests/test_os_sandbox.py`） | codex 三平台（Seatbelt/Landlock/Windows 沙箱）+ 默认断网；Qoder/ZCode 为策略级 |
| 网络限制开关 | ✅ `agent.network_access`（bwrap 真隔离，其它平台如实降级） | codex `sandbox_workspace_write.network_access` |
| AI 审 AI（自动审查） | ✅ `agent.auto_review`（独立调用审单次动作，block 即拒） | codex Guardian（自动审查 + `/approve` 放行 + OTLP 审计） |
| 输出脱敏 | ✅ 三层（密钥/主目录/堆栈）（`sanitize.py` / `tests/test_sanitize.py`） | — |
| 第三方输出防注入 | ✅ untrusted 围栏（`tools/untrusted.py` / `tests/test_p0_untrusted_sandbox.py`） | 无同等显式机制（ZCode 浏览器"页面文本不作指令"） |
| Web 控制面保护 | ✅ loopback 默认 + X-Spark-Token（非本机启动时打印）（`web/server.py` / `tests/test_web_security_hardening.py`） | opencode `OPENCODE_SERVER_PASSWORD`；ZCode `--token` |

## 8. 多 Agent / 协作

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 子代理 | ✅ `task`（最多 4 并行）+ 声明式 `.claude/agents` | codex spawn_agent（并发/深度限制/消息板）；opencode general/explore/scout；ZCode subagents（frontmatter 超集）+ workflows |
| 子代理独立模型 | ✅ `.claude/agents/*.md` 的 `model:` 字段，按声明切 provider | ZCode subagent `model`/`thoughtLevel`；codex `default_subagent_model` |
| 专业团队编排 | ❌ | Qoder Experts Mode（Lead + 6 专家 + Team Canvas）；ZCode Dynamic Workflows（JS 编排） |
| 主 agent 切换 | ✅ `/plan` `/build` 双档（plan-only 只读 ↔ 常规审批，复用审批档案 override） | opencode Tab 切 build/plan；codex `/agents` 指挥中心 |
| 子会话导航 | ✅ 子代理轨迹卡片带声明名 + 任务描述 + 实时进度（`subtask_start` 事件携带 `agent`/`prompt`，UI 逐卡区分） | opencode 子会话树（leader+方向键）；codex `/subagents` |

## 9. 回滚 / 检查点

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 快照粒度 | ✅ 每轮（涉文件/命令后）tar.gz + baseline 增量（`core/checkpoints.py` / `tests/test_checkpoints.py`） | opencode 基于 git snapshot；codex rollout + git apply |
| 回滚范围 | ✅ 文件 + 对话截断（`core/checkpoints.py` / `tests/test_checkpoints.py`） | opencode `/undo` `/redo`（可多次）；ZCode 全有或全无 + Reapply；Qoder 会话/文件分别 |
| 非 git 目录回滚 | ✅（`core/checkpoints.py` / `tests/test_checkpoints.py`） | — |

## 10. 自动化 / 通知 / 更新 / 其它

| 功能点 | spark | 竞品代表 |
|---|---|---|
| 定时任务 | ✅ `automations.py`（cron + skipped 语义 + 停机补跑） | ZCode Automations（模板/聊天创建 + 四种频率 + skipped 语义）；Qoder `/crontab` |
| 空闲算力队列 | 🟡 | `task_queue.py` 单 worker：无会话忙碌时执行、持久暂停/恢复、失败重试 3 次；**每日完成上限 `queue_daily_limit` + 单任务墙钟上限 `queue_task_timeout_sec` 已落地**（超时回 queued 重试，0=不限）；仍缺按成本额度和多 worker 并发 |
| 桌面通知/声音 | ✅ Web Notification（回合结束/等待审批，页面不可见时才发；设置可开）（`notify.ts`） | opencode attention 声音包；codex notifications（osc9/bel）+ `/warnings` |
| 主题 | ✅ Web light/dark/system × 5 套配色（正交）；TUI 固定（`theme.ts` + `index.css`） | opencode 10+ 主题 + 自定义 JSON；codex `/theme` + `$CODEX_HOME/themes` |
| 快捷键重绑 | ✅（`shortcuts.ts` + 设置→快捷键） | `shortcuts.ts` + 设置→快捷键：7 个动作本地重绑、恢复默认 |
| 状态栏定制 | ✅ `agent.status_items` 有序项（模型/接口/密钥/审批/访问/目录/会话） | codex `tui.status_line` 有序项；ZCode 命令中心 |
| 通知/远程 | ✅（`web/remote.py` + `web/web_push.py` + `MobileShell.tsx` / `tests/test_remote_access.py`） | 扫码配对（一次性链接/7 天设备令牌）、LAN/公网入口、Web Push 审批、在线心跳与仅本机审计、390×844 手机首页/固定导航/两步审批/语音输入；缺官方托管 relay |
| IDE 集成 | ✅ VS Code 原生面板（选区/流式/内联 diff/诊断/跳转）+ Web 工作台内联编辑（预览/哈希守卫/应用）+ Neovim `<leader>si` 选中区桥 + ACP 一键接入 Zed / JetBrains 2025.3+ / Neovim CodeCompanion；JetBrains 原生 inline UI 仍走 `spark inline` External Tool（`ide.py` + `vscode-spark/` + `tests/test_ide_install.py`） | opencode 自动装 VS Code 扩展 + ACP（Zed/JetBrains/neovim）；codex `/ide`；Qoder 本尊是 IDE |
| Git PR 工作流 | ✅（`pr.py` + `tools/pr_tools.py` / `tests/test_pr.py`） | `pr_context` / `pr_view` / `pr_diff` / `pr_checks` / `pr_review`，`spark pr publish --confirm` 才写评论；GitHub / GitCode / Gitee |
| 遥测 | 🟡 `otel.py` 已有 turn/tool/approval/workflow/step span；metric（token/延迟）与显式审计 log（审批/工具失败/工作流结束）已走 OTLP gRPC；仍缺 Python logging 全量桥接、HTTP OTLP 与采样/脱敏策略 | codex 全量 OTLP（log/trace/metrics + 审计导出） |
| 多语言 UI | ✅ 1007 个 zh/en key 完全对齐；`check-i18n` 进 `npm run build`，en 词典无中文、957 个 `t()` 调用可解析、硬编码中文失败；浏览器实测英文主界面与模型设置 0 中文 | ZCode/Qoder 多语言完整 |

## 11. spark 独有 / 领先项

1. **四档沙箱 × 六档审批正交组合**——`plan-only`/`approve-plan` 作为独立审批档位是差异化设计。
2. **tar.gz 检查点**：不依赖 git、含 manifest 校验、非 git 目录可回滚。
3. **任务即分支**（`task_begin/step_commit/finish`）：绿测门控自动提交、失败自动废弃——竞品无同类一等功能。
4. **MCP 注册表搜索**：内置中国可用性评估 + Glama 评分 + 离线数据集兜底；stdio 与 sse/streamable_http 三种传输都能一键添加（早期只认 stdio，见 2026-10-22 审查 D-01）。
5. **`spark mcp-serve`**：把自己反向暴露为 MCP server。
6. **`spark a2a-serve`**：A2A（Linux Foundation agent-to-agent 标准）Server——Agent Card 发现 + message/send + tasks/get，编排器可直接委派 Spark；MCP/ACP/A2A 三协议全就位，开源编码 agent 中少见。
7. **A2A/AG-UI/ANP 协议调研决策记录**：AG-UI 明确不做（自有 SSE 已覆盖同语义），理由与边界见 docs/A2A.md 与 docs/reviews/2026-11-04-ecosystem-audit.md。
6. **`/test` 失败回灌闭环**：traceback 自动作为 user 消息让 agent 自修。
7. **完全本地数据面**：无任何云依赖可全功能运行（含离线 mock）。
8. **tree-sitter 语法门禁**：写文件前 AST 检查（语法错误拒写 + 符号删除警告），竞品未见同款内建。

## 12. 差距清单（建议优先级）

**P0（低成本高价值，已有基础设施只差接线/UI）**
- ~~Fork 的 UI/API 暴露~~（已关闭：`/api/sessions/fork` + 前端入口）
- ~~消息级 Undo + 按轮"文件+消息"一并还原~~（已关闭：`/api/messages/truncate` 支持 `restore_files`）
- ~~`/export` 会话导出~~（已关闭：`/api/sessions/export` md/json + HTML 分享）
- ~~`@` 文件引用补全~~（已关闭：Composer + `/api/fs/tree`）
- effort 全链路（本轮已修 ✅）
- system_prompt 全链路（本轮已修 ✅）

**P1（中成本，对齐业界预期）**
- ~~自定义斜杠命令~~（已完成，见 §3）
- ~~桌面通知~~（第七轮完成，见 §10）
- ~~`exec --json`~~（第七轮完成，见 §3）
- ~~LSP 代码智能~~（已完成：`src/spark/lsp/` 9 个工具；LSP 优先，无服务器显式降级；编辑走沙箱/审批）
- ~~会话分享~~（第七轮完成：单文件 HTML，见 §4）
- ~~快捷键可配置~~（已完成）+ ~~主题扩展~~（已完成：5 套配色，与明暗正交）

**第七轮（2026-10-23）已关闭**：一键安装、自动更新、`exec --json`、`spark review`、
`!cmd` 内联 shell、桌面通知、HTML 分享、会话导入、全局规则、small_model、（另已存在）
`~/.agents` 全局层。

**P2（高成本 / 需要产品决策）**
- ~~定时 Automations~~（已实现：automations.py + 设置→定时任务）
- ~~JetBrains / Neovim 的一键接入~~（已完成：JetBrains 25.3+ 原生 ACP；Neovim CodeCompanion 模块；不写编辑器专属插件）
- 插件市场公共治理（已做 manifest+代码包摘要签名、篡改拒装、trust/revoke；仍缺公共索引、上架审核、撤销列表）
- 多 agent 专业编排（Experts/Workflows 级）

**当前不做**：托管账号体系/多租户云沙箱/第二套原生移动 App（移动端走 Web 响应式与远程接管）；完整 Tab 补全先过 Phase 3 接受率/延迟门禁，当前只交付内联编辑与 NES 入口。浏览器能力层、多引擎与远程浏览器是核心路线，不因旧“本地优先”结论而放弃。

## 13. 来源

- opencode：opencode.ai/docs（config/tui/keybinds/permissions/plugins/skills/mcp/acp/…），github.com/sst/opencode
- Codex CLI：github.com/openai/codex（README、cli/src、config 源码、release notes 158–162）
- Qoder：docs.qoder.com（qoder/*、cli/*、extensions/*、account/*、release-notes/*）
- ZCode：zcode.z.ai/en/docs/*、zcode.z.ai/en/changelog、github.com/zai-org/ZCode（README、apps/zcode-cli、docs/ 设计文档）
- spark：本仓库源码与 docs/
