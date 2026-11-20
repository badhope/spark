# 安全说明

## 报告漏洞

发现安全问题请**不要**公开提交 Issue，以免在修复前被利用。请通过以下任一方式私下联系维护者：

- 使用本仓库的 **Security Advisories / 私有反馈**入口（推荐）
- 或在仓库所有者主页获取联系方式后直接联系

我们会在确认后尽快评估并发布修复。

## 安全设计（内置）

- **审批门**：6 档审批模式（`suggest` / `auto-edit` / `full-auto` / `plan-only` / `approve-plan` /
  `manual`），写入与命令按档位征求同意；`[[permissions]]` 通配规则可精确到"工具 + 路径"
- **四档沙箱**：`sandbox-only` / `workspace`（默认）/ `full-access` / `unrestricted`；
  路径越界、受保护路径（含 `~/.spark` 与凭据文件）、shell 命令 token 级检查逐层拦截
- **注入防护**：MCP / 工具 / 网页抓取结果一律包裹 `<untrusted>` 围栏，不作为指令执行
  （见 `src/spark/tools/untrusted.py`）
- **出口脱敏**：SSE 事件、错误文案、日志经三层脱敏（密钥 / 家目录 / 堆栈）；
  密钥在报错信息中自动打码（见 `src/spark/sanitize.py`）
- **Web 控制面**：默认仅监听 `127.0.0.1`；`--host 0.0.0.0` 时启动会打印每进程控制令牌
  （`X-Spark-Token`），非本机请求必须携带
- **本地优先**：会话、记忆、检查点、用量全部存于 `~/.spark/`，不经过第三方服务器；
  可完全离线运行（Ollama / mock）
- **可回滚**：每轮涉及文件或命令的操作自动打工作区快照，支持回滚（含非 git 目录）
- **依赖安全门**：`Security` workflow 的 `uv audit --locked` 与 `pip-audit` 默认阻断合并；
  仅豁免已登记、上游无修复版本且 Spark 不暴露其服务端的 ChromaDB 嵌入式客户端 advisory

## 已知边界

- 受保护路径的 shell 检查是**命令 token 级启发式**：运行时拼接的路径（如
  `python -c "open(chr(67)+...)"`）可绕过。最终防线是审批模式——对凭据敏感的场景建议
  使用 `suggest` / `auto-edit`，而不是 `full-auto`
- `unrestricted` 档会关闭包括 Spark 自身代码保护在内的全部拦截，仅建议在隔离环境使用
- 模型端点（`base_url`）由用户自选，Spark 不代理、不中转你的提示词与代码

## 支持的版本

仅维护最新发布版本（见 CHANGELOG 与 tag）；安全修复随下一版本发布。
