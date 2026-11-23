> [English](README.md) | **中文**

# Spark

**浏览器原生、跨平台、开源可扩展的 AI 编程与办公工作台。**

Spark 把同一条 Agent 循环带到浏览器、终端、桌面、IDE、移动端与 API。每次写入先过审批和沙箱，每个回合都能回滚；模型、工具、Skills、MCP 与界面都可替换，代码与账本默认存储在你指定的工作区。

```bash
# Linux / macOS —— 一行安装，SHA256 校验
curl -fsSL https://raw.githubusercontent.com/badhope/spark/main/scripts/install.sh | sh

# Windows（PowerShell）
irm https://raw.githubusercontent.com/badhope/spark/main/scripts/install.ps1 | iex

spark init    # 配置任意 OpenAI 兼容端点
spark web     # 打开浏览器界面
```

没有 API Key 也能启动，但 Spark 会如实显示配置错误并拒绝生成假回答。设置页提供显式的「Mock 展示模式」一键进入/退出；真实模式缺 Key、模型未下载、端点连不上都按真实状态报错。

![Spark 对话：推理过程、工具调用与实时上下文用量](assets/screenshots/11-chat.png)

## 核心能力

- **浏览器是执行面。** Web、桌面、CLI/TUI、IDE、移动端与 API 共用同一条 Agent 循环；浏览器操作、审批、回滚和账本不分裂成多套。浏览器自动化基于 Playwright，可切 Chromium / Chrome / Edge / Firefox / WebKit，可用持久化 Profile 复用登录态，也可通过 CDP 接管已运行的浏览器。
- **每一层都可替换。** MIT 开源；Provider、工具、MCP、Skills、命令、插件、主题与工作流都可换，也可自托管。
- **执行可审计。** 写入和命令执行前先过审批与沙箱；通配规则覆盖 user / project / local 三个作用域，`deny` 始终优先。凭据路径保护与 shell token 检查保持开启。
- **失败可回滚。** 每个回合快照工作区，文件与对话可一起恢复，非 git 目录同样有效。
- **任务即分支。** 开始任务、逐步提交、绿测后收尾；失败的运行会放弃分支，不把改动泄漏到主工作区。
- **处理真实文件。** 办公工作台把文档、会议录音和表格变成带来源行号的纪要、行动项、报告与导出文件；本地 OCR 与转写让原始资料留在机器上。
- **模型自由。** 任意 OpenAI 兼容端点、fallback 模型链、凭据池，以及独立的小模型承担辅助调用。

## 运行

```bash
spark                         # 终端 TUI
spark web                     # 浏览器界面，仅回环
spark web --host 0.0.0.0      # 局域网；设置→远程控制生成二维码链接
spark web --public-url https://spark.example.com  # 放在自己的 HTTPS 反向代理后
spark desktop                 # 原生窗口，仅回环
spark exec "任务" --json       # NDJSON 事件流，适合脚本与 CI
spark review                  # 审查未提交的 diff
spark doctor                  # 体检配置、凭据与运行时
```

办公工作台由企业控制面提供：

```bash
spark enterprise --host 127.0.0.1 --port 18800
# http://127.0.0.1:18800/office
```

`spark exec` 每行输出一个 JSON 事件，最后以 `{"type":"final","text":"…"}` 收尾，可直接接 `jq`。

## 界面

| 界面 | 命令 | 用途 |
|---|---|---|
| Web UI | `spark web` | 对话、文件、终端、执行图 |
| 企业 API | `spark enterprise` | `/api/v1` 控制面与 `/office` |
| 桌面 | `spark desktop` | 原生窗口，仅回环 |
| TUI / CLI | `spark` · `spark exec --json` | 终端工作与自动化 |
| IDE / ACP | `spark acp` · `spark ide install` | Zed、JetBrains、Neovim |
| MCP / A2A | `spark mcp-serve` · `spark a2a-serve` | 工具与 Agent 间协作 |

## 远程控制

打开 **设置 → 系统 → 远程控制**，生成一次性链接和二维码。手机接入的是同一个工作区；文件、命令、模型调用和审批仍在电脑上执行。同一 Wi‑Fi 用 `spark web --host 0.0.0.0` 即可；外网用 `spark web --public-url https://你的域名`，前面挂自己的 HTTPS 反向代理。

手机能发消息、审批、取消、切换会话、看文件和 diff；设置、插件、直接 shell/PTY 仍留在本机。配对码 5 分钟有效，每台设备拿独立的 7 天令牌，可单独吊销。设备列表带在线状态和仅本机可见的远程写审计；HTTPS 或 localhost 页面可开启 Web Push，锁屏时也能收到审批通知。

手机端使用独立首页而不是把桌面布局缩窄：390×844 首屏固定显示待审批、当前会话和新任务，底部四栏导航始终可见；审批在首页卡片点“查看并审批”后再点允许/拒绝，两步完成。支持加入主屏幕（PWA）；输入框的麦克风走浏览器 `SpeechRecognition`，浏览器不支持或权限被拒时提示并回到键盘输入。

![Spark 远程控制：二维码、一次性链接与已配对设备列表](assets/screenshots/25-remote-control.png)

![Spark 远程控制：在线设备、最近请求与仅本机可见的审计记录](assets/screenshots/26-remote-audit.png)

![Spark 手机端首页：待审批、当前会话与新任务在同一屏](assets/screenshots/27-mobile-home.png)

![Spark 手机端审批：首页卡片进入详情，拒绝或允许两步完成](assets/screenshots/28-mobile-approval.png)

## 安全模型

三层权限逐级收窄：

1. **审批** —— 从 plan-only 到 full-auto；默认模式在写入和命令前询问。
2. **沙箱** —— 默认工作区隔离，越界路径与受保护路径直接拒绝。
3. **规则** —— 按“工具 + 资源”匹配，覆盖 user / project / local 三个作用域；克隆下来的仓库只能收紧权限，不能给自己放行。

平台支持时再叠加 OS 级隔离：Linux 用 bwrap，Windows 用 Job Object，macOS 用 `sandbox-exec`。平台做不到的能力会如实写进工具结果，而不是假装有。

## 文档

| 英文 | 中文 |
|---|---|
| [Product positioning](docs/en/PRODUCT_POSITIONING.md) | [产品定位](docs/PRODUCT_POSITIONING.md) |
| [Core capability gaps](docs/CORE_CAPABILITY_GAPS.md) | [核心能力差距](docs/CORE_CAPABILITY_GAPS.md) |
| [Integration map](docs/INTEGRATION_MAP.md) | [外部集成地图](docs/INTEGRATION_MAP.md) |
| [Install & build](docs/en/INSTALL.md) | [安装与打包](docs/INSTALL.md) |
| [Configuration](docs/en/CONFIG.md) | [配置](docs/CONFIG.md) |
| [HTTP API](docs/en/API.md) | [API](docs/API.md) |
| [Architecture](docs/en/ARCHITECTURE.md) | [架构](docs/ARCHITECTURE.md) |
| [Features](docs/FEATURES.md) | [功能总览](docs/FEATURES.md) |
| [Remote control](docs/REMOTE.md) | [远程控制](docs/REMOTE.md) |
| [Enterprise control plane](docs/ENTERPRISE.md) | [企业控制面](docs/ENTERPRISE.md) |
| [A2A protocol](docs/A2A.md) | [A2A](docs/A2A.md) |
| [ACP / IDE setup](docs/ACP.md) | [IDE 接入](docs/ACP.md) |

更多：[文档索引](docs/README.md) · [English docs](docs/en/README.md) · [VS Code 扩展](vscode-spark/README.md) · [审计记录](docs/reviews/)。

## 本地验证

```bash
pytest -q
ruff check src tests scripts
mypy src/spark --ignore-missing-imports
```

CI 另外跑 Windows 保留路径检查、前端构建漂移、打包检查、密钥扫描、OpenAPI 快照检查，以及 mock 模式 Agent 评测。

## 运行要求

Python 3.11.5+（或直接使用打包产物），Node.js 18+ 仅用于前端开发，支持 Windows / macOS / Linux。

---

**License:** MIT · **Author:** badhope
