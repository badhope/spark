# 新能力融会贯通审计（2026-11-23）

## 一句话结论

**主闭环已经接通：任务 → AgentLoop → turn 指标 → 交付账本 → 工作台 → 隐私回执。**
**原先的四处半接已收口：交互式改盘自动写账本、语义索引后台自动建、内联编辑有 Web UI + Neovim + CLI 桥、插件签名策略进了设置页。**
**仍有一个产品外缺口：JetBrains 没有原生 inline UI，走 `spark inline` External Tool/CLI 桥，而不是编辑器内联弹层。**

这不是“功能列表堆完就算完成”。下面按用户能看到的一条链路核对。

## 已经接通的链路

### 1. 任务执行 → 延迟证据 → 交付账本

- `AgentLoop` 每轮记录 `queue_ms / context_ms / llm_ms / ttft_ms / tool/rounds / usage`；
- 指标先落 SQLite `turn_metrics`，再由 `/api/metrics` 给工作台；
- 队列任务结束自动 `build_delivery()`，把 git 变更、工具事件、检查点、指标、
  测试证据、审批、成本、隐私回执聚合成一条 delivery；
- 工作台可以展开、导出 Markdown、通过/打回/回滚；
- 隐私回执明确区分 local / remote、端点、模型、读取文件、联网工具，
  `telemetry=local-only`，不假装“完全不出境”。

浏览器实测：入队任务完成后自动出现 review 账本，任务卡显示 `ledger`，
隐私回执显示“远程端点”，最近延迟可见。

### 2. 语义索引 → 工具 → 上下文 → UI

- `semantic_search` 工具首次调用可建索引，`refresh` 可重建；
- `AgentLoop._semantic_context()` 只在索引已存在时检索，不在热路径全仓建索引；
- 检索结果以 `<semantic_code_index>` untrusted 围栏注入，不直接当系统指令；
- 工作台显示 `files/chunks` 与重建按钮；索引存 `~/.spark/code_index`，不进用户仓库。

### 3. 编辑器内联编辑 → 后端 → diff 预览

- `/api/inline_edit` 接收 `edit` / `nes`，路径必须过 `sandbox.resolve()`；
- VS Code 扩展默认关闭 NES，开启后才注册 `InlineCompletionItemProvider`；
- 内联编辑先打开 `spark-inline-preview` 虚拟文档做 diff，用户点“应用”才
  `WorkspaceEdit.replace`；不是模型直接改盘。

### 4. 插件签名 → 扫描 → 执行门 → CLP/Web

- manifest 的 `publisher/key_id/signature` 由 Ed25519 校验；
- 执行层 `_signature_gate` 对 `invalid/revoked/untrusted` 永远拒绝；
  `require_signature=true` 时 `unsigned` 也拒绝；
- CLI 有 `verify/trust/revoke/keygen/sign`，`plugins list` 带签名状态；
- Web 插件卡片现在显示“签名有效 / 未签名 / 未受信 / 已撤销 / 签名无效”和发布者；
- Web 启用开关对 `invalid/revoked/untrusted` 直接 400，不再出现
  “界面显示已启用、执行层却拒绝”的矛盾。

### 5. 工具面 → 设置页 → 执行层

`agent.tool_preset` 现在同时接了三处：

- 配置模型与 TOML 序列化；
- `/api/settings` 的读取、校验、保存；
- 设置页“执行参数”下拉：`full` / `coding`；
- `AgentLoop` 构造时把预设应用到 `ToolRegistry`，显式 allowlist 不被覆盖。

## 四个断点的收口状态

### A. 交互式对话自动写交付账本（已收口）

队列任务自动 `build_delivery()`；交互式聊天在出现写文件 / 编辑 / git 提交时
也会自动写一条 `review` 账本，只读对话仍保留“生成本次账本”按钮。这样既不
把闲聊灌进账本，也不让“聊天里改完一个文件”丢失证据。**当前结论：改盘自动，
只读手动。**

### B. 语义索引后台自动建（已收口）

索引不存在时，AgentLoop 在后台启动一次构建，不阻塞当轮 TTFT；首轮不注入
语义上下文，次轮起可用。工作台“重建”和 `semantic_search` 仍保留为显式入口。
**当前结论：自动建、异步可用，热路径不建。**

### C. 内联编辑多载体（已收口，JetBrains 仍是桥接）

- VS Code：原生 `InlineCompletionItemProvider` + diff 预览 + 应用；
- Web：工作台“内联编辑”卡片，选中文本 + 指令 → 预览 → 显式应用；
  `/api/inline_edit/apply` 要求 `base_sha256` 与当前文件一致，过期预览返回 409；
- Neovim：`spark ide install neovim` 生成的模块带 `<leader>si`，选中区调用
  `spark inline --json`，确认后写回 buffer；
- JetBrains：没有原生 inline UI，用 `spark inline` 作为 External Tool/CLI 桥。
**当前结论：VS Code / Web / Neovim 三条原生腿 + JetBrains CLI 桥。**

### D. 插件签名策略进设置页（已收口）

插件页新增“强制插件签名”开关，写入 `[plugins].require_signature` 后立即
重建 loop；仅本机或 30 分钟 `device_admin` 可改。开启后未签名插件点击启用
会被 400 拒绝。**当前结论：配置、执行门、设置页三处一致。**

## 验证

- `pytest tests/test_settings_roundtrip.py tests/test_settings_api.py`
  → 13 passed。
- `pytest tests/test_plugin_trust.py tests/test_tool_preset.py` → 5 passed。
- `npx tsc --noEmit` 通过；`npm run build` 通过。
- 全量非浏览器 pytest 抓到一处真断点：后端 `TurnEventType` 已有 `metrics`，
  前端 `ChatEvent` 没声明。已补 `{ type: "metrics"; data: Record<string, unknown> }`。
- `qwen-flash` 30 任务矩阵：**30/30 PASS**，TTFT p50 4.15s、p95 7.82s，
  turn p50 35.8s、p95 113.8s；30 任务总 token 约 1.55M/20k，
  按公开价目估算约 **$0.049**（网关无账单，`cost_usd` 仍为 null）。
- 修正两处评测任务歧义：`retrieval-symbol` 初始文件名大小写矛盾、`test-async-fetch`
  “已有 1 个测试却要求再加 1 个”的表述；修正后两项 PASS。

## 判断

主链路已经通了，且有账本、指标、30 任务评测和全量回归作证。
四个审计断点里，三个产品面（账本、索引、插件策略）已闭环，内联编辑
覆盖 VS Code / Web / Neovim，JetBrains 用 CLI 桥接。
尚未做的是 JetBrains 原生 inline UI（需要 Kotlin 插件工程）；这不是 Spark
后端能力缺口，已明确标注，不用“全绿”掩盖。
