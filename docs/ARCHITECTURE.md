# Spark Agent — 架构文档

本文档描述 Spark 的整体架构与核心流程。

## 概览

Spark 是一个**本地自托管的 AI 编程 Agent**，采用 Codex 式 Agent Loop 设计，支持多种 LLM Provider，内置丰富工具集，具备长会话记忆与检查点回滚能力。

```
用户输入 → AgentLoop（回合引擎）→ 模型推理 → 工具执行 → 结果反馈 → 下一轮
                ↑                                         ↓
        上下文压缩 ← 记忆检索 ← AGENTS.md ←─── SQLite 持久化
                ↓
         SSE 流式渲染 → Web UI
```

## 核心组件

### 1. AgentLoop (`src/spark/core/loop.py`)

AgentLoop 是核心引擎，负责管理整个对话回合：

```
单次 turn 流程：
  用户输入
    │
    ▼
  [上下文检查] ──≥85%──→ 触发 5 段式压缩（Objective / Important Details / Work State / Next Move / Relevant Files）
    │
    ▼
  [记忆检索] → 补充到 messages
    │
    ▼
  [构建 prompt] = system + AGENTS.md + history + memory
    │
    ▼
  [调用 Provider] → 流式接收 token
    │
    ├─ 有 tool_call ──→ 执行工具 → 收集结果 → 回到模型推理
    │                       │
    │                       ▼
    │                  sandbox 检查（是否允许此操作）
    │                       │
    │                       ▼
    │                  审批策略（suggest/full-auto）
    │
    └─ 无 tool_call ──→ 输出最终答案
                              │
                              ▼
                         落库（history + store）
                              │
                              ▼
                     自动检查点（write/shell 类回合）
                              │
                              ▼
                         turn_end → 推送前端
```

### 2. 上下文引擎 (`src/spark/core/context.py`)

管理 Agent 的"视野"：

| 模块 | 作用 |
|------|------|
| AGENTS.md | 注入项目级说明，定义工作流与约定 |
| Token 估算 | tiktoken（模型已知时用 `encoding_for_model`，否则 `o200k_base`）；不可用时回退启发式 |
| 自动压缩 | 85% 阈值触发，保留最近 8 条消息 + 分段摘要 |
| 记忆检索 | 关键词匹配 → LLM 抽取 → 语义检索（降级关键词） |

### 3. Provider 层 (`src/spark/providers/`)

统一抽象，支持多种 LLM 后端：

```python
class BaseProvider(ABC):
    @abstractmethod
    async def stream(self, messages, schemas) -> AsyncIterator[Delta]: ...

class OpenAICompatProvider(BaseProvider):     # OpenAI 兼容接口
class OllamaProvider(BaseProvider):           # 本地 Ollama 服务
class MockProvider(BaseProvider):             # 测试用 mock
```

### 4. 工具注册表 (`src/spark/tools/registry.py`)

所有工具通过 schema 注册，统一审批与沙箱检查：

```
ToolRegistry
├── 文件工具：read_file, write_file, apply_patch
├── 搜索工具：grep, glob, list_dir
├── 执行工具：run_shell, bg_start, bg_output, bg_kill（跨重启持久化）
├── Git 工具：git_status, git_diff, git_log, git_branch, git_add, git_commit
├── 数据工具：read_notebook, notebook_edit
├── 联网工具：web_search, web_fetch
├── 规划工具：update_plan
├── 扩展工具：task（子 Agent 委派，最多 4 个并行）
└── MCP 工具：McpBridge 桥接外部协议
```

### 5. 沙箱系统 (`src/spark/sandbox.py`)

四档权限，逐级放行：

```
sandbox-only:      list_dir / read_file / grep / glob
    ↓
workspace:         + write_file / apply_patch / shell
    ↓
full-access:       + background / MCP / readonly tools
    ↓
unrestricted:      全部放开（仅用户自定义保护路径）
```

**安全防护：**
- Shell 命令白名单扫描
- 路径穿越检测（绝对路径 → workdir 校验）
- 保护路径清单（config.toml、sessions.db 等）
- 文件写入前 `check_write_path()` 校验

### 6. 持久化 (`src/spark/store.py`)

SQLite 存储：

| 表 | 内容 |
|----|------|
| `sessions` | 会话元信息（id, title, model, updated_at） |
| `messages` | 对话消息（role, content, timestamp） |
| `checkpoints` | 检查点记录（id, session_id, label, created_at） |
| `tool_events` | 工具调用审计（name, args, duration, ok） |
| `bg_jobs` | 后台任务跨重启状态 |

### 7. 记忆系统 (`src/spark/memory/`)

```
每轮结束
    │
    ▼
自动抽取（LLM 调用）
    │
    ▼
入库（SQLite + 关键词索引）
    │
    ▼
检索时：top_k 最近 + 语义相关性
```

**降级策略**：嵌入模型不可用时，自动降级为关键词匹配。

### 8. 检查点 (`src/spark/core/checkpoints.py`)

每次关键操作后自动快照：

```
触发条件：write_file / apply_patch / run_shell / notebook_edit
    │
    ▼
tar.gz 快照（排除 .git / node_modules / __pycache__ 等）
    │
    ▼
存储路径：~/.spark/checkpoints/<session_id>/<id>.tar.gz
    │
    ▼
回滚时：还原文件 + 截断对话历史 + 重载历史记录
```

## 前端架构

```
React 19 + Vite + Tailwind 4
    │
    ├── App.tsx            # 主布局与状态管理
    │   ├── SessionSidebar # 会话列表（CRUD + 关键词）
    │   ├── ChatMessage    # 消息渲染（markdown + 工具块）
    │   ├── InputBox       # 输入框（图片附件 + 快捷按钮）
    │   ├── SettingsPanel  # 设置面板（多 Tab）
    │   └── Toast          # 通知系统
    │
    ├── api.ts             # API 客户端（fetch + SSE）
    ├── types.ts           # TypeScript 类型定义
    └── components/
        ├── Markdown.tsx   # mdx 渲染（rehype + highlight.js）
        ├── ThinkingBlock  # 推理过程展示
        ├── PlanCard       # 任务计划卡片
        └── ContextMeter   # 上下文水位仪表
```

**通信协议：**
- 实时消息：SSE（Server-Sent Events）
- CRUD 操作：REST JSON
- 事件类型：`text_delta` / `reasoning_delta` / `tool_start` / `tool_end` / `plan` / `approval_needed` / `compaction` / `context` / `turn_end` / `turn_error` / `done`

## CLI 架构

```
spark                          # 默认启动 TUI
spark exec "prompt"            # 无头执行
spark web                      # 启动 Web UI
spark sessions                 # 列出会话
spark resume <id>              # 恢复会话
spark test --model xxx         # 连通性测试
```

## MCP 集成

通过 `McpBridge` 桥接外部 MCP 服务器：

```toml
[[mcp_servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
readonly_tools = ["ReadFile", "ReadDir", "Glob"]
```

安全策略：
- MCP 工具默认走审批门
- 可通过 `readonly_tools` 白名单声明只读（免审批）
- 服务端工具集变化时通过 schema revision 检测并剔除失效调用
- 配置在 `[[mcp_servers]]` 段中（stdio / sse / streamable_http 三种传输）

## 文件结构

```
spark/
├── src/spark/
│   ├── __init__.py
│   ├── cli.py               # CLI 入口（typer）
│   ├── config.py            # 配置加载（pydantic + toml）
│   ├── core/
│   │   ├── loop.py          # AgentLoop 外围职责（历史/存储/记忆/压缩/计账/hooks/子代理）
│   │   ├── graph.py         # LangGraph 状态机——ReAct 控制流唯一真相源（llm→tools→finalize）
│   │   ├── context.py       # 上下文引擎
│   │   ├── compact.py       # 对话压缩（LlamaIndex + 结构化摘要）
│   │   ├── tokens.py        # Token 计数（tiktoken；启发式兜底）
│   │   └── checkpoints.py   # 检查点管理
│   ├── providers/
│   │   ├── base.py          # Provider 抽象基类
│   │   ├── factory.py       # 工厂函数
│   │   ├── openai_compat.py # LiteLLM 统一实现（openai_compat + ollama 两个类）
│   │   ├── mock.py          # Mock 实现
│   │   └── probe.py         # 连通性探测
│   ├── tools/
│   │   ├── registry.py      # 工具注册表
│   │   ├── fs.py            # 文件系统工具
│   │   ├── shell.py         # Shell 执行
│   │   ├── search.py        # grep/glob
│   │   ├── web.py           # 联网检索
│   │   ├── notebook.py      # Jupyter 操作
│   │   ├── bg.py            # 后台任务
│   │   └── mcp_bridge.py    # MCP 桥接
│   ├── memory/
│   │   ├── service.py       # 记忆服务
│   │   ├── extractor.py     # LLM 抽取
│   │   ├── llm.py           # 记忆 LLM 调用
│   │   └── store.py         # 记忆存储
│   ├── sandbox.py           # 沙箱策略
│   ├── policy.py            # 工具权限策略
│   ├── store.py             # SQLite 持久化
│   ├── control/             # 企业控制面（FastAPI /api/v1、SQLAlchemy、Casbin）
│   ├── tui/                 # 终端 UI（textual）
│   └── web/                 # Web 服务器（stdlib http.server）
├── tests/
│   ├── test_sandbox.py      # 沙箱测试
│   ├── test_policy.py       # 策略测试
│   ├── test_loop.py         # 循环逻辑测试
│   ├── test_store.py        # 持久化测试
│   └── test_p1_capabilities.py  # P1 功能 E2E
├── web/
│   ├── src/                 # React 源码
│   ├── public/              # 静态资源
│   └── package.json
├── brand/                   # 品牌资产
│   ├── favicon.svg
│   ├── logo.svg
│   ├── logo-dark.svg
│   └── hero.svg
├── docs/                    # 文档
│   ├── ARCHITECTURE.md
│   ├── API.md               # HTTP API 参考
│   ├── CONFIG.md            # 配置参考
│   ├── INSTALL.md           # 安装与打包
│   └── assets/
│       ├── architecture.svg # 整体架构图
│       └── turn-flow.svg    # 回合流程图
├── README.md
├── CONTRIBUTING.md
├── LICENSE
├── pyproject.toml
└── .env.example
```

## 扩展指南

### 添加新工具

1. 在 `src/spark/tools/` 下创建实现
2. 在 `registry.py` 的 `schemas()` 方法中注册 `_schema(name, description, properties, required)`
3. 在 `policy.py` 中归类（READONLY_TOOLS 或 SHELL_TOOLS）
4. 在 `tools/__init__.py` 中导出执行函数

### 添加新 Provider

1. 继承 `BaseProvider`
2. 实现 `stream()` 异步生成器
3. 在 `providers/factory.py` 的 `create_provider()` 中添加分支
4. 在 `config.py` 的 `ProviderName` Literal 中添加类型

### 声明式子代理（.claude/agents/，P1-4）

「文件即 subagent」：在仓库 `.claude/agents/*.md`（项目级）或
`~/.claude/agents/*.md`（用户级）放置一个 Markdown 文件，即声明一个可委派的专家子代理。
加载器为 `src/spark/agents.py`（frontmatter 语法与 `skills.py` 对齐，零 YAML 依赖）：

```markdown
---
name: reader                  # 必填，spawn 句柄
description: 只读代码探索      # 一行摘要，注入 <available_subagents>
when_to_use: 探索陌生代码时    # 可选，委派时机提示
tools: read_file, grep        # 可选；缺省 = 全部工具
---

这里是子代理的 system prompt：角色、规则、输出格式。
```

行为约定：

- **发现**：`discover_declared_agents(workdir)` 扫描两个目录，项目级同名文件优先；
  非法文件（缺 frontmatter / 缺 name / 空 body）在发现时跳过并告警，按名 spawn 时严格拒绝并报错。
- **注入**：系统 prompt 追加 `<available_subagents>` 列表（name/description/when_to_use）。
- **spawn**：`task` 工具传 `{"agent": "reader", "prompt": "..."}`；并行模式传
  `tasks: [{"agent": "reader", "prompt": "..."}, ...]`（最多 4 个）。子代理 body 经
  `subagent_system_prompt()` 包裹后写入**子回路自己的 system 槽**（`AgentLoop._extra_system_prompt`），
  `tools` 字段经 `ToolRegistry.tool_allowlist` 收敛其工具 schema。
- **复用**：不新建执行器——声明式子代理走的就是既有并行 task 执行器
  `AgentLoop.spawn_subtask` / `spawn_subtasks_parallel`（`src/spark/core/loop.py`），
  仅多传 `system_extra` / `tools_allowlist` 两个可选参数。

仓库内置示例：`.claude/agents/reader.md`（只读代码探索）、`.claude/agents/tester.md`（跑测试汇总结论）。

## 设计原则

- **本地优先**：所有数据存储本地，不依赖云服务
- **可扩展**：Provider、工具、MCP 均可插件化
- **安全**：沙箱四档、审批流、路径保护
- **可观测**：Token 计数、上下文水位、事件流可视化
- **容错**：记忆降级、模型探测、异常兜底
