# 外部开源生态与 MCP 接入地图

> 2026-11-23。原则：成熟生态直接接，不自研重复能力；先接核心模块，不接冷门玩具。当前内置覆盖：9 个 Skills、9 个斜杠命令、7 个内置 MCP 精选（Playwright / Chrome DevTools / Git / Sequential Thinking / Memory / Repomix / MarkItDown）、1 个插件（repo-health）。首批 4 个已接入，但离“核心模块全覆盖”仍有距离。

## 接入分层

| 层级 | 做法 | 说明 |
|---|---|---|
| **A. 直接作为 MCP server 接入** | 写进内置精选目录，用户一键加入；工具走 `mcp__<server>__<tool>` 命名空间与正常审批 | 最优先。协议已经支持 stdio / sse / streamable_http。 |
| **B. 作为本地工具/库复用** | 引入 Python/Node 包或 CLI，包装成原生工具 | 需要许可证与体积评估；不让第三方进程绕过审批。 |
| **C. 作为参考实现** | 读源码/协议，自己实现薄适配层 | 仅当协议不兼容或许可证不适合时使用。 |
| **D. 不接** | 与现有能力重复、维护停滞、许可证污染、体积过大 | 明确写“不接”，避免以后反复讨论。 |

## 第一优先级：直接接的 10 个

| 资源 | 类型 | 解决的核心模块 | 接入方式 | 备注 |
|---|---|---|---|---|
| [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) | 浏览器 MCP | A 浏览器能力层 | MCP 精选 + 扩展桥 | 支持无障碍树快照、Chrome/Edge 扩展连接已登录标签页、持久化 profile；可作为“全浏览器”第一块拼图。 |
| [ChromeDevTools/chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) | 浏览器 MCP | A 浏览器能力层 | MCP 精选 | 强在 DevTools：网络、控制台、性能 trace、CSS 检查；官方支持 Chrome/Chromium，其他 Chromium 浏览器不保证。 |
| [oraios/serena](https://github.com/oraios/serena) | 代码智能 MCP | C 代码智能 | MCP 精选 / 可选内置 | 符号级查找、引用、类型层级、编辑与重构；和 Spark 自有 LSP 重叠，定位为“深度代码智能增强”，不替换 LSP 兜底。 |
| [yamadashy/repomix](https://github.com/yamadashy/repomix) | 仓库打包 MCP | C 代码智能 / 上下文 | MCP 精选 | 把仓库压成 AI 友好文件，Tree-sitter 压缩约 70% token；适合大仓库上手、跨仓库对比。 |
| [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | 官方 MCP 集合 | D 工具网关 | 已有 | 已有 Git / Memory / Sequential Thinking；可补 Filesystem、Fetch、Time 等，但避免与原生工具重复。 |
| [github/github-mcp-server](https://github.com/github/github-mcp-server) | GitHub MCP | D 工具网关 / 协作 | MCP 精选（远程/本地） | Issue、PR、Actions、代码搜索；和现有 `pr_*` 工具互补，先接只读工具。 |
| [microsoft/markitdown](https://github.com/microsoft/markitdown) | 文档转 Markdown | J 办公/知识 | 本地库/工具 | PDF、Word、PPT、Excel、图片转 Markdown；办公工作台的通用入口。 |
| [docling-project/docling](https://github.com/docling-project/docling) | 文档版面解析 | J 办公/知识 | 本地库/工具 | PDF 版面、表格、阅读顺序；比纯文本抽取更适合合同/论文/报表。 |
| [semgrep/semgrep](https://github.com/semgrep/semgrep) | 静态安全扫描 | I 安全 | 本地 CLI/MCP | 规则化扫描，适合接入安全审查 Skill 与 CI；注意 CE 与商业规则边界。 |
| [google/osv-scanner](https://github.com/google/osv-scanner) | 依赖漏洞扫描 | I 安全 | 本地 CLI | 锁文件漏洞扫描；与 repo-health、发布检查组合。 |

## 第二优先级：按核心模块补

### 浏览器与网页

| 资源 | 用途 | 判断 |
|---|---|---|
| [browser-use/browser-use](https://github.com/browser-use/browser-use) | 让 Agent 用视觉/结构操作浏览器 | 可作为“浏览器子代理”参考；不自研视觉点击，先接 MCP/库。 |
| [puppeteer/puppeteer](https://github.com/puppeteer/puppeteer) | Chrome/Chromium 自动化与 WebMCP | Chrome 专用，作为 DevTools 路线底层。 |
| [SeleniumHQ/selenium](https://github.com/SeleniumHQ/selenium) | WebDriver 多浏览器 | 覆盖 Firefox/Safari 的兜底适配层，不作为主交互层。 |
| [playwright](https://github.com/microsoft/playwright) | Chromium/Firefox/WebKit 三引擎 | 已有底层；补 engine 选择与 profile 管理即可，不换框架。 |

### 代码与仓库

| 资源 | 用途 | 判断 |
|---|---|---|
| [ast-grep/ast-grep](https://github.com/ast-grep/ast-grep) | 结构化搜索/替换 | 比正则稳，适合跨文件重构与规则检查；可 CLI + MCP。 |
| [sourcegraph/scip](https://github.com/sourcegraph/scip) | 代码索引协议 | 大型仓库符号图参考；暂不引入完整 Sourcegraph。 |
| [tree-sitter/tree-sitter](https://github.com/tree-sitter/tree-sitter) | 增量语法树 | 已有；继续作为符号/分块基础。 |
| [gitleaks/gitleaks](https://github.com/gitleaks/gitleaks) | 密钥扫描 | 已有 repo-health 简化版；可换更完整规则集。 |
| [trivy](https://github.com/aquasecurity/trivy) | 依赖/容器/IaC 扫描 | 发布检查与供应链安全；按需接。 |

### 协议与编排

| 资源 | 用途 | 判断 |
|---|---|---|
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | MCP client/server | 已有协议实现可参考；保持兼容，不另造协议。 |
| [jlowin/fastmcp](https://github.com/jlowin/fastmcp) | 快速写 MCP server | 插件作者友好；可作为插件模板依赖。 |
| [agentclientprotocol](https://github.com/agentclientprotocol/agent-client-protocol) | ACP 编辑器桥 | 已有 JetBrains/Neovim 接入；继续跟进规范。 |
| [a2aproject/A2A](https://github.com/a2aproject/A2A) | Agent 间协作 | 已有 A2A server；先做只读委派与任务状态。 |

### 界面与前端

| 资源 | 用途 | 判断 |
|---|---|---|
| [codemirror/codemirror6](https://github.com/codemirror/dev) | 轻量代码编辑器 | Web 工作台可替换 Monaco，减小体积。 |
| [microsoft/monaco-editor](https://github.com/microsoft/monaco-editor) | VS Code 同源编辑器 | 需要重 IDE 体验时用；注意体积。 |
| [xtermjs/xterm.js](https://github.com/xtermjs/xterm.js) | 终端渲染 | Web TUI/终端面板。 |
| [xyflow/xyflow](https://github.com/xyflow/xyflow) | 节点/工作流画布 | 已有执行图可复用；Team Canvas 后置。 |
| [tailwindlabs/tailwindcss](https://github.com/tailwindlabs/tailwindcss) | 样式 | 已有前端栈，不重复。 |

### 观测、评测与记忆

| 资源 | 用途 | 判断 |
|---|---|---|
| [open-telemetry/opentelemetry-python](https://github.com/open-telemetry/opentelemetry-python) | trace/metric/log | 已有 OTel；补 exporter 稳定性即可。 |
| [langfuse/langfuse](https://github.com/langfuse/langfuse) | LLM trace/评测 | 可选自托管；不要默认绑定云。 |
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | 长期记忆 | 与自有 memory 重叠；只参考抽取/去重策略，不替换本地存储。 |
| [confident-ai/deepeval](https://github.com/confident-ai/deepeval) | LLM 评测 | 用于回归集指标；不把自评当唯一证据。 |

## 不建议接

- **完整 Agent 框架替换**（LangGraph / CrewAI / AutoGen 整套）：Spark 已有 loop、审批、检查点、workflow；整套替换会破坏审计与成本链。只借鉴编排思想。
- **自研浏览器引擎 / 自研 LSP / 自研 OCR / 自研向量库**：成熟生态足够，重复造轮子没有收益。
- **重仓库打包/索引默认常驻**：Repomix、Serena 都按需启动，避免冷启动与内存常驻。
- **需要厂商账号才能工作的云 MCP**：默认不进内置精选；只能作为用户手动添加的远程选项。
- **许可证不清晰、长期停更的“Awesome 列表”项目**：只做参考，不进默认目录。

## 落地顺序

1. **内置 MCP 精选从 7 扩到 10**：已接 Playwright、Chrome DevTools、Repomix、MarkItDown；下一批补 Serena、GitHub、Semgrep/OSV、Fetch。先只读、后写入。
2. **浏览器扩展桥**：已交付 Playwright MCP `--extension` 一键预设与[安装/验收手册](BROWSER_EXTENSION_BRIDGE.md)，复用用户已登录的 Chrome/Edge 标签页；扩展桥默认只放行四个只读工具，写工具需显式加白名单；站点规则只作用于原生 `browser_*`，因为 Spark 看不到扩展当前标签页 URL。
3. **工具网关**：统一 MCP 健康检查、工具级 allowlist、OAuth/Token 注入、失败降级、审计事件。
4. **按需加载**：所有外部 server 默认不拉起，用户点“一键加入”后才启动；冷启动与内存占用写进设置页。
5. **许可证与供应链清单**：每个内置资源记录来源、许可证、维护状态、最后验证日期；升级前跑回归。

## 验收

- 新增 7 个内置精选后，设置页可见、可一键加入、可禁用；
- 每个 server 有健康检查与明确失败提示；
- 工具调用仍走审批与审计，不被 MCP 绕过；
- 冷启动 p95 不超过既有阈值；
- 至少 3 个真实任务证明“接了之后确实更好用”，而不是只多了几个工具名字。
