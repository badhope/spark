> 🌐 [English](README.md) | **中文**

# Spark

**本地优先的 AI 编程智能体与办公工作台，护栏可审计。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen.svg)](https://www.python.org/)
[![Node 18+](https://img.shields.io/badge/node-18%2B-339933.svg)](https://nodejs.org/)
[![CI](https://github.com/badhope/spark/actions/workflows/ci.yml/badge.svg)](https://github.com/badhope/spark/actions/workflows/ci.yml)
[![Security](https://github.com/badhope/spark/actions/workflows/security.yml/badge.svg)](https://github.com/badhope/spark/actions/workflows/security.yml)

一套 agent loop、八种形态、同一套权限模型。编程任务、办公流程与企业控制面
跑在同一个可审计内核上：写操作先审批、命令在沙箱中执行、每回合可回滚，
模型层兼容任意 OpenAI 兼容端点。

```bash
# Linux / macOS —— 一行安装，SHA256 校验
curl -fsSL https://raw.githubusercontent.com/badhope/spark/main/scripts/install.sh | sh

# Windows（PowerShell）
irm https://raw.githubusercontent.com/badhope/spark/main/scripts/install.ps1 | iex

spark init    # 配置任意 OpenAI 兼容端点
spark web     # 打开浏览器界面（自动选择空闲端口）
```

没有 API Key 时进入**演示模式**（脚本化 mock 模型）；在设置中填入密钥后，
下一轮自动切换真实模型，无需重启。

<p align="center">
  <img src="assets/screenshots/11-chat.png" alt="对话：推理过程、工具调用与实时上下文用量" width="100%">
</p>

## 为什么是 Spark

- **执行可审计。** 六档审批 × 四档沙箱；通配规则覆盖 user/project/local 三个
  作用域，`deny` 始终优先；凭据路径受保护，shell 命令做 token 级检查。
- **失败可回滚。** 每回合快照工作区（tar.gz + manifest 校验），文件与对话可一起
  回滚，非 git 目录同样有效。
- **任务即分支。** `task_begin` → `task_step_commit` → `task_finish`；绿测才允许
  提交，失败自动废弃分支。
- **企业控制面。** `/api/v1` 提供租户、项目、任务、成果物与审计；本地用 SQLite，
  生产可切 PostgreSQL + Keycloak OIDC + Casbin。
- **能产出真实文件的办公工作台。** `/office` 把会议转写和文档变成带来源行号的
  纪要、行动项、报告与表格，支持导出 Markdown、Word、Excel、PowerPoint、PDF；
  可导入 docx/xlsx/pptx/pdf、邮件、图片与扫描 PDF（本地 OCR）；配置模型后可选用
  AI 摘要与结论章节。
- **模型自由。** 任意 OpenAI 兼容端点、fallback 模型链、凭据池，以及独立的小模型
  承担辅助调用。

## 快速上手

```bash
spark                         # 终端 TUI
spark web                     # 浏览器界面，绑定 127.0.0.1
spark desktop                 # 原生窗口，仅回环
spark enterprise              # 企业 /api/v1 控制面
spark exec "任务" --json       # NDJSON 事件流，适合脚本与 CI
spark review                  # 审查未提交的 diff
spark wiki --refresh          # 重新生成本地代码文档
spark doctor                  # 体检配置、凭据与运行时
```

办公工作台由企业控制面提供：

```bash
spark enterprise --host 127.0.0.1 --port 18800
# http://127.0.0.1:18800/office
```

`spark exec` 每行一个 JSON 事件，最后一行是 `{"type":"final","text":"…"}`，
可直接接 `jq`。

## 八种形态

| 形态 | 命令 | 场景 |
|---|---|---|
| Web UI | `spark web` | 对话、文件查看、终端、执行图 |
| 企业控制面 | `spark enterprise` | `/api/v1` 控制面与 `/office` |
| 桌面 | `spark desktop` | 原生 pywebview 窗口，仅回环 |
| TUI | `spark` | 纯终端 / SSH |
| CLI | `spark exec "任务" --json` | 自动化、CI、管道 |
| IDE（ACP） | `spark acp` | Zed 等 ACP 客户端 —— [docs/ACP.md](docs/ACP.md) |
| MCP Server | `spark mcp-serve` | 把内置工具暴露给其它 MCP 客户端 |
| A2A Server | `spark a2a-serve` | Agent 间委派 —— [docs/A2A.md](docs/A2A.md) |

## 三层护栏

1. **审批** —— 从 `plan-only`（只出计划、不改任何东西）到 `full-auto`。
   默认 `suggest`：写文件与执行命令都要先确认。
2. **沙箱** —— `sandbox-only` / `workspace`（默认）/ `full-access` /
   `unrestricted`。越界路径与受保护路径一律拒绝。
3. **规则** —— `[[permission_rules]]` 按“工具 + 资源”匹配，三个作用域内
   `deny` 优先；克隆下来的仓库只能收紧权限，无法给自己放行。

OS 级隔离叠在上一层：Linux 用 bwrap，Windows 用 Job Object（进程树
kill-on-close + 内存上限），macOS 用 `sandbox-exec`。平台做不到的能力会如实
写进工具结果，而不是假装有。

## 本地验证

```bash
pytest -q                                  # 1493 passed, 3 skipped
ruff check src tests scripts
mypy src/spark --ignore-missing-imports
bandit -r src/spark -ll -ii -c bandit.yaml
lint-imports                               # 架构契约
```

CI 另外跑密钥扫描、OpenAPI 快照新鲜度、覆盖率下限、Windows 保留路径检查、
打包检查，以及 Linux/Windows 上的 mock 模式 agent 评测。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | 安装、打包、桌面构建、自动更新、卸载 |
| [docs/CONFIG.md](docs/CONFIG.md) | 全部配置项与环境变量 |
| [docs/API.md](docs/API.md) | HTTP API 与 SSE 事件协议 |
| [docs/ENTERPRISE.md](docs/ENTERPRISE.md) | `/api/v1`、`/office`、PostgreSQL、Keycloak、Casbin、Alembic |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 分层与模块边界 |
| [docs/FEATURES.md](docs/FEATURES.md) | 逐项功能与代码落点 |
| [docs/A2A.md](docs/A2A.md) | A2A Agent Card、流式与任务生命周期 |
| [docs/competitive-comparison.md](docs/competitive-comparison.md) | 与 Qoder / ZCode / opencode / Codex CLI 的对比 |
| [docs/reviews/](docs/reviews/) | 审计报告：问题、修复与诚实的边界 |
| [vscode-spark/](vscode-spark/) | VS Code 扩展 |

## 平台要求

- Python 3.11+，或直接使用打包好的可执行文件（无需 Python）
- Node.js 18+ 仅用于前端开发（发行版自带编译产物）
- Windows / macOS / Linux

---

**License:** MIT · **Author:** badhope
