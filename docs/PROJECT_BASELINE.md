# Spark 项目基线（2026-11-24）

> 本文是 Spark 的**项目基线单一来源**：当前核心状态、已交付功能、待开发任务池、迭代路线图都在这里对齐。
> 版本口径以 `main` 分支为准；本文与代码、远程主分支文档同一次提交发布，不接受“文档在本地、远程还是旧版”的分叉状态。
> 产品定位的唯一口径仍是 [`PRODUCT_POSITIONING.md`](PRODUCT_POSITIONING.md)：**浏览器原生、跨平台、开源可扩展的 AI 编程应用**。

## 0. 基线条目

| 项 | 当前值 |
|---|---|
| 产品 | Spark |
| 版本 | `0.15.4` |
| 主分支 | `main` |
| 许可 | MIT |
| 包入口 | `spark` / `python -m spark` |
| 主要入口 | Web、CLI/TUI、桌面壳、VS Code、JetBrains/Neovim（ACP）、A2A API |
| 代码规模 | `src/spark` 189 个 Python 源文件（mypy 口径） |
| 最近全量基线 | 见 §2.6；本轮新增定向测试、真实本地模型验收与 120 例 Autopilot 压力矩阵 |
| 五平台 | GitHub `badhope/spark`、`X33834/spark`、`Morningstar202604/spark`；Gitee `badhope/spark`；GitCode `badhope/spark` |

## 1. 当前核心状态

### 1.1 产品身份

Spark 是 AI 编程应用，不是通用办公套件，也不是单一 IDE 插件。主线是：

1. 理解编程任务与仓库约束；
2. 检索代码、符号、测试与历史；
3. 生成可审计的编辑；
4. 运行测试并把失败回灌；
5. Review、回滚、提交与交付。

文档、会议、表格只存在于企业控制面，属于辅助工作流，不构成产品定位。

### 1.2 架构现状

| 层 | 当前实现 | 边界 |
|---|---|---|
| 入口层 | Web、CLI/TUI、桌面壳、VS Code、ACP、A2A | 多入口共用同一套后端能力，不各自维护第二套 Agent |
| 会话层 | LangGraph 状态机 + SSE + 持久化 turn checkpoint | 审批挂起可恢复；进程崩溃后的无人值守续跑仍需加强 |
| Agent 层 | ReAct + 工具循环 + 声明式子 Agent + Goal + Workflow DAG + Autopilot 状态机 | 无人值守闭环第一版可用；队列调度、崩溃恢复、真实长任务基准仍缺 |
| 工具层 | 文件/搜索/Shell/PTY/Git/LSP/浏览器/Notebook/MCP/插件 | 所有执行必须经过 Registry、审批与沙箱 |
| 模型层 | OpenAI 兼容、Anthropic、Gemini、Azure、Bedrock、国产 provider、Ollama、本地 GGUF | BYO 模型；路由可按能力/成本/离线选择 |
| 检索层 | tree-sitter 符号地图 + 符号感知代码索引（增量、BM25/向量/符号重排）+ 知识库 | 已升级；缺人工标注 recall 集，语义召回不能冒充完整理解 |
| 安全层 | 六档审批 + 四档沙箱 + 规则引擎 + 输出脱敏 + untrusted 围栏 | Linux 强隔离可用；Windows/macOS 按平台能力如实降级 |
| 观测层 | OTel trace/metric/log + 审计 + 成本台账 + 交付账本 | 产品级真实任务评测仍不足 |

### 1.3 当前明确短板

- **无人值守长任务仍是第一版**：`spark autopilot` 已能无人值守跑 build→repair→audit→done，但没有接入任务队列，也没有真实模型 + 外部仓库的 ≥20 回合基准；进程被 `kill -9` 后的自动恢复未验证。
- **自动化覆盖边界**：现有压力矩阵只覆盖运行器可枚举的失败模式（120 例），不等于 100% 真实编程场景；真实模型行为、超大仓库、并发工作区仍缺证据。
- **代码质量治理只完成第一轮**：新增代码已不引入 D/E，主链路索引与 Autopilot 已拆；全仓仍有 70 个历史 D/E/F 函数，未做到“全量逐行重构”。
- **代码向量库已升级但缺召回指标**：符号感知分块、增量索引、混合重排已落地；还没有人工标注的代码检索 recall@k 数据集。
- **OpenCode 兼容层只覆盖静态资产**：配置、命令、Agent、权限映射已兼容；插件运行时、provider 注册表、MCP 自动导入明确不支持。
- **发布链不完整**：Windows 未 Authenticode 签名，macOS 无 `.app/.dmg` 与公证；这需要证书/Apple 账号，不能用代码假装完成。
- **五社区上线未形成可复核清单**：当前只保证五个代码托管平台的代码与仓库元数据同步，不等于五个技术社区的内容发布完成。

## 2. 已交付功能清单

> 详细模块契约、owner 与测试面见 [`project/module-ledger.md`](project/module-ledger.md)。本表只列用户可感知的能力与证据，不把“有代码”当成“已交付”。

### 2.1 编程主链路

| 能力 | 状态 | 证据 |
|---|---|---|
| 多轮 Agent Loop（LLM→审批→执行→回灌） | ✅ | `src/spark/core/loop.py`、`core/graph.py`；`tests/test_loop*`、`test_agent_runtime.py` |
| 持久化 turn checkpoint / 审批恢复 | ✅ | `core/turn_store.py`、`core/checkpoints.py`；`tests/test_turn_recovery.py` |
| 文件读写、SEARCH-REPLACE、apply_patch、语法门禁 | ✅ | `tools/fs.py`、`edit/`；`tests/test_edit*`、`test_apply_patch*` |
| LSP 9 类代码智能（定义/引用/悬停/符号/诊断/rename/code action/影响测试） | ✅ | `src/spark/lsp/`；`tests/test_lsp_tools.py` |
| tree-sitter 仓库地图 + 符号感知语义代码索引（增量/混合重排） | ✅ | `repo_map.py`、`semantic_index.py`；`tests/test_p0_repo_map.py`、`test_semantic_index.py` 5 项 |
| 测试检测与失败回灌 | ✅ | `tools/gitops.py:run_tests`、`slash.py:handle_test`；相关工具/ slash 测试 |
| Git 任务即分支、绿测门控提交 | ✅ | `git_memory.py`、`tools/gitops.py`；`tests/test_git_memory*` |
| Goal 长程目标与证据门槛 | ✅ | `tools/goal.py`、`store` goals；Goal 相关测试 |
| Workflow YAML DAG、暂停/续跑/单节点重跑、worktree 隔离 | ✅ | `workflow.py`；`tests/test_workflow.py` |
| 空闲任务队列、日配额、成本上限、1–4 worker | ✅ | `task_queue.py`；队列相关测试 |
| 全自动 Autopilot 闭环 | ✅ 第一版 | `src/spark/autopilot.py`、`prompts/autopilot_*.md`、`tests/test_autopilot.py` 15 项、`test_cli_autopilot.py` 4 项、`test_autopilot_e2e.py` 真实 AgentLoop+工具+测试 1 项 |
| OpenCode 静态资产兼容 | ✅ | `src/spark/opencode_compat.py`；`tests/test_opencode_compat.py` 8 项；`docs/OPENCODE_COMPAT.md` |

### 2.2 工具与执行面

| 能力 | 状态 | 证据 |
|---|---|---|
| Shell/PTY/后台任务/进程树 | ✅ | `tools/shell.py`、`pty/`、`proc.py` |
| 内置浏览器 24 个 `browser_*`、五引擎、Profile、CDP、域名授权、敏感确认、trace | ✅ | `tools/browser.py`、`browser_policy.py`；`tests/test_browser.py` 20 项 |
| MCP 双向桥（stdio/sse/streamable_http） | ✅ | `tools/mcp_bridge.py`、`mcp_registry.py`、`mcp_expose.py` |
| 插件 manifest、签名、权限门、子进程隔离 | ✅ | `plugins.py`；`tests/test_plugins.py`、`test_plugin_trust.py` |
| Skills / Claude 兼容命令 / Hooks | ✅ | `skills.py`、`slash.py`、`hooks.py` |
| 声明式子 Agent 与并行任务 | ✅ | `agents.py`、`core/subagent_runner.py` |
| 审批、沙箱、规则、脱敏、untrusted 围栏 | ✅ | `policy.py`、`sandbox.py`、`sanitize.py`、`tools/untrusted.py` |

### 2.3 模型、检索与知识

| 能力 | 状态 | 证据 |
|---|---|---|
| 12 个 provider 预设 + 自定义 OpenAI 兼容端点 | ✅ | `providers/presets.py`、`factory.py` |
| 本地 GGUF 模型目录、下载、SHA256、一键启用 | ✅ | `local_models.py`、`providers/llama_cpp.py` |
| 模型路由（capability/cost/offline）+ 成本台账 | ✅ | `providers/routing.py`、`cost_accounting` |
| Prompt cache（Anthropic 稳定前缀） | ✅ | `providers/anthropic.py` |
| 项目知识库 BM25 + 可选向量混合检索 | ✅ | `knowledge.py`；`tests/test_knowledge.py` 36 项 |
| 长期记忆、自动提取、Web 管理 | ✅ | `memory/`；`tests/test_memory.py` |
| Repo Wiki（结构事实 + 行号引用校验） | ✅ | `wiki.py`；Wiki 测试 |

### 2.4 界面、协作与分发

| 能力 | 状态 | 证据 |
|---|---|---|
| Web 工作台、SSE、审批、会话、设置 | ✅ | `web/`、`web/src/` |
| 手机端独立壳、底部导航、两步审批、语音输入 | ✅ | `MobileShell.tsx`、`useIsMobile.ts`；`tests/test_ui_a11y.py`、`test_ui_browser.py` |
| 远程扫码、设备令牌、在线心跳、审计、Web Push | ✅ | `web/remote_access.py`、`remote.py`、`web_push.py` |
| 会话租约、设备冲突、限时 device_admin | ✅ | `web/state.py`、`web/lease.py`、`device_admin.py` |
| VS Code 原生聊天、内联 diff、诊断、VSIX | ✅ | `vscode-spark/` |
| Zed / JetBrains / Neovim ACP 接入 | ✅ | `ide.py`、`acp_server.py` |
| A2A Agent Card、message/send、SSE 流式 | ✅ | `a2a_server.py` |
| 单文件 HTML 分享、导入导出、归档 | ✅ | `web/_handlers_sessions.py` |
| 自动更新、scoop bucket、卸载命令 | ✅ | `updater.py`、`bucket/spark.json`、`cli.py uninstall` |
| Windows 签名 / macOS 公证 | ❌ 外部凭据 | `DS-01`/`DS-02` |

### 2.5 企业控制面与观测

| 能力 | 状态 | 证据 |
|---|---|---|
| 文档/会议/表格工作流、OCR/转写、导出 | ✅ | `control/`；`tests/test_control_*` |
| OTel trace/metric/log、审计脱敏、采样 | ✅ | `otel.py`、`audit.py` |
| 交付账本（diff/成本/隐私/测试归属） | ✅ | `workbench.py`、`store` deliveries |
| 评测集与真实模型矩阵 | 🟡 | `evals/`；真实任务覆盖仍不足 |

### 2.6 本轮验证记录

| 检查 | 结果 |
|---|---|
| `tests/test_autopilot.py` | 15 passed |
| `tests/test_cli_autopilot.py` | 4 passed |
| `tests/test_autopilot_e2e.py` | 1 passed（真实 `AgentLoop` + `write_file` + 真实 `pytest` + 独立 verifier） |
| `tests/test_opencode_compat.py` | 8 passed |
| `tests/test_semantic_index.py` | 5 passed |
| 声明代理/命令/任务回归 | 49 passed |
| Autopilot 压力矩阵 | 120/120 passed，3.689s |
| 真实本地模型验收 | MiniCPM5-2B + 真实仓库 + 真实 pytest：首轮 4 轮/11 次工具调用 passed；最终代码干净仓库重跑 4 轮/8 次工具调用 passed（见 `reviews/2026-11-24-autopilot-local-model.md`） |
| `ruff check src tests scripts` | 通过 |
| `mypy src/spark --ignore-missing-imports` | 189 files，0 error |
| `npm run build`（`web/`） | 通过；i18n 1108 keys、1052 calls |
| 全量 pytest | 1998 项：1982 passed、16 skipped、0 failed，978.48s（2026-11-24 最终执行） |

未验证：真实模型 + **外部仓库 ≥20 回合**；`kill -9` 中断恢复；并发工作区；五社区上线。

## 3. 待开发任务池

> 状态：`🚧 进行中`、`📋 已排期`、`⏸ 需决策/外部条件`、`✅ 已完成`。
> 完成标准必须是可复验的行为，不接受“有入口”“有文案”就算完成。

### 3.1 P0：本轮必须闭环

| ID | 任务 | 现状 | 完成标准 |
|---|---|---|---|
| BASE-01 | 四模块项目基线文档 | ✅ 本文 | 四模块齐全；已接入 docs 导航；远程同提交见本轮推送 |
| AUT-01 | 全自动编程运行器 | ✅ 第一版 | `spark autopilot` 从初始需求启动；build→repair→audit→done；状态落盘；超时/无进展/预算/成本可停；`--resume` 可恢复 |
| AUT-02 | 自动化可靠性门禁 | ✅ 第一版 | 120 例合成矩阵 120/120；覆盖首轮成功、一次/两次修复、无进展、最大轮次、模型错误、预算、超时、无变更、错误恢复 |
| AUT-03 | 工程化提示词体系 | ✅ 第一版 | 契约 + build/repair/verify 四份提示词；场景矩阵含新功能/Bug/重构/测试/性能/安全/文档配置/环境依赖 |
| CODE-01 | 全仓合规审计 | 🟡 第一轮 | ruff/mypy 通过；vulture 6 条已逐项判定为协议签名；radon 70 个历史 D/E/F 已列清单；新增代码无 D/E |
| IDX-01 | 代码向量知识库升级 | ✅ 第一版 | 符号感知分块、增量索引、BM25+向量+符号加权重排；索引版本 2；无人工标注 recall 集 |
| DOC-03 | 行业调研与三项论证 | ✅ | `docs/INDUSTRY_RESEARCH_2026-11-24.md`：两赛道、竞品矩阵、三项基准、公开数据与边界 |
| OSS-01 | OpenCode 生态兼容层 | ✅ 第一版 | `opencode.json(c)`、`.opencode/agents`、`.opencode/commands`、permission/tools denylist 映射；`spark opencode` 只读查看 |

### 3.2 P1：下一阶段

| ID | 任务 | 现状 | 完成标准 |
|---|---|---|---|
| AUT-04 | 队列接入 Autopilot | 📋 | 无人值守任务可由队列调度；失败重试、日配额、成本上限生效 |
| AUT-05 | 真实仓库长任务基准 | 📋 | ≥3 个外部仓库、≥20 回合任务；记录 pass@1、人工介入、恢复成功率、成本 |
| IDX-02 | 跨语言符号图与调用链 | 📋 | Python/TS/Go/Rust 至少四语言；跨文件影响面可读 |
| CODE-02 | 重复代码与复杂度门禁 | 📋 | CI 固定跑 ruff/mypy/vulture/radon；新增 D/E 级函数必须解释或拆分 |
| REL-01 | 发布门禁与五社区清单 | 📋 | Release 资产、README 截图、五个社区上线记录逐项可查 |
| UX-01 | 全链路过渡与异常提示 | 📋 | Web/手机关键路径有加载、空态、错误恢复；截图与交互测试 |
| DS-01 | Windows Authenticode | ⏸ 需证书 | `signtool verify /pa` 通过 |
| DS-02 | macOS `.app/.dmg` 公证 | ⏸ 需 Apple 账号 | `spctl --assess` 通过 |

### 3.3 P2：需要产品决策或高成本

| ID | 任务 | 现状 | 阻塞点 |
|---|---|---|---|
| AQ-04 | 专家团队编排（Lead + 专家） | ⏸ | 交互形态、成本模型、是否产品化 |
| EX-01 | 公共插件市场与撤销治理 | ⏸ | 签名信任链、审核规则 |
| RC-02 | 官方托管 relay / 账号入口 | ⏸ | 公网域名、TLS、托管成本、隐私边界 |
| DS-03 | Windows AppContainer 强隔离 | ⏸ | 工程量大；当前如实标注降级 |
| GROW-01 | 五技术社区内容上线 | ⏸ 需账号与发布确认 | 五个社区名单、账号登录态、内容口径、审核与发布节奏；当前只完成代码托管五平台同步 |

## 4. 迭代规划路线图

### 阶段 A：本轮收口（2026-11-24）

目标：把“能演示”变成“能无人值守跑完一个真实编程任务”。

1. 基线文档与远程 main 对齐；
2. 完成行业调研三项论证，锚定 AI 原生编程平台定位；
3. 落地 `spark autopilot`：需求→计划→编码→测试→修复→复验→交付；
4. 补可靠性场景矩阵与压力测试；
5. 全仓质量审计，修复死代码与明确缺陷；
6. 代码向量库符号感知升级；
7. 全量回归、静态检查、前端构建；
8. 五平台代码/元数据同步，README 补演示截图。

阶段门：
- Autopilot 场景矩阵全部通过（120/120，已达成）；
- 全量 pytest 无新增失败（见 §2.6 最终记录）；
- ruff/mypy 通过（已达成）；
- 五平台 `main` 同一提交（本轮推送后核验）；
- 远程仓库描述与标签仍为 AI 编程应用口径（本轮推送后核验）。

### 阶段 B：可靠性加固（下一阶段）

1. Autopilot 接入队列，支持暂停/恢复/重试/预算；
2. 真实仓库 20+ 回合长任务基准；
3. 无进展检测升级为调用指纹 + diff 指纹 + 测试指纹三路；
4. 进程崩溃后的自动恢复与心跳；
5. 代码索引跨语言符号图。

阶段门：
- 3 个外部仓库完成端到端任务；
- 人工介入次数、恢复成功率、单任务成本有数据；
- 进程被杀后可恢复，不重复已完成步骤。

### 阶段 C：产品化与生态

1. OpenCode 配置/命令/Agent 兼容层扩到可日常使用；
2. 五社区上线与内容运营；
3. 插件市场治理；
4. Windows/macOS 签名与公证；
5. 前端全链路动效、引导、异常恢复；
6. 企业控制面与编程主链路解耦，避免办公能力反客为主。

阶段门：
- 新用户按 README 可在 10 分钟内跑通；
- 五个社区上线记录可查；
- 安装包在目标平台无阻断警告，或有明确签名证据。

### 阶段 D：规模化

1. 多用户/团队权限与会话协作；
2. 分布式任务队列与远程执行；
3. 真实评测看板与回归门禁；
4. 模型路由质量与成本优化；
5. 专家团队编排产品化。

阶段门：
- 多租户隔离、审计、配额、SLA 有可复验证据；
- 不以“本地优先”或“通用办公”作为增长叙事。

## 5. 维护规则

1. **一个事实只写一次**：能力状态以本文 + `module-ledger` + `REMAINING` 为准；其他文档引用，不复制第二份进度表。
2. **关闭任务必须带证据**：代码路径、测试名、命令输出、远程链接至少有一项。
3. **远程优先**：文档改动与代码同提交推送；不接受只在本地改完就说“已对齐”。
4. **不把计划写成已交付**：路线图里的条目默认未完成，除非在“已交付功能清单”有证据。
5. **办公能力降级表述**：企业控制面可以写“提供文档/会议工作流”，不能写“Spark 是办公工作台”。
6. **外部依赖如实标注**：签名、公证、公共市场、官方 relay 等需要账号/证书/治理的条目，不伪装成代码问题。
