# A2A Server（Agent2Agent 协议）

Spark 可以作为一个 **A2A Server** 被其他 agent 编排器调用（A2A 是
Linux Foundation 的 agent-to-agent 开放协议，JSON-RPC 2.0 over HTTP）。
与 Spark 已有的两个出口各占一层，互不重叠：

| 出口 | 协议 | 对面是谁 |
|------|------|----------|
| `spark mcp-serve` | MCP | agent（把 Spark 的 36 个工具暴露给别的 agent） |
| `spark acp` | ACP | 编辑器（Zed 等） |
| `spark a2a-serve` | **A2A** | 编排器（跨团队/跨厂商的 agent 委派） |

## 启动

```bash
spark a2a-serve --workdir /your/project --port 41241
# 默认只监听 127.0.0.1；对外暴露请显式 --host 0.0.0.0 并自行评估风险
```

## 能力边界（诚实声明）

- **Agent Card**：`GET /.well-known/agent-card.json`（兼答 `/agent.json`），
  声明 `capabilities.streaming = false`
- **`message/send`**：非流式——跑一个 turn，返回 `completed` Task +
  文本 artifact；同 contextId 的请求串行执行
- **`tasks/get`**：查任务状态（进程内登记，重启即失）
- **`ping`**：连通性探测
- 未声明的能力（`message/stream`、push notifications）按规范返回 -32601，
  客户端会自动降级到 `message/send`

## 快速验证

```bash
curl http://127.0.0.1:41241/.well-known/agent-card.json

curl -X POST http://127.0.0.1:41241/ -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 1, "method": "message/send",
  "params": {"message": {"role": "user",
    "parts": [{"kind": "text", "text": "list files"}],
    "contextId": "ctx-1"}}}'
```

## 会话模型

每个 `contextId` 对应一个独立 Spark 会话（独立 AgentLoop，最多 64 个
LRU 复用）；`taskId` 由调用方提供或自动生成，`tasks/get` 可回查状态。
审批模式用 `--approval` 显式指定（无人值守默认 `full-auto`）。

> AI生成
