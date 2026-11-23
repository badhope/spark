# Spark 审查记录与方向讨论

> **定位说明（2026-11-23）**：本文写作于“本地优先”旧口径时期，该口径已废止。现行定位见 [PRODUCT_POSITIONING](../PRODUCT_POSITIONING.md)；本文仅作历史审计与事实记录，不再作为路线依据。


> 日期：2026-10-17 · 状态：**未动代码，仅记录** · 基线：v0.9.2 @ a0f5769
> 本文档两部分：①源码审查结论（修复清单）②方向讨论（定位/生态/竞品/扩展/工程化）

---

## 第一部分：源码审查记录（2026-10-17 全量走查）

规模：Python 147 文件 / 29,594 行（src），前端 TS/TSX 6,634 行，测试 68 文件 / 11,046 行。

### 1.1 缺陷清单

| 级别 | 位置 | 问题 | 修复建议 |
|---|---|---|---|
| P0 | tools/shell.py | bwrap 用 `--unshare-all`（含网络隔离），docstring 却说"不隔离网络"——装了 bwrap 的 Linux 在 workspace 模式下 pip/npm 等网络命令全部失败 | 加 `--share-net`；沙箱参数表化 |
| P0 | store.py | `delete_messages_after` / `truncate_messages` 删 FTS 用 `rowid > X` 无 session 过滤——单库多会话时把其他会话的搜索索引一起删掉 | 删除条件加 session 维度或改用 rowid IN 子查询 |
| P0 | memory/service.py | 向量记忆默认 embedding_model=text-embedding-3-small，但请求走 provider.base_url（默认 DeepSeek chat 端点，无 /embeddings）→ 默认配置下向量检索永远失败降级关键词 | embedding 独立配置 base_url + 默认关闭并提示 |
| P1 | core/tokens.py + loop | token 全部启发式估算（CJK=1 其他 4字符/token），用量表 source=estimated，无真实成本 | 流式响应回填 usage；无法回填的才标 estimated |
| P1 | core/compact.py | Compactor 绕过 Provider 抽象直调 litellm.acompletion——mock provider 测试会意外走真路径，压缩行为与主循环不一致 | 压缩也走 provider 接口，或声明依赖并注入 |
| P1 | providers/openai_compat.py | 多工具调用缺 id 时兜底都叫 `call_0`，同轮多调用撞 id；参数 JSON 解析失败静默变 `{}` | 序号兜底 + 解析错误回显给模型 |
| P1 | core/loop.py | 压缩只在 turn 开始触发；超长 turn 中途不压缩，只靠 trim_recent 硬裁剪 | 每轮工具循环后检查阈值 |
| P1 | tools/registry.py | `execute()` 每次调用重建整张 dispatch 表（全部 lambda 闭包） | 构建一次，`__init__` 缓存 |
| P2 | core/compact.py | `trim_recent` 文档说 token-aware，实现按 chars | 统一口径 |
| P2 | core/compact.py | merge_summaries 失败回退把 prior 塞进 "## Objective"，格式失真 | 独立小节 |
| P2 | repo_map.py | 仅 py/ts/tsx/js 四种语法；无 git 热度排序；`_iter_files` 达 max_files 即 break，地图按遍历顺序偏置 | 按语言增量补 grammar；git log 频次加权 |
| P2 | config.py | `history_budget_chars` 按字符、`max_context_tokens` 按 token，两套单位并存 | 统一 token 口径 |
| P2 | sandbox.py | shell 防泄漏为黑名单子串匹配（自述 best-effort）：`python -c "import os;os.environ"` 等可绕过读父进程环境变量 | 子进程 env 白名单注入（至少剥离 *KEY*/TOKEN/SECRET） |
| P2 | core/context.py | 默认 `max_context_tokens=32768` 明显偏小（竞品 128k-200k） | 默认提到 131072 |
| P2 | prompts/system.md | 仅 34 行，远薄于同类（无代码风格/提交规范/工具优先级细则），是行为质量天花板 | 扩写到 150+ 行并按模式分节 |

### 1.2 做得好的（保持）

- **安全意识**：项目级 `.spark.toml` 清洗彻底；`$SPARK_HOME/.env` 只从用户主目录加载（防恶意仓库）；密钥文件 chmod 600 + Windows ACL；审批签名绑定 (tool,args) 防粘贴攻击；NEVER_PERSIST_ALLOW。
- **edit/core.py**：aider SEARCH/REPLACE 三级容错（exact→尾空白→缩进漂移）、多块原子应用、唯一性强约束。
- **checkpoints.py**：manifest 路径逃逸校验、tar filter=data、restore 防 unarchived 误删。
- **policy.py**：resource 空值防全局放行、glob 深度防爆。
- **测试文化**：68 个测试文件、对抗性安全测试、perf bounds、CI evals。
- **MCP 边界**：SSRF 白名单、命名空间隔离、注册表国内可达性探测 + 离线兜底。

### 1.3 架构师视角缺失清单

- A1 无 LSP/编译诊断闭环（模型看不到类型错误，盲改）
- A2 无 IDE 集成路径（无 ACP/插件协议）
- A3 无真实 usage/成本计量（全部估算）
- A4 turn 中途不压缩、无上下文优先级分层
- A5 子代理只回传 summary，轨迹不可回放
- A6 无消息级回溯/分支重跑 UI（存储层已支持，UI 未接）
- A7 分发链缺失：无 PyPI/安装脚本/自动更新
- A8 无 review/PR 审查模式
- A9 Web 多会话并发无隔离（单 loop）
- A10 in-flight turn 崩溃不可恢复（checkpointer 只管工作区+索引）
- A11 LangGraph 参考实现与生产 while 循环**两套真相源并存**，迟早漂移

---

## 第二部分：方向讨论（回应五点输入）

### 2.1 定位重申（同意，并具体化）

本地优先、代码和密钥不出本机、模型自带（BYO）、高度可定制——这四条不变。
推论：**所有新增能力优先选"协议/标准"而非"自建服务"**，因为自建服务会慢慢把产品变成云厂商，跟定位打架。判据：一个新功能如果需要常驻服务端，先想协议方案（本地 daemon / 本地 MCP）能不能替代。

### 2.2 协议与框架生态地图（按时间，标注置信度与采用决策）

置信度：✅=仓库竞品文档已逐条核查（2026-10-14）/ 📚=训练知识 / ❓=待核实。
"采用"栏：现=已有 / 缺=缺失待补 / 半=部分实现 / 看=观望。

| 时间 | 技术/事件 | 对 Spark 的意义 | 采用 |
|---|---|---|---|
| 2022-10 | LangChain 开源 📚 | 编排框架先驱，但重；Spark 自研 loop 是对的 | 不用 |
| 2023-03 | AutoGPT/BabyAGI 爆火 📚 | 证明"循环+工具"范式，也证明无护栏必翻车 | 教训 |
| 2023-06 | OpenAI Function Calling 📚 | 工具调用成为标准接口 | ✅现 |
| 2023-11 | OpenAI Assistants API / GPTs 📚 | 托管 agent 的第一波，锁定风险样本 | 不用 |
| 2024-09 | OpenAI o1 推理模型 📚 | reasoning_content 需要 provider 透传 | ✅现 |
| 2024-10 | Claude Computer Use 📚 | 截图→操作，桌面自动化范式 | 看 |
| **2024-11** | **Anthropic 发布 MCP** 📚 | 工具/上下文接入的事实标准 | ✅现（加强） |
| 2025-01 | DeepSeek R1 开源推理模型 📚 | 国产低成本强模型，Spark 默认档 | ✅现 |
| 2025-02 | Claude Code 预览版 📚 | 本品类标杆，交互/记忆/子代理全参考 | 对标 |
| 2025-03 | MCP Streamable HTTP 替代 SSE 传输 📚 | 传输层升级 | ⚠️缺（现仅 stdio） |
| 2025-03 | OpenAI Responses API + Agents SDK 📚 | 官方 agent 框架；托管倾向 | 不用 |
| 2025-03 | OpenAI 宣布支持 MCP 📚 | MCP 跨厂商地位确立 | — |
| 2025-04 | Codex CLI 开源 📚 | apply_patch 格式成事实标准、OS 级沙箱（Linux Landlock/seccomp、macOS Seatbelt） | ✅现(patch格式) / ⚠️缺(沙箱) |
| **2025-04** | **Google A2A 协议**（后捐 Linux 基金会）📚 | 跨厂商 agent 互操作 | 看（等生态成熟） |
| 2025-04 | Qwen3 📚 | 国产能力档 | ✅现 |
| 2025-05 | Claude 4 系列 📚 | 长程编码能力跳升 | — |
| **2025-06** | **Zed 发布 ACP（Agent Client Protocol）**📚 | 编辑器↔agent 开放协议，opencode/Gemini CLI 已接入——**接 ACP=免费进 IDE，不用自己写插件** | ⚠️缺（高优先） |
| 2025-06 | MCP 规范 2025-06-26：OAuth 2.1 授权、Elicitation 📚 | 服务端凭据安全 + 运行时询问用户 | ⚠️缺 |
| **2025-07** | **GLM-4.5 / Kimi K2 / Qwen3-Coder 集中发布** ✅ | 国产 agentic 三件套，路由矩阵已覆盖 | ✅现 |
| 2025-08 | DeepSeek V3.1 ✅ | 混合思考 | ✅现 |
| 2025-08 | Qoder（阿里）发布 ✅ | 竞品：重 IDE | 对标 |
| **2025-08** | **AGENTS.md 规范站上线、获 OpenAI 等背书** ✅ | 项目指令事实标准 | ✅现（三级加载） |
| 2025-09 | opencode 走向成熟（models.dev 目录、LSP 集成、TS 插件系统）✅ | 三个可直接抄的能力：LSP、插件、模型目录 | ⚠️缺×3 |
| 2025-09 | MCP 官方 Registry 预览 📚 | 服务发现标准化 | ✅现（自建注册表+国内可达性） |
| 2025-09 | DeepSeek V3.2-Exp ✅ | 成本档 | ✅现 |
| **2025-10** | **Anthropic Claude Skills（SKILL.md）** 📚✅ | 技能包格式开放，生态可复用 | ⚠️半（格式兼容待对齐） |
| 2025-10 | OpenAI AgentKit 📚 | 托管化路线，反例 | 不用 |
| 2026-H1 | ZCode（智谱）成型：消息级 Undo/Reapply、Usage 面板、手机遥控、飞书/微信 Bot、CC 一键导入 ✅ | 竞品：Bot 渠道与回溯体验 | 对标 |
| 2026-10 | 本仓库 v0.9.2 / 五平台矩阵 | — | — |

❓待你确认的两个名字：
- **hermes**——指哪个？（Nous Research 的 Hermes 模型？还是某个 agent 框架/产品？）确认后我补进学习清单。
- **deepseed harness**——是否指 DeepSeek 官方的 agent harness？还是 HuggingFace 系（smolagents 等）？确认后补。

#### MCP"把能力拿完"差距表（重点）

| MCP 能力 | Spark 现状 | 动作 |
|---|---|---|
| Tools 调用（stdio） | ✅ 已有 | 保持 |
| 作为 Server 暴露 | ✅ mcp-serve | 保持，补 Streamable HTTP |
| Streamable HTTP 传输（客户端+服务端） | ❌ 仅 stdio | 补——远程 MCP 市场基本全是 HTTP |
| Resources（资源读取/订阅） | ❌ | 补客户端支持 |
| Prompts（服务端提示模板） | ❌ | 补，`/mcp:` 命令入口 |
| Sampling（服务端反向请求 LLM） | ❌ | 补——agent↔server 双向的关键能力 |
| Elicitation（服务端运行时问询用户） | ❌ | 补，接现有 Approval 通道 |
| OAuth 2.1 授权（远程 server） | ❌ | 随 Streamable HTTP 一起补 |
| Roots（工作区边界声明） | ❓半 | 对齐现有 sandbox root |
| Notifications/进度 | ❓半 | 对齐事件流 |

结论：MCP 是 Spark 最该"吃干净"的协议。吃干净后，Spark 自动成为任意 MCP 客户端（Claude Code/Codex/Zed）的**本地引擎**——这本身就是获客入口。

#### LangGraph 决策（A11 消除）

二选一，不做第三种：
- **方案 A（推荐）**：v0.10 把生产 loop 迁到 LangGraph（interrupt=审批、SqliteSaver=turn 级恢复、子图=子代理），graph.py 现状就是迁移蓝图；迁移期用现有 68 个测试当行为等价安全网。
- **方案 B**：删除 graph.py，文档写明自研理由，止损漂移。

倾向 A：我们要的"turn 级崩溃恢复 + plan/verify 双节点"，自研工作量大于接入。

### 2.3 竞品学习清单（能抄的抄，能用的用）

| 来源 | 学什么 | 怎么落 |
|---|---|---|
| opencode | LSP 集成、TS 插件系统、models.dev 模型目录、@文件引用、share 会话导出、ACP 接入 | LSP/ACP/插件按 2.2 表落地；models.dev 当 provider 元数据源替代硬编码路由 |
| Codex CLI | OS 级沙箱（Landlock/seccomp/Seatbelt）、apply_patch 格式、/status 透明度、thread/revert | 沙箱后端策略化：bwrap→landlock/seccomp（Windows 无对应时明确降级）；/status 等价物进 TUI |
| ZCode | 消息级 Undo/Reapply、Usage Stats 面板、飞书/微信 Bot 遥控、CC 一键导入+技能市场 | 回溯 UI（A6）、用量面板（A3）优先；Bot 渠道作为分发面 |
| Claude Code | hooks 事件面、skills/子代理细节、memory hierarchy、压缩体验 | 已有 hooks/skills 雏形，对齐事件集合与 SKILL.md 格式 |
| pi | 极简内核哲学：核心小、扩展用普通代码、不锁框架 | 反向约束 2.5 的模块化设计，防止"策略化"变成"框架化" |

### 2.4 横向 × 纵向扩展路线

**横向（更多接入面，多借协议）**：
1. ACP 接入 → Zed 及全部 ACP 客户端（最高杠杆）
2. mcp-serve 补 Streamable HTTP → 可托管给任意 MCP 客户端当本地引擎
3. Bot 渠道：飞书/微信/Telegram（ZCode 已验证需求）
4. CI 模式：GitHub Action 里当 reviewer/fixer（A8）
5. PyPI + 一键安装脚本（A7，分发是横向前提）

**纵向（同一循环更强，多借反馈信号）**：
1. LSP 诊断闭环：改完即读编译器/类型错误，自动修复（A1）
2. plan/verify 双循环：计划批准后每步有验证节点（LangGraph 方案 A 顺带解决）
3. 真实 usage 回填 + 成本报表（A3）
4. 小模型分工：压缩/标题/事实抽取用便宜模型，主循环用强模型（默认 DeepSeek V3.2-thinking 主 + 非 thinking 辅）
5. 记忆升级：抽取→巩固→遗忘（store 已有 importance 字段，缺 TTL 与衰减）
6. evals 升级：场景库扩到 50+，加 LLM-as-judge
7. turn 级崩溃恢复（A10，LangGraph checkpointer）

### 2.5 模块化/标准化/可运维（参考前沿但不框架化）

原则（学 pi）：**内核小而稳，变化点全部策略化，策略通过注册表插拔，插件就是普通代码**。

1. **包拆分**：`spark-core`（loop/context/compact/checkpoint）、`spark-providers`、`spark-tools`、`spark-mcp`、`spark-web`、`spark-tui`——src/spark 现 40+ 文件平铺，按依赖方向分六包，core 禁止反向 import 上层。
2. **策略注册表**（统一成同一模式）：Provider、Sandbox 后端（bwrap/landlock/docker/none）、Compactor、Memory 后端、审批策略、Hook 执行器。
3. **插件 API**：普通 Python 包 + entry-points 声明，不造 DSL；扩展点带 PROTOCOL_VERSION。
4. **可观测**：OTel GenAI 语义约定接进 otel.py（trace=turn，span=工具调用，metric=token/成本/延迟），默认关、doctor 一键开。
5. **配置治理**：schema 版本号 + 迁移器；单位统一 token；doctor 覆盖 embedding/sandbox/MCP 体检。
6. **发布工程**：release-please 自动 CHANGELOG + tag；tag 触发五平台推送与 PyPI 发布；perf budgets 进 CI（已有 test_perf_bounds）。
7. **ADR**：docs/decisions/ 编号化，先补两篇——"loop 自研 vs LangGraph"、"沙箱后端矩阵"。

---

## 第三部分：待你拍板的问题（2026-10-17 20:35 已答复）

1. ~~hermes / deepseed harness 指什么~~ → 已调研确认：**Hermes = NousResearch/hermes-agent**；**deepseed harness = DeepSeek Harness (dsh)**。见第四部分。
2. ~~LangGraph 迁入还是删除~~ → **迁入**（"只要自研能被生态替换的尽量都迁入"）。
3. ~~IDE 路线~~ → **内置 IDE 模式：一键切换 IDE + 一键切换 Agent**（Cursor/Trae 式），见 4.4。
4. ~~分发优先级~~ → **双轨并行不冲突**：pip/一行命令先行（最方便），桌面 EXE 是"软件安装"另一条轨，见 4.5。
5. ~~三 GitHub 账号分工~~ → **已拍板：主仓 badhope，其余四仓全部纯镜像**；CI/CD 自动化与镜像同步方案见 Master Plan §8（`2026-10-17-master-plan.md`）。

---

## 第四部分：第二轮调研与决策（2026-10-17 晚）

> 调研方式：官方 README/docs/官方页 + 架构深读文章（pi 博客、opencode internals、
> OpenHands v1 路线、10 Design Elements gist），非二手报告。

### 4.1 两个名字的谜底

**DeepSeek Harness (`dsh`)**（deepseek-ai/deepseek-harness，2026-08 开源，MIT）
- **Everything is a Plugin**：模型适配器、工具注册表、agent loop、会话、沙箱全部可插拔；底层是 **Cordis 元框架**（论文《A Programming Paradigm for Spatiotemporal Composability》arXiv:2608.25512）
- `npx @deepseek-ai/dsh web` 一行安装；Web UI + 桌面双形态
- **Creator mode**：聊天里直接"帮我写个番茄钟插件"→ 自动生成/安装插件（技能+工具+界面三层）
- 插件发现走 GitHub topic（dsh-plugin）+ 官方插件目录 + Discord 社区
- 对 Spark 的意义：**"harness（座舱）"与"agent（大脑）"分离**是行业新范式；Spark 应自我定位为 open harness，插件/技能/界面三层全部可插拔

**Hermes Agent**（NousResearch/hermes-agent，MIT）
- **自进化闭环**（最大特色）：任务后自动创建技能；技能在使用中自我改进；周期性"记忆提醒"（nudge）驱动知识持久化；FTS5 全文搜历史会话 + LLM 摘要做跨会话回忆；Honcho 辩证式用户建模（"越用越懂你"）
- **无处不在**：单一网关进程接 Telegram/Discord/Slack/WhatsApp/Signal + CLI；语音转写；跨端会话连续；可跑 $5 VPS/无服务器，天然 headless
- **供应商层工业级**：`hermes model` 一键切换（Nous Portal/OpenRouter/Copilot OAuth/Anthropic OAuth/vLLM/Ollama），**Provider Routing + Fallback 链 + 凭据池（credential pools）**
- 已接 **ACP Host**、MCP、agentskills.io 开放技能标准；自带 cron 自动化（自然语言定时任务投递到任意 IM）
- 对 Spark 的意义：记忆系统（自进化技能+nudge+跨会话搜索）与供应商路由（fallback/凭据池）是最值得拿的两块

### 4.2 同类项目全景：架构级"值得拿"清单（全部拿来，不设上限）

| 项目 | 核心架构 | 直接可拿的（→ Spark 对应部件） |
|---|---|---|
| **OpenHands v1 SDK** | 事件流→同步 Conversation 的极简 SDK 化重构（arXiv:2511.03690） | API 分层范式：`Conversation` 一等公民 + 同步优先/asyncio 可选；SDK 独立成包（→ 我们的 spark-core API 设计） |
| **SWE-agent** | ACI 理论：工具接口设计比模型更重要（arXiv:2405.15793） | 编辑工具带 lint 反馈（写完即诊断）；工具 ergonomics 评审流程 |
| **aider** | repo map = git 图 PageRank + tree-sitter 符号 | repo_map.py 加 **git PageRank 排序**（我们目前按目录遍历偏置） |
| **opencode** | client-server（Hono HTTP + Go TUI）、Stainless 生成类型安全 SDK、LSP 诊断回流、primary/subagent 两层（build/explore/compaction/title） | **LSP 诊断闭环**（edit→didChange→diagnostics→context）；**OpenAPI→SDK 自动生成**（Spark Web API 治理）；compaction/title 用小 agent 的两层制 |
| **Claude Code** | hooks/skills/subagents/plugins 体系、三级记忆 | hooks 事件面扩展（post_message 等）；skills 装载语义对齐 |
| **Codex CLI** | Rust 核心 + **Landlock(Linux)/Seatbelt(macOS) OS 级沙箱** | 沙箱后端矩阵加 landlock 目标（bwrap 只是过渡）；`/status` 式透明面板 |
| **Goose** | "Stop building agents, start harnessing"——小内核+MCP 扩展 | 一切能力默认 MCP-first 的原则背书 |
| **Cline/Roo/Kilo** | Plan/Act 双模式；模式=系统提示+工具白名单组合 | 声明式模式档案（mode profile），UI 一键切 Plan/Act/Review |
| **Crush** | Catwalk：开源 provider/模型目录 | 模型目录数据源（替代我们 routing.py 硬编码矩阵） |
| **Gemini CLI / Qwen Code / iFlow** | 开源 CLI 矩阵、国内生态 | Qwen Code 对国产模型的兼容细节测试用例 |
| **Trae / Cursor** | IDE 壳 + 可切换 agent；IDE mode/Agent mode 无缝切换 | 见 4.4 IDE 模式设计 |
| **pi** | 极简内核、上下文全透明、会话格式文档化可回放 | **上下文审计**（每次请求可回放/可视化）；"克制清单"反向约束 |
| **ZCode / Qoder** | 消息级 Undo/Reapply、Usage 面板、Bot 渠道 | 双粒度回滚（消息级+任务级）；Usage 面板接真实计量 |

### 4.3 生态替换地图（"好的就换"——自研部件 → 生态件）

| Spark 现有自研 | 替换/增强方案 | 动作 |
|---|---|---|
| loop 手写 while | **LangGraph**（interrupt=审批、SqliteSaver=turn 级恢复、子图=子代理） | ✅ 已拍板迁入，v0.10 |
| routing.py 硬编码矩阵 | **models.dev / Crush Catwalk 数据源** + litellm router | 替换为数据驱动目录 |
| 单 provider 假设 | litellm 已在用 → 补 **fallback 链 + 凭据池**（学 hermes） | 增强 |
| repo_map 三桶 | aider 式 **git PageRank** | 增强 |
| 启发式 token | litellm stream usage 回填；不可回填才标 estimated | 修复 P1 |
| bwrap 沙箱 | 先修 `--share-net`（P0），中期加 **landlock 后端** | 修复+扩展 |
| Compactor 直调 litellm | 走 Provider 抽象；备选 LlamaIndex 保留 | 修复 P1 |
| FTS 手删索引 | 迁移到 **SQLite triggers** 维护 FTS | 修复 P0 的治本方案 |
| skills 手扫描 | 对齐 **agentskills.io / Claude Skills 开放标准**（含 ~/.claude/skills 全局层） | 对齐 |
| 记忆抽取 | 学 hermes：任务后自动产 SKILL.md + 使用中自改进 + nudge 机制 | 路线图 |
| 手写 MCP bridge | MCP 官方 SDK 全能力（resources/prompts/sampling/elicitation/OAuth/streamable HTTP） | 补全 |
| 手写 HTTP 层 | 评估 FastAPI/OpenAPI → **自动生成类型安全客户端**（学 opencode+Stainless） | 路线图 |
| 无 IDE 协议 | **ACP server**（Zed 已成熟、社区在扩散） | 新增 |
| CHANGELOG 手写 | **release-please** 自动化 | 新增 |

### 4.4 IDE 模式设计（一键切换 IDE / 一键切换 Agent）

```
┌─ Spark 内核（harness 层：审批/检查点/记忆/MCP/skills 统一） ─┐
│  Agent 档案（一键切换，hermes model 式）：                     │
│   spark-native │ claude-code │ codex-exec │ glm │ ...        │
│  接口层（一键切换 IDE/前端）：                                 │
│   ACP server ─→ Zed / 任何 ACP 客户端                         │
│   HTTP+OpenAPI ─→ Spark Web / 第三方（Cursor 式外挂宿主）      │
│   TUI ─→ 终端            Desktop ─→ EXE 壳                   │
└──────────────────────────────────────────────────────┘
```
- **Agent 切换**：会话级 `agent profile`——`spark agent use claude-code`（以 ACP/CLI 子进程方式驱动外部 agent，UI/审批/检查点/记忆仍是 Spark 的）→ "壳不换、大脑随便换"
- **IDE 切换**：Spark 常驻 server（HTTP+ACP），`spark ide attach zed|vscode|web` 一键接管当前会话；会话状态在服务端，前端随意换
- 这是 dsh"harness 化"+ Trae/Cursor"agent 可换"思路的本地化组合，也是与单体内核竞品的结构差异

### 4.5 分发双轨（确认不冲突）

1. **最快路径**：`uvx spark-agent` / `pipx install spark-agent` + 五平台一键安装脚本（国内镜像从 Gitee/GitCode 直装）→ 先行
2. **软件轨**：桌面 EXE（已有 PyInstaller spec）= "安装即用的软件"，与 pip 并行；后续补自动更新通道与签名
3. 两轨共用同一版本号与 release-please 自动 CHANGELOG

### 4.6 差异化头脑风暴（大胆版——与市面开源的真实区别）

1. **上下文合同（Context Contract）**：学 pi 但做成产品级——每个请求的完整装配（system/注入/裁剪/压缩决策）落盘可回放，Web 端可视化"这轮模型到底看到了什么"。**没有任何竞品把上下文透明度做成功能**，这是开发者信任的硬卖点。
2. **出网黑匣子**：把"本地优先"从口号做成**可验证**——可选本地审计代理，记录/放行/拦截 agent 的每次出网（web 工具、MCP HTTP、shell curl），一键导出合规报告。企业内网刚需，竞品全无。
3. **双粒度时间机器**：git_memory 的任务级 worktree+squash（已有）× ZCode 式消息级 Undo → "检查点/消息/任务"三层回滚矩阵，UI 统一成时间轴。
4. **自进化技能库（全本地）**：hermes 的自进化思路 + 我们的 skills：任务结束自动沉淀 SKILL.md、使用中自改进、nudge 驱动记忆固化——"越用越懂你"但数据不出机器。
5. **中国网络原生 harness**：MCP 注册表国内可达性探测（已做）扩展为全链路——模型路由带可达性维度、安装脚本走 Gitee/GitCode 镜像、插件目录含国内可达性标注。这是 OpenAI/Anthropic 系结构上做不了的。
6. **Harness 可换内核**（4.4）：Spark 统一座舱，Claude Code/Codex/GLM 都能当大脑——把竞品变成我们的可选项，而不是敌人。
7. **Creator mode 本地版**（学 dsh）：对话里生成/改进 skill、hook、MCP 配置，GitHub topic + 内置目录双轨分发插件。
8. **评测驱动发布**：已有 evals harness + perf budgets——每个 release 附"能力分数卡"（场景通过率、耗时、token 成本），版本间可对比。透明度即营销。
9. **IM 即 IDE**（学 hermes）：飞书/微信/Telegram 网关进程，手机上审批/派活/收 diff——国内团队协作场景。
10. **五端五平台工程秀**：MIT + 五平台镜像 + CI 全绿 + 分数卡，本身就是开源工程标杆叙事。

### 4.7 记录在案的其他好东西（未指定来源的"你觉得好就拿"授权项）

- **prompt caching 编排**（opencode ProviderTransform.applyCaching）：国产模型缓存计价差异大，值得做进 provider 层
- **结构化分片工具结果**（pi）：工具结果结构化分 part，前端/回放/压缩各自取用
- **差异化渲染 TUI**（pi retained-mode）：解决闪烁，TUI 体验对标
- **会话格式文档化**（pi）：sessions.jsonl 公开 schema，社区可做回放器/分析器
- **credential pools + fallback 链**（hermes）：多 key 轮换 + 失败转移
- **cron + IM 投递**（hermes）：定时任务自然语言配置
- **Plan/Act/Review 模式档案**（Cline/Kilo）
- **GitHub 回应式协作**（opencode 生态）：被 @ 时在 CI 里干活——Spark 云 CI 模式可后置

---

## 第五部分：独有性严格核验（2026-10-17 深夜，证伪式调研）

> 方法：对第四部分每条"差异化"主张主动找反例（官方文档/生态目录/竞品仓库）。
> 结论先行：**没有任何一条是发明级独有。** 该批评完全成立。

### 5.1 逐条核验表

| 主张 | 反例（已核实） | 判定 | 残余价值（正确表述） |
|---|---|---|---|
| 换大脑不换壳 | dsh 模型适配器+四源迁移向导（Claude Code/Codex/OpenCode/Hermes→dsh，迁会话/记忆/技能）；**iPolloWork**："unified multi-engine workspace for Codex/DSH/OpenCode"，企业级本地优先；Vibe Kanban/Claude Squad 编排多家 agent | ❌ 不独有 | 我们做的是"统一信任层的多内核"，不是多内核本身 |
| 任务级时间机器 | Claude Code **/rewind** 官方 checkpointing（每 prompt 检查点，代码+对话同回滚）；aider auto-commit+/undo；Vibe Kanban worktree 隔离 | ❌ 大部存在 | 残余：task 语义（分支+squash 回主干）与统一时间轴 UI |
| 上下文可回放 | Langfuse/LangSmith **自托管**全量 trace（prompt payload 级）；opencode session export/share；opencode-replay | ❌ 旁挂生态成熟 | 残余：零部署内建 + 证据文件化；方案改为 OTel→Langfuse 兼容 |
| 成本台账 | **litellm proxy** spend tracking/tag budgets（官方有 Claude Code 粒度归账教程） | ❌ 能力已存在 | 残余：内嵌化——"装上就有" vs "要运维一套 proxy" |
| 出网审计 | Claude Code 企业网络配置（代理/环境变量治理）+ TLS inspection 拼凑；dsh 生态有 Security & Governance 插件分类 | ⚠️ 拼凑存在，开箱证据级未见成熟实现 | **残余最强的一条**，但表述为"集成完整性"而非发明 |
| 国产多模型原生 | iFlow CLI（阿里，免费多模型 Qwen3-Coder/Kimi K2，hooks/SDK）；CodeBuddy CLI（腾讯）；Qwen Code | ❌ 拥挤赛道 | 残余：无厂商绑定（也不绑阿里/腾讯）+ MIT + 五端 |
| 自进化技能库 | hermes 完整实现（任务后建技能/自改进/nudge） | ❌ hermes 领跑 | 我们是集成者，学其设计 |
| Creator mode | dsh 原生（聊天生成插件+热加载） | ❌ | 集成，本地 Python 生态版 |
| IM 网关 | hermes 五通道（TG/Discord/Slack/WhatsApp/Signal） | ❌ | 残余：飞书/微信优先的国内通道 |
| 评分卡发布 | SWE-bench/OpenHands 公开评测惯例 | ❌ 惯例 | 工程实践，不称特色 |

### 5.2 核验后的残余优势（就四条，全部是"组合与执行"不是"发明"）

- **R1 信任链一体化**：审批签名 + 出网审计 + 上下文回放 + 回滚，**同一数据模型、同一时间轴、导出即证据包**。竞品的对应物全是旁挂/拼凑（Langfuse 要部署、企业拼 TLS 检查、Claude Code 无审计文件）。
- **R2 内嵌 vs 旁挂**：成本归账、观测、沙箱、fallback——竞品要装运维一套 proxy/面板，Spark 装上就有。部署形态差异，不是功能差异。
- **R3 开放格式反锁定**：sessions/上下文 JSONL 公开 schema + OTel 标准导出 + MIT。数据随时搬走，这是对"订阅绑架"的真实差异化。
- **R4 工程执行力**：五端同源、五平台镜像、CI、分数卡、证伪式核验文化本身。

### 5.3 企业/HR 问"你到底有什么用"的现实话术（禁用"本地化"等烂大街词）

- **对掏钱的人**：同一个任务，模型路线差 10–20 倍成本；Spark 把预算、降级链、按任务归账做成引擎内置——AI 开销从"黑盒订阅"变成"带台账的工程支出"。
- **对交差的人**：每个 agent 任务自动产出三份可归档文件——审计日志、成本流水、上下文回放。安全审查和财务流程直接接，不用为 AI 单开流程。
- **对干活的人**：并行任务编排（worktree）+ 团队规范自动执行（AGENTS.md+hooks）+ 夜间批处理——工程师从"盯着 agent 干活"变成"验收它产出的 PR"。
- **对面试官/HR（个人层面）**：完整交付过一个跨五端、五平台分发、有 CI/评测/文档体系的开源系统；所有卖点做过证伪式核验并公开否定了自己一半的初始判断——这展示的是工程判断力，而工程判断力是招人时最贵的东西。

---

## 第六部分：全量采纳决议 + 横向扩展原则（2026-10-17 深夜第三轮）

1. **全量纳入**：上一轮头脑风暴的全部候选（含此前"不推荐"的 IM 网关、插件市场治理）全部进入计划；排序原则=先信任链后通道（AEF 立住，通道类才有差异化载体）。里程碑扩为 P0–P5，见 master plan §10。
2. **大招 A 采纳**：AEF（Agent Evidence Format）开放标准 + Spark 作为参考实现 + `aef-export` 兼容转换器（dsh/opencode/CC 存储互转）。这是"自研变力干将"的主路径：**做标准的主人，不做功能的内卷者**。
3. **大招 B 采纳**：座舱五件套（审批/审计/回放/回放/计账）拆为可独立 pip 安装的嵌入库，允许竞品集成——走"成为基础设施"路线。
4. **横向扩展总原则**（新）：每个已有功能都视为扩展面，扩展≠照抄——每项必须写清"生态先例是谁 + 我们改什么"，否则不立项。18 项横向扩展地图定稿于 master plan §5.5（H1–H18），先例已核实（CC 30 hook 事件/四层设置、opencode small_model/commands/keybinds、codex profiles、Roo checkpoints、Gemini extensions、agentskills RFC、claude-code-action、Crush themes）。
5. 关键空白确认（先例调研中零成熟实现，属真空白）：MCP 沙箱运行时、PR 证据包、provenance blame、夜间安全包、团队记忆 MR 化、上下文火焰图。

