# Spark 竞争位与下一阶段路线：做“可审计的 Agent 工作台”，不做第二个 Cursor

> **定位说明（2026-11-23）**：本文写作于“本地优先”旧口径时期，该口径已废止。现行定位见 [PRODUCT_POSITIONING](../PRODUCT_POSITIONING.md)；本文仅作历史审计与事实记录，不再作为路线依据。


> 调研日期：2026-11-23｜范围：ZCode 3.18.2 源码与 3.19.1 文档、Cursor、Claude Code、Codex、Qoder、Trae、Cline/Continue，以及 Spark 本仓库代码、测试与本机实测。

## 核心判断

**Spark 不应把主战场放在“编辑器内 Tab 补全”，而应把已经形成的底层能力收束成“本地优先、可审计、模型自由的 Agentic Development Environment”。** ZCode 真正值得学的不是模型参数，而是任务闭环、Goal 验证、Review、远程接管、设计系统与性能遥测；它明确不提供默认操作系统沙箱，这恰好是 Spark 可以放大的信任长板。

但 Spark 目前还不是“第一梯队产品”，因为最关键的三类证据缺失：没有内联补全/NES、没有真实模型的任务准确率基线、没有把 59 个工具和 6 档审批组织成用户可感知的任务工作台。先用两周建立评测与延迟标尺，再用四周补任务闭环，最后才做编辑器桥接；18 周内不追完整 Tab 补全。

## 一、市场坐标：Agent-first 已成主流，补全只是入口之一

2026 年的 AI 编程产品已经分成两类。一类是 IDE 原生补全路线，Cursor、Qoder、Trae 用 Tab/NES 把 AI 嵌入输入法级别的编码动作；另一类是 Agent 工作台路线，ZCode、Claude Code、Codex 用自然语言任务、工具调用、计划、审查和远程执行完成整段工作。两条路线都在向对方扩张，但竞争焦点已经从“能不能生成代码”转向“能不能稳定交付一个可审查的结果”。

| 产品 | 形态 | 最强能力 | 明显短板 | 对 Spark 的启示 |
| --- | --- | --- | --- | --- |
| ZCode | 桌面 ADE + CLI/Web | GLM-5.3 深度适配、1M 上下文、Goal、Review、Remote/Bot、Idle Task | 模型重心单一；无默认 OS 沙箱；曾发生数据上传争议；Electron 体量大 | 学任务闭环与工程体系，不学“把模型当卖点” |
| Cursor | IDE + 云 Agent | Tab/NES、Composer、并行云任务、代码库语义索引 | 云优先、用量计费不透明、难自托管 | 补全的体验标杆，但不是 Spark 应正面硬碰的战场 |
| Claude Code | 终端 + IDE/Web/Cloud | 权限、Hooks、Subagents、长任务、Agent Teams | 模型锁定、终端学习曲线、上下文漂移 | 把“权限与可验证交付”做成产品语言 |
| Codex | CLI + IDE + Cloud | 本地沙箱、云端并行任务、Worktree、多 Agent | OpenAI 生态绑定、云端依赖 | 沙箱与云端任务的产品化方式 |
| Qoder | IDE + Quest 工作台 | NEXT 补全、RepoWiki、Experts、10 万文件级代码库分析 | IDE 重、Credits 计费、能力多而杂 | 代码库智能与多智能体流水线 |
| Trae | IDE + SOLO | 多项目/多 Agent、Figma/Supabase/Vercel 集成、Cue 补全 | 应用生成导向、索引可能临时上云 | 场景闭环与“从想法到上线” |
| Cline/Continue | VS Code 插件 | 开源、BYOK、轻量 | 任务工作台与审查体验弱 | 开源分发与模型自由，但别只做插件 |
| Spark | CLI/TUI/Web/Desktop/IDE/ACP/A2A/MCP | 本地审计、模型自由、多端连续、真实沙箱 | 无内联补全、无真实评测、任务工作台不完整 | 用“信任 + 自由 + 连续”组合差异化 |

各产品的形态与能力依据官方文档、发布页与独立评测整理。[17][18][19][20][21][22][23]

这张表最重要的结论不是“谁功能多”，而是：**ZCode、Claude Code、Codex 已经证明 Agent-first 可以成为第一梯队；Cursor、Qoder、Trae 证明补全是留存入口，但不是唯一护城河。** Spark 不需要同时赢两条路线，必须先选一条主路线，再把另一条作为桥接。

## 二、ZCode 拆解：它的强项是一套“把长任务关进工作台”的工程体系

### 2.1 交互逻辑：以任务为中心，而不是以文件为中心

ZCode 的官方定位是 ADE（Agentic Development Environment），不是传统 IDE。它把“任务”作为最小工作单元：同一任务里保留目标、文件引用、终端结果、浏览器上下文、执行模式与 Git 分支状态；输入框支持 `@` 引用文件/文件夹、`/` 命令、`$` 技能，侧边对话用于中途追问；执行模式分为逐次确认、自动编辑、先计划、全权限四档；思考强度分为 Low/High/Max 三档。

这套设计的价值在于**连续性**：用户不需要在“聊天、终端、编辑器、Git、浏览器”之间反复搬运上下文。任务完成后进入 Review，按任务查看变更、行数和文件列表，再决定提交或回滚。对长任务，Goal 模式把目标、完成验证和状态恢复放在同一条执行链里，而不是让模型“聊完就算”。[1][2]

### 2.2 算法与准确率：模型能力被产品化为一组可控参数

ZCode 与 GLM-5.3 做了深度联调：1M 上下文、长程 RL、工具调用、子任务分解、环境反馈与反作弊训练；官方给出的 Terminal-Bench 3.0 为 28.3、DeepSWE v1.1 为 66.9、Agents' Last Exam 为 28.5。[3] 这些是**模型在标准 harness 上的成绩，不等于 ZCode 产品的任务成功率**；产品准确率还取决于上下文选择、工具权限、验证闭环和重试策略。

独立评测给出了另一面：在 Terminal-Bench 4.0 上，GLM-5.3 约 41.8%，Claude Fable 5.1 约 57.9%，GPT-6 Astra 约 58.2%；GLM-5.3 的单次运行成本约 2,728 美元，明显低于 Claude 的 6,200 美元。[4] 这说明它的竞争力是“单位成本下的可用性”，不是绝对精度。早期 ZCode 3.x 的用户反馈也集中在速度与稳定性：约为 Opus 的一半、请求经常需要重试、配额不透明；3.19.1 的更新日志显示官方持续在修流式卡顿、重试等待、MCP 启动和长对话性能。[5][6]

### 2.3 场景适配：桌面、远程、Bot 三套入口共用同一任务

ZCode 不只做本地桌面：它支持手机 Remote 与飞书/微信 Bot，用户可以在离开电脑后继续给同一任务补充指令；订阅用户还可以把非紧急任务放进 Idle Task，在空闲算力上免费执行。这个设计的本质是**把 Agent 从“坐在电脑前的一次对话”变成“可以排队、可以远程接管的工作流”**。[1]

### 2.4 工程体系：为什么它看起来“像一个产品”

对 3.18.2 源码做本地统计，仓库有 18,680 个受控文件、1,974 个测试文件，版本已到 3.19.1；这不是一个小团队两周能搭出来的壳。更关键的是三份内部工程文档：

- `DESIGN.md` 把颜色、字号、间距、桌面窗口控制、明暗主题写成仓库级约束，而不是靠设计师口头维护。
- `docs/monitoring/ttft-stage-telemetry.md` 把首字延迟拆成提交、排队、上下文、模型、首 token、渲染等阶段，默认只写本地日志、不上传云端。
- `docs/agents/runtime-boundaries.md` 明确桌面本地 Host、CLI 子进程、远程 workspace、移动端快照/补帧、owner/lease、命令 FIFO 的边界。

这三件事共同解释了 ZCode 的“完整感”：它不是功能堆出来的，而是把交互、性能、运行时边界当成同一套产品系统来设计。[7][8][9]

### 2.5 不能照抄的部分：信任边界是它的结构性弱点

ZCode 的 `NOTICE.md` 明确写着：当前共享 Agent 执行适配器**不提供默认的操作系统沙箱**；文件、终端、Git、外部进程都在实际运行账号的权限范围内读写；独立 CLI 用 `--prompt` 非交互执行且未指定模式时，默认进入 `yolo`。[10] 这意味着它的“全权限”体验是以扩大攻击面为代价的。

2026 年 9 月的仓库快照上传争议进一步放大了这一点：开发者发现客户端会打包工作区（含 `.git` 历史、LFS 缓存、reflog）并尝试上传；313MB 的商业项目快照因超限失败 564 次，但一个约 15KB 的公开仓库快照被服务端接收。官方随后致歉、在 3.14.0 移除 Repo Wiki 与快照上传链路、开源客户端，并引入第三方核查；但历史数据是否被访问/解密/备份，公开报告未能完全倒推自证。[11][12] 这不是“ZCode 一无是处”，而是提醒 Spark：**信任一旦被打破，功能优势很难单独修复它。**

## 三、Spark 的真实底牌：四个长板，组合起来才难复制

### 3.1 长板一：真实执行边界，而不是“权限提示”

Spark 的 6 档审批（suggest、auto-edit、full-auto、plan-only、approve-plan、manual）与 4 档沙箱（sandbox-only、workspace、full-access、unrestricted）不是 UI 开关，而是执行层约束：Linux 用 `bwrap` 命名空间、macOS 用 `sandbox-exec`、Windows 用 Job Object 做进程树与资源约束；工作目录逃逸、敏感文件名（`.env`、私钥、`.git/hooks`）默认拦截；检查点快照刻意不写入 `.git`，避免 Git 钩子成为持久化后门。[13]

这套组合的直接价值是：**用户能在“让 Agent 干活”和“知道它最多能干到哪里”之间做选择，而不是只能在“全信”与“不用”之间二选一。** ZCode 的 NOTICE 明确没有默认 OS 沙箱，Spark 在 Linux/macOS 上有真实系统级隔离；这是可验证、可写进安全白皮书的差异，不是营销话术。

### 3.2 长板二：模型自由，且不是“随便填一个 Base URL”

Spark 支持 OpenAI-compatible、Anthropic、Gemini、Azure、Bedrock、Ollama、llama.cpp、Mock 等 provider，带凭据池、fallback 模型链、成本路由与本地 GGUF 目录；本地模型目录已扩到 5 个（MiniCPM5-1B/2B、Qwen3.5-2B/4B、BotPsy-0.8B），直链与体积均做过核验。[13] 对国内用户，这意味着不必被单一模型订阅锁定；对隐私敏感用户，可以完全走本地。

ZCode 也支持多家模型，但产品默认与 GLM-5.3 深度绑定，配额、缓存、工具优化都围绕自家模型；Cursor/Qoder 的模型选择更多是平台内菜单。Spark 的“模型记分牌 + 自动路由”如果做出来，会是一个更中性、更可解释的选择层。

### 3.3 长板三：多端连续，而不是“多一个客户端”

Spark 已有 TUI、Web、Desktop、VS Code 聊天面板、JetBrains/Neovim ACP、A2A server、MCP server、relay、手机 PWA 远程审批与推送；会话租约、设备配对、审计和后台队列把“换设备”做成同一条任务链。[13] ZCode 有桌面/Remote/Bot，Qoder 有 Quest/Remote，Claude Code 有终端/IDE/Web，但 Spark 的协议面更宽：它既能做人用的工作台，也能做别的 Agent/系统调用的执行端。

这不是大众卖点，却是企业/团队场景的入口：一个团队可以把 Spark 放进自己的 MCP/A2A 编排里，而不是再买一个封闭 SaaS。

### 3.4 长板四：轻量，且不是“功能少所以轻”

本机性能基线显示 CLI 启动 p50 约 155.8ms、RSS 约 37MB；仓库约 8 万行 Python/TS/TSX/JS、722 个受控文件、179 个测试文件，非浏览器测试 1,859 项通过。[14] 对比 ZCode 的 18,680 个受控文件、1,974 个测试文件与 Electron 桌面，Spark 的架构更适合低配机器、服务器、CI 和无头环境。

轻量不是“低端”的同义词。相反，它让 Spark 可以进入 ZCode/Cursor 进不去的地方：4GB 内存的笔记本、SSH 服务器、容器、CI runner、离线机房。

## 四、Spark 的硬伤：不是功能少，而是“体验证据”缺失

### 4.1 没有编辑器内补全/NES，当前是“聊天式改代码”

对 `vscode-spark` 与 `src/spark` 做源码检索，没有 `CompletionItemProvider`、`InlineCompletion` 或 Next Edit Suggestion 的实现；VS Code 扩展是聊天面板 + 内联 diff 审批，不是 Cursor/Qoder/Trae 那种输入即补全。[14] 对话里让模型“补一段代码”不等于编辑器内补全；后者要求 100-300ms 级延迟、上下文裁剪、FIM/Next Edit 专用模型和接受率优化，是完全不同的产品能力。

这不必然致命：ZCode、Claude Code 也不是以 Tab 补全为主卖点。但如果 Spark 对外宣称“AI 编程软件”并直接对标 Cursor/Qoder/Trae，这就是阻断成交的硬伤。**要么明确承认自己是 Agent 工作台，要么投入 6-8 周做 VS Code 内联编辑桥接；不能含糊。**

### 4.2 没有真实模型准确率基线，“12/12”只是 mock 回归

仓库 `evals/baseline.json` 的 12 个场景全部由脚本化 provider 驱动，测的是工具链与解析器，不是模型在真实仓库里的任务成功率；没有 SWE-bench、Terminal-Bench、RepoBench 或内部真实任务的 pass@1 记录。[14] 这导致三个后果：无法判断“准确率差”是模型问题、上下文问题还是 harness 问题；无法做模型路由；无法对外承诺质量。

本机用 MiniCPM5-2B Q4_K_M 做了一次真实小任务：在 2 个文件的 `add()` bug 修复上，Agent 最终改对并通过测试，但耗时 90.9 秒，期间出现重复读文件、先跑测试后改代码的冗余路径；同一模型在简单 FastAPI 问答里给出了不够规范的写法。n=1 不能证明准确率，但足以证明“本地 2B 可用，不等于可作主力”。[14]

### 4.3 延迟问题在任务层，不在首 token

同一台 4 vCPU/8GB、无 GPU 的机器上，MiniCPM5-2B 的首 token 约 0.68 秒、生成约 64.8 字符/秒，单看流式体验并不差；但端到端小任务 90.9 秒，说明瓶颈在**多轮工具调用、重复上下文、验证回路与串行执行**，不是模型首字。[14] 目前 Spark 没有把 TTFT 拆成提交/排队/上下文/模型/首 token/渲染阶段的产品化遥测，也没有跨 provider 的提示缓存；只有 Anthropic 原生 prompt cache，OpenAI-compatible 仅在响应里回传 cached tokens。[13]

ZCode 把 TTFT 阶段遥测写进仓库文档，并针对长对话做了多轮性能修复；这是 Spark 应该先补的“可观测性”，而不是盲目换更快的模型。[8]

### 4.4 代码库智能还停留在“符号 + 文本检索”，没有规模化语义索引

Spark 有 tree-sitter 仓库地图、LSP 9 类工具、Repo Wiki、BM25+向量知识库，但没有面向代码补全/跨文件任务规划的代码语义索引；Qoder 明确支持 10 万文件级项目分析，ZCode 用 1M 上下文和深度仓库理解做长任务。Spark 在 50 个文件的小仓库里够用，在 5,000 文件以上的真实项目里会出现“找不全、带不全、改不动”。[13][15]

### 4.5 任务工作台不完整，核心闭环还差一层“产品壳”

Spark 有 `QueueSection`、`ChangesPanel`、`CheckpointModal`、任务分支/worktree/工作流引擎，但没有 ZCode/Qoder 那种“任务列表 + 任务状态 + Review 队列 + 交付清单”的统一工作台；也没有远程 SSH/Docker workspace，只有对本地会话的远程控制。[14] 这导致底层能力很强、用户却要自己拼：先开队列，再开变更面板，再去检查点，再去 Git。**功能存在 ≠ 任务闭环成立。**

### 4.6 分发与信任资产薄弱

Spark 当前版本 0.15.4，构建产物未签名，插件市场没有签名/撤销机制，官方托管 relay 与计费体系未落地；ZCode 已到 3.19.1、公开报道用户达百万、GitHub 8.7k stars，Qoder/Cursor/Trae 都有成熟安装与增长链路。[6][16] 这不是“再写一个功能”能补的，需要签名证书、安装器、新手引导、文档、社区与稳定发布节奏。

### 4.7 定位文档自相矛盾

`docs/ARCHITECTURE.md` 开头写“API-first，本地/自托管只是可选部署方式，不是产品卖点”，但 README 与产品实际能力强调本地优先、隐私与离线。这个矛盾会直接污染路线图：如果本地优先不是卖点，就不会为沙箱、离线模型、审计投入；如果是卖点，就不该把云端 API 当默认叙事。**在开始任何市场动作前，先把一句话定位定死。**

## 五、战略选择：三条路，推荐 B

| 路线 | 做法 | 成本 | 差异化 | 风险 | 结论 |
| --- | --- | --- | --- | --- | --- |
| A. 第二个 Cursor | 自建/ fork IDE，优先 Tab/NES + Composer | 极高（编辑器、模型、GPU、索引） | 低，正面撞 Cursor/Qoder/Trae | 补全质量与生态都难追上 | 不做主路线 |
| B. 可审计 Agent 工作台 | 本地优先 + 任务闭环 + 模型自由 + 多端连续 | 中高（3-4 人、18 周） | 高，信任与自由是组合差异 | 需要真实评测与产品化 | **推荐** |
| C. Agent 基础设施 | MCP/A2A/ACP/CLI/企业控制面 | 中（偏后端） | 高，但受众窄 | 没有用户产品与付费验证 | 作为第二曲线 |

推荐 B 的理由不是“本地优先听起来正确”，而是它同时满足三个市场空位：

1. **信任空位**：ZCode 事件后，企业与个人都开始问“代码到底去了哪里”；Spark 的沙箱、检查点、无遥测、可离线是现成答案。
2. **模型空位**：国内开发者需要国产模型、本地模型、BYOK 和成本可控；Cursor/Claude Code/ZCode 都不同程度绑定自家模型或订阅。
3. **连续空位**：任务从桌面开始、手机审批、IDE/CLI 继续，Spark 已有租约/relay/PWA/ACP 的底层件，缺的是一层统一任务 UI。

一句话定位：**“Spark：本地优先、可审计、模型自由的 AI 编程工作台。代码去哪里，你说了算；每一步做了什么，你看得见。”**

目标客群按优先级：
- P0：国内 1-20 人小团队/独立开发者，机器一般、预算敏感、用国产模型、在意代码不出境。
- P1：高校/科研/政企外包，不能把代码放到境外云，需审计、离线与自托管。
- P2：企业平台/自动化团队，用 A2A/MCP/CLI 把 Spark 接进自有系统。

## 六、放大长板：把四个能力变成用户能感知的承诺

### 6.1 隐私回执：每一轮都告诉用户“什么离开了本机”

不是再写一篇隐私政策，而是在任务页显示一张可导出的“数据出境回执”：本轮走了哪个模型端点、发送了多少 token、涉及哪些文件、是否有本地模型可替代、是否触发浏览器/搜索/更新检查。本地模型任务显示“0 代码出境”；云端任务明确“仅发给你配置的模型端点，不经过 Spark 云端”。这直接对应 ZCode 事件后的用户疑问，也是 Spark 最难被复制的地方。

验收：构造 10 个含 `.env`、私钥、`.git` 历史的仓库，自动测试“未授权文件不进入上下文”；每次任务都能导出回执；默认无遥测（更新检查可关）。

### 6.2 交付账本：把“改了什么、凭什么说改对了”做成可审查物件

把任务分支、diff、测试结果、审批记录、检查点、成本、回滚入口收束到一张“交付账本”。用户不需要理解 59 个工具，只需要看到：改了哪些文件、跑了哪些验证、哪些步骤需要人审、失败能否一键回滚。ZCode 的 Review 与 Qoder 的 Delivery Manifest 都在做这件事；Spark 有底层件，缺的是统一呈现。

验收：每个任务可导出 Markdown/JSON 账本；review 页面能按任务看 diff、测试证据、审批与成本；回滚后账本保留审计记录。

### 6.3 模型记分牌：让“选模型”从玄学变成可解释决策

用 30 个内部真实任务（修 bug、加功能、重构、测试生成、跨文件检索）跑 3 类模型：国产云模型、国际云模型、本地 GGUF。每个模型给出 pass@1、TTFT p50/p95、每任务 token 成本、人工干预次数；产品里显示“这类型任务推荐哪个模型”，并允许一键 fallback。ZCode 的“GLM 深度适配”是单模型优化，Spark 可以做“多模型可解释路由”，这是另一种差异化。

验收：模型记分牌每月更新；路由策略可解释、可关闭；本地模型任务明确标注能力边界，不把 2B 包装成“全能”。

### 6.4 跨端连续任务：从“远程控制”升级为“任务跟着人走”

把现有 relay、租约、PWA、推送、ACP 组合成一个用户动作：桌面发起任务 → 手机收到审批/进度 → 一键接管 → 回到 IDE/CLI 继续。ZCode 的 Remote/Bot 已经证明这个场景有需求；Spark 的协议面更宽，但还缺一条顺滑的“任务上下文同步”链路。

验收：同一任务在桌面、手机、VS Code 三端状态一致；断线重连不丢事件；审批在手机完成后再回到桌面能继续；不要求用户重新解释上下文。

## 七、补齐短板：按“阻断成交、影响留存、品牌溢价”排序

| 优先级 | 动作 | 为什么现在做 | 验收指标 | 估算 |
| --- | --- | --- | --- | --- |
| P0 | 真实评测 + TTFT 阶段遥测 | 没有基线，所有“准确率/速度”判断都是猜 | 30 任务×3 模型×2 轮；TTFT p50/p95；pass@1、干预次数、token 成本 | 2 人×2 周 |
| P0 | 任务工作台 v1 | 底层能力已有，缺统一闭环；直接影响留存 | 任务列表/状态/Review/交付账本/回滚；5 人可用性测试≥4/5 | 2 人×4 周 |
| P0 | 代码语义索引 v1 | 跨文件任务准确率的上限；当前只有符号/文本 | 50 个真实查询 recall@10≥80%；5,000 文件仓库任务成功率+10pp | 2 人×4 周 |
| P0 | 延迟优化 | 本地 2B 小任务 90.9s；用户感知是“卡” | 云模型 TTFT p50≤2.5s、p95≤6s；本地 2B 小任务≤60s；token 成本-30% | 2 人×3 周 |
| P1 | VS Code 内联编辑 + NES | 补全缺口；但先做“选中代码→自然语言改”而非全量 Tab | 内联编辑接受率≥25%、p95≤1.5s；不回归现有聊天面板 | 2 人×6 周 |
| P1 | Goal 自动续跑 + 证据门 | ZCode 的 Goal 已证明长任务需要自动推进；Spark 现在偏手动 | 目标未完成时自动下一轮；每轮有测试/命令证据；可暂停/恢复 | 1 人×2 周 |
| P1 | 签名、安装器、新手引导 | 0.15.4 未签名；分发是信任的一部分 | Windows/macOS 签名；安装成功率≥95%；首启 3 步内跑通 | 1-2 人×3 周 |
| P2 | 远程 SSH/Docker workspace | 团队场景；ZCode/Qoder 已有 | 远程工作区创建/连接/断线恢复；与本地任务同 UI | 2 人×6-8 周 |
| P2 | 完整 Tab 补全 | 成本最高、正面撞 Cursor；只有内联编辑数据达标才做 | p95≤300ms、接受率≥30%、日活使用率≥20% | 3 人×8-12 周 + GPU |
| P2 | 插件市场签名/撤销 | 生态与供应链信任 | 签名校验、撤销列表、权限清单、审计日志 | 2 人×4-6 周 |

**不做清单**：不做完整 IDE 替换；不做第二个 Electron 壳；不先做 Bot/语音/主题；不把 2B 本地模型当主力；不用厂商自报 benchmark 做营销；不在没有评测的情况下宣称“第一梯队”。

## 八、阶段计划：18 周，四次过门

以下按 3 人等效投入估算；单人串行约 8-10 个月。时间不是承诺，过门指标才是。

| 阶段 | 周期 | 交付 | 过门指标 | 资源 |
| --- | --- | --- | --- | --- |
| Phase 0 立标尺 | 2 周 | 真实评测集、TTFT 阶段遥测、3 模型基线、10 人访谈提纲 | 30 任务可复跑；报告能解释失败原因；无“拍脑袋第一梯队” | 1 Agent/后端 + 1 QA |
| Phase 1 任务闭环 | 4 周 | 任务工作台 v1、交付账本、隐私回执、回滚/审计 | pass@1 较基线+10pp；任务完成时间-25%；5 人可用性≥4/5；0 起未授权外发 | 2 前端 + 1 后端 + QA |
| Phase 2 上下文与延迟 | 4 周 | 语义索引 v1、模型路由/缓存、Goal 自动续跑 | recall@10≥80%；云 TTFT p50≤2.5s；本地小任务≤60s；token 成本-30% | 2 后端/AI + 1 前端 |
| Phase 3 编辑器桥接 | 4 周 | VS Code 内联编辑 + NES + 诊断；补全 A/B | 接受率≥25%、p95≤1.5s；聊天面板零回归；Tab 门禁数据达标才立项 | 2 IDE/前端 + 1 模型 |
| Phase 4 发布与规模化 | 4 周 | 签名安装器、新手引导、文档、插件签名、远程 workspace PoC | 崩溃-free≥99.5%；安装成功≥95%；30 任务 pass@1≥65% 或与 ZCode 同 harness 差距≤10pp | 全员 + 设计/安全 |

Phase 0 的关键不是“再写功能”，而是**把争论变成数字**：同一个任务，Spark、ZCode、Claude Code 各跑一遍，记录成功率、时间、token、人工干预。没有这张表，后面的路线图都会重新变成拍脑袋。

Phase 1 的关键不是“做任务列表”，而是把已有的队列、变更、检查点、审批、成本串成一条用户能看懂的执行链。Phase 2 才解决“为什么找不到文件/为什么慢”。Phase 3 才碰补全。Phase 4 才谈规模化。顺序不能反。

## 九、资源与预算：3-4 人等效，不要用堆人解决

| 角色 | 职责 | 投入 |
| --- | --- | --- |
| Agent/后端工程师 | 评测、上下文、工具、沙箱、Goal、路由 | 1.0 FTE，18 周 |
| 前端/IDE 工程师 | 任务工作台、Review、VS Code 内联编辑 | 1.0 FTE，Phase 1 起 |
| 模型/基础设施工程师 | 本地推理、FIM/NES、缓存、GPU/云基准 | 0.5-1.0 FTE，Phase 2 起 |
| QA/安全 | 评测执行、沙箱逃逸、外发审计、发布门禁 | 0.5 FTE，全程 |
| 产品/设计 | 定位、任务流、可用性测试、文档 | 0.5 FTE，Phase 0-4 |

预算估算（仅增量，按月/一次性标注）：
- 云模型评测：一次性 3,000-8,000 元；稳定期 500-1,500 元/月。
- GPU 租用/云桌面：1,000-3,000 元/月，Phase 2-3 共 2-3 个月；本地 4 vCPU 只做 smoke，不做训练/大规模评测。
- Windows 代码签名证书：约 2,000-4,000 元/年；Apple Developer 约 700 元/年，Phase 4 前办理。
- 设计/可用性测试：5,000-15,000 元或 2-4 周外部支持；也可用 AI 生成原型，但真人测试不能省。

单人模式不要按 3 人并行排期：先做 Phase 0 + 任务工作台，再做上下文/延迟，补全放最后；每阶段结束写一页“过门/不过门”结论，避免长期处于“什么都做了一点”的状态。

## 十、风险与边界

1. **安全叙事不能说过头**。Spark 默认无遥测，但云端模型任务仍会把上下文发给你配置的模型端点；只有本地模型才是“代码不出本机”。更新检查默认会访问 GitHub，可关闭。对外必须说“无厂商遥测、模型端点可见”，不能说“永不出网”。
2. **厂商 benchmark 不能直接当产品准确率**。GLM-5.3 的官方分数与独立 Terminal-Bench 分数有差距；ZCode 的产品体验还受工具、上下文、重试影响。Spark 必须建立同 harness 对比。
3. **本地 2B 不是万能解**。它能做单文件修复，但复杂重构、跨文件理解、长任务规划仍需要云模型；产品应明确“本地优先、云端增强”，而不是把本地模型包装成全能。
4. **范围蔓延是最大风险**。任务工作台、语义索引、补全、远程 workspace、插件市场、Bot 一起做，会回到“功能很多、体验很散”。按过门指标砍功能。
5. **信任修复是慢变量**。ZCode 开源、第三方审计、补偿之后仍有用户不原谅；Spark 一旦出现一次数据外发，代价同样高。安全测试与回执要先于市场宣传。
6. **市场空位可能比想象中窄**。本地优先/可审计能打动企业与隐私敏感用户，但大众开发者仍可能为补全和速度留在 Cursor/Trae。Phase 0 必须做 10 人访谈，验证“信任”是否真的能转化为付费。

## 结论

1. **Spark 已经有第一梯队的底层骨架，但没有第一梯队的可验证体验。** 59 个工具、6 档审批、4 档沙箱、多端协议、模型自由都是真资产；缺的是评测、任务闭环、延迟与分发。
2. **最值得放大的不是功能数量，而是“可信执行 + 模型自由 + 多端连续”的组合。** 单点都能被复制，组合很难；尤其在有 ZCode 信任事件的市场上，“可审计”是稀缺品。
3. **下一步先做 2 周基线，再做 4 周任务工作台；不先做 Tab 补全。** 18 周的目标不是“超过 Cursor”，而是“在可信 Agent 工作台这个格子里做到第一梯队”，然后再用编辑器桥接扩大入口。

## 参考来源

1. [ZCode Docs：《Welcome to ZCode for GLM-5.3》](https://zcode.z.ai/en/docs/welcome)
2. [ZCode Docs：《ZCode Agent》](https://zcode.z.ai/en/docs/agents)
3. [Z.ai：《GLM-5.3: Frontier Coding with Emergent Cyber Capabilities》](https://z.ai/blog/glm-5.3)
4. [KGen：《GLM Coding Plan Review (2026): Speed, Limits, Verdict》](https://kgen.cloud/glm-coding-plan-review)
5. [eesel：《The 8 best ZCode alternatives in 2026》](https://www.eesel.ai/blog/zcode-alternatives)
6. [ZCode：《版本发布与更新》](https://zcode.z.ai/cn/changelog)
7. [ZCode 仓库：《DESIGN.md》](https://github.com/zai-org/ZCode/blob/main/DESIGN.md)
8. [ZCode 仓库：《TTFT stage telemetry》](https://github.com/zai-org/ZCode/blob/main/docs/monitoring/ttft-stage-telemetry.md)
9. [ZCode 仓库：《Agent runtime boundaries》](https://github.com/zai-org/ZCode/blob/main/docs/agents/runtime-boundaries.md)
10. [ZCode 仓库：《NOTICE.md》](https://github.com/zai-org/ZCode/blob/main/NOTICE.md)
11. [央广网：《智谱股价三个交易日累计跌逾16%，公司回应ZCode数据上传争议》](http://finance.cnr.cn/ycbd/20260923/t20260923_527823143.shtml)
12. [eSecurity Planet：《Z.ai Disables ZCode Features Over Privacy Concerns》](https://www.esecurityplanet.com/news/news-zai-zcode-privacy-concerns-apac-china/)
13. [Spark 仓库源码与测试（本地审计）](https://github.com/badhope/spark)
14. [Spark 本机性能与评测基线（本地实测）](https://github.com/badhope/spark)
15. [Qoder Docs：《NEXT 代码建议》](https://docs.qoder.com/zh/plugins/next)
16. [新浪财经：《ZCode被曝打包代码上传，智谱道歉并整改》](https://finance.sina.com.cn/roll/2026-10-01/doc-initstui1351408.shtml)
17. [Cursor 官网](https://cursor.com/)
18. [Cursor Docs：《Composer 2》](https://cursor.com/docs/models/cursor-composer-2)
19. [Claude Code Docs：《Create custom subagents》](https://code.claude.com/docs/en/sub-agents)
20. [OpenAI Developers：《Codex CLI》](https://developers.openai.com/codex/cli)
21. [Qoder CN 官网](https://qoder.com.cn/ide)
22. [Trae 官网](https://www.trae.cn/)
23. [Trae Docs：《SOLO 模式概览》](https://docs.trae.ai/ide/solo-mode?_lang=zh)
