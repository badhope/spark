# Spark Agent（VS Code 扩展）

在编辑器内完成一次完整的 AI 编程闭环：选区上下文 → 流式对话 → 工具执行 →
多文件 diff 审批 → 诊断回显 → `file:line` 跳转。

| 命令 | 作用 |
|------|------|
| **Spark: 打开聊天面板** | 在侧边打开原生聊天面板（`spark web` 作为控制面，自动拉起/复用） |
| **Spark: 新建会话** | 创建一个 `isolated` 会话，不抢占浏览器 Web UI 的当前会话 |
| **Spark: 对选中代码提问** | 把选区（文件、行号、语言、代码）带进面板 |
| **Spark: 解释 / 修复选中代码** | 预填提示词，直接进入面板 |
| **Spark: 打开 Web UI / TUI** | 保留的全功能入口 |
| **Spark: 查看 Problems** | 跳到 VS Code Problems；Spark 写入的诊断会同步进去 |

## 编辑器内能力

- **选区上下文**：右键菜单 / `Ctrl+Alt+Shift+S` 发送 `file:line` + 代码块。
- **SSE 流式**：直接消费 `/api/chat/stream`，文本、工具、计划、审批实时更新。
- **内联 diff**：审批请求带 `diff` 时自动打开 `vscode.diff`（真实文件 vs 提案），
  面板上的 **Apply / Allow** 让后端执行，**Reject** 拒绝。
- **工具结果回显**：诊断写入 VS Code Problems；测试/命令输出摘要显示在工具卡片。
- **file:line 跳转**：工具结果中的 `path:line` 变成可点击 chip。
- **任务状态**：状态栏显示计划完成度 `Spark n/m`。

## 前置条件

- 已安装 Spark（打包版 EXE，或 `pip install spark-agent`；扩展默认调 `spark`，
  可在设置里改 `spark.executable` 指向可执行文件路径）。
- 审批模式可在设置里选（默认 `suggest`，写/执行逐条确认）。
- 如果已有常驻 Web 服务，把 `spark.serverUrl` 指过去即可复用；非回环地址需同时配
  `spark.token`。

## 安装

```bash
code --install-extension vscode-spark/spark-agent-0.15.2.vsix
```

（Marketplace 上架待签名与发布者账号，当前为本地 VSIX 分发。）

> AI生成
