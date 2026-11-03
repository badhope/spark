# 生态对标与全维度审查报告（2026-11-04）

> 审查对象：`spark-agent` 0.10.0 → 0.11.0（本轮提交）
> 审查方法：全部结论有实跑命令输出或 `文件:行号` 支撑；无法取证的一律标 `[未确认]`。
> 触发：全功能端到端验证 + 业界 harness 生态调研 + ≥10 个同类项目对标。

## 一、端到端验证（全部真实跑通，mock 模式）

| 面 | 验证项 | 结果 |
|---|---|---|
| CLI | `--version` / `version` / `init --no-probe` / `doctor --no-probe` / `config path` / `config show --json` / `uninstall --list` / `sessions` / `profile list` / `models --help` / `agent --help` / `test --help` / `review --help` / `context-profile --help` / `replay --help` / `resume --help` | 16/16 PASS |
| Web | 首页 200 / `/api/chat` 事件流 / `/api/debug` / `/api/sessions`（冷启动 ~8-15s，litellm+langgraph 惰性加载） | 4/4 PASS |
| MCP | `spark mcp-serve` JSON-RPC initialize 握手返回 serverInfo | PASS |
| A2A | Agent Card 发现 / message/send / tasks/get / 能力协商拒绝（9 条新测试） | 9/9 PASS |
| EXE | 本地 PyInstaller onefile（166MB）：`--version` ✓、web 起服务 200 ✓、mock 聊天事件流 ✓ | PASS |

## 二、协议层对标（2026-11 生态现状）

| 层 | 协议 | 状态 | 说明 |
|---|---|---|---|
| Agent → 工具/数据 | MCP | ✅ 双向 | client（stdio/SSE/HTTP+OAuth+采样）+ server（`spark mcp-serve`） |
| Editor → Agent | ACP | ✅ | Zed 等；版本协商 `1..PROTOCOL_VERSION`（`acp_server.py:507`） |
| Agent → Agent | **A2A** | ✅ 本轮新增 | `spark a2a-serve`：Agent Card + message/send + tasks/get；诚实边界：非流式（capabilities.streaming=false，客户端按规范降级） |
| Agent → UI | AG-UI | ❌ 明确不做 | Spark Web 自有 SSE 事件协议已覆盖同等语义；引入第二套前端协议只增加维护面，无用户价值 |
| 观测 | OTel GenAI | ✅ | `spark.turn`/`spark.tool` span + token metrics，OTLP 任意端点（Langfuse 原生兼容） |

结论：**四个有用户价值的边界协议全部就位**（MCP/ACP/A2A/OTLP），AG-UI 记为明确不做并写明理由。

## 三、参考项目对标（12 个，逐家学习点）

| 项目 | 学到什么 | Spark 状态 |
|---|---|---|
| opencode | scoop 安装、build/plan 双 agent | ✅ scoop 本轮；双 agent 属 ⏸（与 approval 语义重叠，需拍板） |
| codex CLI | OS 级沙箱、`app` 桌面、/status、resume、skills/subagents/plugins/MCP | 桌面✅ 诊断✅ resume✅ 全套✅；**Windows job object 沙箱列入待做** |
| Claude Code | hooks、skills、CLAUDE.md 层级、@mention 触发 | hooks✅ skills✅ AGENTS.md✅；GitHub 触发依赖 PR 工作流（待做） |
| aider | repo map、改后 lint/test 闭环 | repo_map✅ format_on_write✅ hooks✅；无 git 自动提交（task-as-branch 已覆盖主场景） |
| gemini-cli | 非交互 JSON 模式、CI 集成 | `exec --json`✅ `review --json`✅ |
| goose | 可嵌入 API（MCP）、多 provider | MCP server✅ LiteLLM✅ A2A✅ |
| cline | browser automation | ❌ 内置浏览器属高成本项（REMAINING 登记） |
| kilocode | BYOK | ✅ 自带密钥/凭据池/fallback 链 |
| Zed | ACP | ✅ |
| Warp | 多 agent 并行编排 | ✅ 4 路并行 task + 声明式子代理 |
| Muse Code | 1M context、审计日志 | 审计✅（SPARK_AUDIT_LOG）；context 上限可配（max_context_tokens le=10M） |
| copilot/continue | IDE 扩展 | ❌ VS Code 扩展（REMAINING 登记） |

## 四、批判性发现（一针见血）

| # | 发现 | 证据 | 处置 |
|---|---|---|---|
| F-A01 | ~~demo 回退在 EXE 形态下不一致~~ **[撤回]**：复核发现首次源码冒烟未设 `SPARK_HOME`，用了真实 `~/.spark/config.toml`（provider 已配置）；EXE 冒烟用全新临时 home（默认 `openai_compat` + 假 key）→ 真实请求 3 次重试失败属**预期行为**。demo 模式仅在**无** key 时启用（`web/state.py:461-482`）。行为两端一致，无缺陷 | 本轮复核 + `config.py:104-108` | 已撤回；文档补充：EXE/CI 冒烟应配 `--provider mock` |
| F-A02 | **web 冷启动 8-15s**（litellm+langgraph 导入）+ onefile 首启解压 ~10s：桌面用户感知是"点了没反应" | 本轮实测（6s 内 000，15s 内 200） | 接受（惰性导入已做）；建议后续加"启动中"状态页 `[未确认成本]` |
| F-A03 | **`spark sessions` 帮助与用法不一致**：命令无子参数，但发现方式像子命令 | `cli.py:785` | 小项，保持现状（help 已说明） |
| F-A04 | **REMAINING 原"清干净 venv"表述失实**：Spark 从不创建 venv，uninstall 清的是数据目录 | 全代码 grep 无 venv 管理 | ✅ 本轮已在 INSTALL/CLAUDE 语义中纠正 |
| F-A05 | **22 个 D 级复杂度函数**（上轮审计遗留）：集中在 loop.py/tui | radon（2026-10-22 报告） | 接受为已知债，重构收益低于回归风险 |
| F-A06 | **A2A 同 context 并发串行**：单 context 请求持锁串行，长 turn 会阻塞同上下文后续请求 | `a2a_server.py` 设计说明 | 诚实边界：A2A 编排器天然按 context 串行；真并行用不同 contextId |
| F-A07 | **CSP/安全头、SSRF 白名单、凭据路径保护**等既有防线未回退 | tests 全绿 | 持续保持 |

## 五、本轮净变化

- 新增：A2A server（`spark a2a-serve`，9 测试）、scoop bucket、`spark uninstall`、
  `agent.status_items`、compact.md 提示词覆盖、EXE/端到端验证记录
- 修复：trufflehog 误报、CI ubuntu INTERNALERROR（全局 os.name 补丁）、
  dist 与 lockfile 漂移、fetch_page 测试在 fake-IP DNS 环境的误伤
- 文档：CONFIG/INSTALL（中英）、REMAINING、竞品矩阵、CHANGELOG 全量同步
