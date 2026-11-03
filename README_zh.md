> 🌐 [English](README.md) | [中文](README_zh.md)

# Spark — 一个大脑，七种形态，可审计的工程级护栏

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen.svg)](https://www.python.org/)
[![Node 18+](https://img.shields.io/badge/node-18%2B-339933.svg)](https://nodejs.org/)
[![CI](https://github.com/badhope/spark/actions/workflows/ci.yml/badge.svg)](https://github.com/badhope/spark/actions/workflows/ci.yml)
[![Security](https://github.com/badhope/spark/actions/workflows/security.yml/badge.svg)](https://github.com/badhope/spark/actions/workflows/security.yml)

一个把**审批、沙箱、回滚、供应商韧性做成工程级**而不是事后补丁的 AI 编程 Agent：
同一套大脑同时运行在 Web UI、桌面窗口、TUI、可脚本化 CLI、ACP（IDE）与 MCP Server
七种形态上。自带任意 OpenAI 兼容端点；无账号、无遥测、无厂商锁定。

```bash
# Linux / macOS —— 一行安装，SHA256 校验
curl -fsSL https://raw.githubusercontent.com/badhope/spark/main/scripts/install.sh | sh

# Windows（PowerShell）
irm https://raw.githubusercontent.com/badhope/spark/main/scripts/install.ps1 | iex

spark init          # 配置任意 OpenAI 兼容端点
spark web           # 打开界面（自动挑一个空闲端口）
```

**没有 API Key 时自动进入演示模式（mock）**，在设置里填入密钥后下一轮就走真实模型，
无需重启。

---

## 为什么选 Spark

| | |
|---|---|
| **六形态，一个大脑** | Web UI · 原生桌面窗口 · Textual TUI · 可脚本化 CLI · ACP（Zed 等编辑器）· MCP Server。同一套 Agent 循环、同一套工具、同一套权限——一处设策略，处处生效。 |
| **审批可审计** | 六档审批 × 四档沙箱；通配规则引擎在 user/project/local 三层作用域里 `deny` 永远优先；凭据路径保护、命令 token 级检查、可选 bwrap 隔离。 |
| **检查点而不是赌运气** | 每轮对工作区做 tar.gz 快照（含 manifest 校验）。文件与对话一起回滚，非 git 目录同样可用。 |
| **任务即分支** | `task_begin` / `task_step_commit` / `task_finish`：绿测门控逐次提交，测试不过自动废弃分支。不是演示，是一等公民工具。 |
| **AI 审 AI** | `spark review` 在强制只读 + 仅沙箱的回合里审查未提交 diff（或指定 commit 区间），按严重级输出结论，支持 `--json` 接入 CI。 |
| **供应商韧性内建** | 凭据池（失败自动轮换多个 Key）、回退模型链、小模型分工（压缩/摘要走 `small_model`）——一个 Key 耗尽或限流不会卡住整轮。 |
| **MCP 双向协议** | Spark 同时是 MCP *客户端*（stdio/SSE/HTTP、最小权限 allowlist、OAuth）与 MCP *服务端*（`spark mcp-serve` 把 36 个工具暴露给其他 Agent）。 |
| **`!cmd` 内联 shell** | 在任意聊天消息里直接跑命令——零 token、零审批噪音——结果以可折叠卡片呈现。 |
| **有治理的记忆** | AGENTS.md 层级（全局 `~/.agents` + 项目 + 嵌套）叠加向量长期记忆：自动抽取、去重合并（UPDATE/NOOP）、容量淘汰，全部可在 Web 界面管理。 |

## 界面预览

> 下列截图全部来自**真实模型 + 真实工作目录**的实时会话，不是 mock、不是设计稿。
> 复现命令：`python scripts/capture_screenshots.py`（核心界面）与
> `python scripts/capture_round7.py`（内联 shell / 分享 / 通知）；
> 30 秒演示片由 `python scripts/build_promo.py` + ffmpeg 重建。

[![观看 30 秒演示](assets/share/preview-frame.png)](assets/spark-demo.mp4)

**▶ [观看 30 秒演示（MP4，1.3 MB）](assets/spark-demo.mp4)** ·
[GIF 预览](assets/share/spark-demo.gif)

### 对话 —— 思考、工具调用、回答

模型读取 `greet.py`、完成推理并用一句话回答。`read_file` 卡片显示 **✓ 完成**——
真实工具往返，经 SSE 流式传输，头部有实时的上下文用量计量。

![对话界面](assets/screenshots/11-chat.png)

### 内联 shell —— 敲 `!` 直接跑

`!echo hello && ls` 在工作目录里执行，走与 `run_shell` 工具完全相同的策略检查与沙箱，
结果渲染成本地卡片。输出**不进模型上下文**，临时命令零 token 成本。

![内联 shell](assets/screenshots/20-inline-shell.png)

### 首屏 —— 接着上次继续

会话侧栏按时间分组、历史可搜索，模型/审批/沙箱状态始终可见。

![首屏](assets/screenshots/10-empty-state.png)

### 设置 —— 模型

任意 OpenAI 兼容端点（DeepSeek / 通义 / 智谱 / Kimi / 豆包 / Ollama / 自定义），
多模型档案一键切换，`测试连接` 全链路探活，推理强度三档映射到供应商原生参数。

![设置 · 模型](assets/screenshots/12-settings-model.png)

### 设置 —— 权限与沙箱

四档访问级别，每档都有明确的行为说明。受保护路径清单可自行编辑；
`.git/hooks`、`~/.spark` 与 Spark 自身源码始终受保护。

![设置 · 权限](assets/screenshots/13-settings-permissions.png)

### 命令面板 —— Ctrl+K

模糊跳转到所有界面：新会话、检查点、回合重放、文件查看、网页阅读、分享导出、主题、语言。

![命令面板](assets/screenshots/15-command-palette.png)

### 文件查看器 —— 语法高亮

浏览工作目录，带行号与 highlight.js 语法着色预览；二进制与超大文件优雅降级。

![文件查看器](assets/screenshots/16-file-viewer.png)

### 响应式 —— 桌面与手机

同一套界面在 390 px 下：侧栏收成抽屉、设置页签横向滚动、输入框始终可达。

![移动端设置](assets/screenshots/18-mobile-settings.png)

## 七种形态

| 形态 | 命令 | 适用场景 |
|------|------|----------|
| Web UI | `spark web` | 日常对话、文件查看、内置终端、执行图（自动选空闲端口） |
| 桌面窗口 | `spark desktop` | pywebview 原生窗口，固定绑定本机 |
| 终端 TUI | `spark` | 纯终端 / SSH 远程 |
| CLI | `spark exec "任务" --json` | 自动化、CI、管道（NDJSON 事件流） |
| IDE（ACP） | `spark acp` | 被 Zed 等 ACP 客户端拉起，见 [docs/ACP.md](docs/ACP.md) |
| MCP Server | `spark mcp-serve` | 把全部内置工具反向暴露给其他 MCP 客户端 |

## 特性一览

| 类别 | 功能 |
|------|------|
| 对话 | SSE 流式、Markdown 代码块、图片附件、思考流、长会话虚拟列表 |
| 工具 | 读写文件、`apply_edit`（tree-sitter 语法门禁）、grep/glob、Shell、后台任务、Jupyter、Web 检索、Git 全套、worktree |
| 规划 | 多步骤计划卡片、子 Agent 并行（最多 4 个）、声明式 `.claude/agents/*.md`、实时执行图 |
| 审批 | 六档模式（含 plan-only / approve-plan）、通配规则引擎、带 diff 预览的三键审批卡 |
| 权限 | 四档沙箱、受保护路径、凭据路径保护、命令 token 级检查、MCP 最小权限白名单 |
| 记忆 | AGENTS.md（全局 `~/.agents` + 项目 + 子目录）、向量长期记忆、自动抽取、Web 管理 |
| 知识库 | 导入文档，BM25 检索每轮以只读围栏注入 —— 不额外引入向量库 |
| 持久化 | 会话管理、检查点回滚（非 git 目录可用）、配置热加载、token 统计、会话全文检索 |
| 模型 | 任意 OpenAI 兼容端点、Ollama、多档案切换、`small_model` 廉价辅助调用、`--probe` 端点体检 |
| 扩展 | MCP 客户端（stdio/SSE/HTTP + OAuth）、MCP 注册表一键添加、技能 `.spark/skills/`、斜杠命令、插件、hooks |
| 界面 | 中英双语、深浅主题、命令面板、可改键、文件查看器、终端、网页阅读、执行图、桌面通知 |
| 工作流 | `spark review`（AI 审 diff）、`spark exec --json`、`!cmd` 内联 shell、单文件 HTML 会话分享、会话导入、`spark update` |
| 运维 | `doctor --fix` 自愈、`doctor --backup`（VACUUM INTO）、结构化审计流、CSP 安全头、更新提示 |

## 架构

![架构概览](docs/assets/architecture.svg)

LangGraph 1.2 是控制流的唯一真相源（`src/spark/core/graph.py`）；LiteLLM 统一供应商；
Web 端是 React 19 跑在带严格 CSP 的标准库 HTTP 服务上；其余每种形态都只是同一个
`AgentLoop` 的薄壳。

详细说明 → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## 参与开发

```bash
git clone https://github.com/badhope/spark
cd spark
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                    # 约 1000 个测试
ruff check src/spark tests scripts
mypy src/spark --ignore-missing-imports
lint-imports
```

前端在 `web/`（React 19 + Vite + Tailwind）；`npm run build` 把产物写进
`src/spark/web/dist/`，由 Python 包一并分发。

CI 在 Linux 与 Windows、Python 3.11 与 3.12 上跑完整门禁：ruff、mypy、import-linter
契约、bandit、gitleaks/trufflehog 密钥扫描、OpenAPI 快照新鲜度、覆盖率下限、
Windows 保留名检查、打包一致性检查、mock 模式 eval 基线。

## 平台要求

- Python 3.11+（或用 PyInstaller 打包版，无需 Python）
- Node.js 18+，仅前端开发需要；发行包已内嵌构建产物
- 任意 OpenAI 兼容 API（DeepSeek、OpenAI、Ollama、Azure、Kimi、智谱……）
- Windows / macOS / Linux

## 文档

| 文档 | 说明 |
|------|------|
| [docs/INSTALL.md](docs/INSTALL.md) | 安装、打包、桌面版构建、自动更新 |
| [docs/CONFIG.md](docs/CONFIG.md) | 配置参考（config.toml / 环境变量 / 上限） |
| [docs/API.md](docs/API.md) | HTTP API 与 SSE 事件协议 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构说明 |
| [docs/competitive-comparison.md](docs/competitive-comparison.md) | 与 Qoder / ZCode / opencode / Codex CLI 的功能对比 |
| [docs/reviews/2026-10-22-full-audit.md](docs/reviews/2026-10-22-full-audit.md) | 全维度审查：发现、修复、证据与诚实边界 |

## 社区

- 贡献指南：[CONTRIBUTING.md](CONTRIBUTING.md)
- 问题反馈：使用本仓库 **Issues** 页

---

**License:** MIT · **作者:** badhope · **Built with ❤️ in Shenzhen**
