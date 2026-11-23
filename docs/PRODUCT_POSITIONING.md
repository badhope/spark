# Spark 产品定位（现行口径）

> 2026-11-24 校正。本文是产品定位的唯一口径：核心是 AI 编程应用，不把文档/会议辅助能力写成产品身份；同时取代此前“本地优先 / local-first”表述。历史评审保留原文，但已标注为旧口径，不再作为路线依据。

## 一句话

**Spark 是一个浏览器原生、跨平台、开源可扩展的 AI 编程应用。**

同一套 Agent 能力运行在浏览器、终端、桌面壳、IDE 与 API 中；模型、工具、Skills、MCP、主题、工作流与部署方式都可替换、可自托管。

核心身份是 **AI 编程应用**：任务理解、代码检索与编辑、测试回灌、Review 与回滚是主线；文档、会议与表格只是企业控制面的辅助工作流，不构成产品定位。

## 三个核心主张

### 1. 浏览器原生，不受单一平台绑定

- Web 界面是主入口，面向 Chrome / Edge / Firefox / Safari 等现代浏览器设计；当前 UI 回归以 Chromium/Edge 为主，Firefox/Safari 仍需补跨浏览器门禁。桌面壳、CLI/TUI、VS Code、JetBrains、Neovim、移动端布局与 A2A API 共用同一套后端能力。
- 不是“某个编辑器里的插件”，也不是“只能装在 Windows 的桌面软件”。入口在浏览器里，能力在服务端，前端只是壳。
- 当前真实边界：Web UI 已经浏览器原生化；Agent 浏览器执行面已支持 Chromium / Chrome / Edge / Firefox / WebKit、持久化 Profile、CDP 接管已运行浏览器、按域名授权、敏感站点确认与性能 trace。扩展桥以官方 Playwright MCP `--extension` 预设落地，仍需用户安装浏览器扩展后手动启用；不把目标写成既成事实。

### 2. 全浏览器能力接入层，而不是只有一个 `browser_*` 工具

目标不是“再写一个爬虫”，而是把浏览器作为 Agent 的通用执行面：

- 引擎：Chromium / Chrome / Edge / Firefox / WebKit，本地与远程浏览器会话。
- 接入方式：CDP、WebDriver、浏览器扩展桥、Playwright、MCP 远程浏览器。
- 能力：标签页与窗口管理、已登录会话复用、Cookie/Storage、下载/上传、截图/PDF、控制台/网络、性能 trace、权限与对话框、文件选择、跨域 iframe。
- 安全：原生 `browser_*` 按域名/动作分级授权，敏感站点与凭据页面默认二次确认（一行 `deny` 可升级为阻断）；扩展桥走 MCP 只读白名单与人工审批。页面文本一律按不可信内容处理。

当前已落地的是 Playwright 五引擎（Chromium / Chrome / Edge / Firefox / WebKit）的 24 个 `browser_*` 工具、持久化 Profile、CDP 接管、按域名授权、敏感站点确认、trace 与 Chromium 性能指标，以及 Web 工作台里的网页/阅读模式入口。扩展桥是官方 Playwright MCP 的 `--extension` 预设加独立验收手册；更深的跨会话管理仍是后续项，不把“全浏览器能力”写成已完成。

### 3. 高度开源、自定义属性强

- MIT 许可，客户端与服务端都可改、可自托管、可二次分发。
- 可替换：模型 provider、工具、MCP server、Skills、斜杠命令、插件、主题、工作流、前端壳。
- 可编程：MCP / ACP / A2A 三种协议；插件有 manifest、权限、签名与代码包摘要；工作流是 YAML DAG；Agent 角色可用 `.claude/agents/*.md` 声明。
- 可审计：审批、沙箱、检查点、成本台账、OTel trace/metric/log 都是产品能力，不是附加 demo。

## 明确不是什么

- **不是“本地优先”产品。** 数据默认落在你指定的工作区，是部署事实和隐私选项，不是产品身份；也支持云端模型、自托管 relay 与远程浏览器。
- **不是通用办公套件。** 企业控制面提供文档与会议工作流，但它们是辅助能力，不是 Spark 的主身份。
- **不是单一 IDE 插件。** 编辑器集成是入口之一，不是能力边界。
- **不是封闭云平台。** 不要求账号才能用，不把会话、技能、插件锁在厂商云里。
- **不是“所有浏览器能力已经完成”。** 全浏览器接入是核心路线目标，当前完成的是 Playwright 五引擎这一层；远程会话、DevTools 网络深度等仍未收口。

## 对外推荐口径

- 中文：**浏览器原生、跨平台、开源可扩展的 AI 编程应用。**
- English: **A browser-native, cross-platform, open-source, extensible AI coding application.**
- 一句话解释：**把 Agent 放进浏览器，把浏览器变成 Agent 的手；模型、工具和界面都由你替换。**

## 与竞品的区隔

| 维度 | 多数前沿编程 Agent | Spark 的方向 |
|---|---|---|
| 主入口 | 编辑器插件 / 桌面 App / 终端 | 浏览器原生 Web 工作台 + CLI/TUI/桌面/IDE/API 共用后端 |
| 浏览器 | 内置一个浏览器工具或只做预览 | 浏览器能力抽象层：多引擎、已登录会话、远程浏览器、扩展桥、DevTools |
| 扩展 | 插件市场偏封闭，或只支持 MCP | MCP + ACP + A2A + Skills + 命令 + 插件 + 主题 + 工作流，全部可替换 |
| 模型 | 绑定自家模型/订阅 | 任意 OpenAI 兼容端点、国产模型、本地模型、fallback 链 |
| 部署 | 厂商云为主 | 自托管、单机、内网、远程 relay 都可；账号不是前置条件 |
| 审计 | 多数只给日志 | 审批、沙箱、回滚、成本、OTel 与交付账本同一条链 |

这张表的重点是“方向”，不是“已经全部领先”。核心模块的现状与差距见 `docs/CORE_CAPABILITY_GAPS.md`，可直接接入的开源资源见 `docs/INTEGRATION_MAP.md`。
