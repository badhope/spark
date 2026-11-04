# Spark Agent（VS Code 扩展）

把 VS Code 工作区一键接入 Spark 的三种使用面：

| 命令 | 作用 |
|------|------|
| **Spark: Open Web UI** | 在工作区根目录起 `spark web`（自动解析实际端口）并打开浏览器；已有实例则直接复用 |
| **Spark: New TUI Session** | 在集成终端里启动 Spark TUI |
| **Spark: Ask about selection** | 选中的代码 → 输入问题 → 在 Web UI 中带上下文提问 |

## 前置条件

- 已安装 Spark（打包版 EXE，或 `pip install spark-agent`；扩展默认调 `spark`，
  可在设置里改 `spark.executable` 指向可执行文件路径）
- 审批模式可在设置里选（默认 `suggest`，写/执行逐条确认）

## 安装

```bash
code --install-extension vscode-spark/spark-agent-0.12.0.vsix
```

（Marketplace 上架待签名与发布者账号，当前为本地 VSIX 分发。）

> AI生成
