# 观测（OTel / Langfuse）

Spark 的观测走 **OTel GenAI 语义约定**：`trace=turn`、`span=tool`、
metric=token/延迟。**默认关**；自托管 [Langfuse](https://langfuse.com)
原生接受 OTLP，因此接入任意 OTLP 端点即可获得完整 trace 视图——Spark
不做专属观测面板。

## 开启

设置环境变量后重启 Spark：

```bash
# 自托管 Langfuse / collector 的 OTLP gRPC 端点
# 推荐写 grpc://host:4317 或 host:4317；http:// 也会被剥成 host:port
export SPARK_OTEL_ENDPOINT=grpc://localhost:4317

# 可选：服务名（默认 spark-agent）
export SPARK_OTEL_SERVICE_NAME=spark-agent

# 可选：把 Python stdlib logging 也桥接到 OTLP（默认关，避免重复日志）
export SPARK_OTEL_BRIDGE_LOGGING=1
export SPARK_OTEL_LOG_LEVEL=INFO

# 可选：trace 采样比例（0–1，默认 1）
export SPARK_OTEL_SAMPLE_RATIO=1
```

体检：`spark doctor` 会显示观测状态与开启指引。

## 语义映射

| Spark 概念 | OTel 表达 | 关键 attributes |
|---|---|---|
| 一次用户 turn | span `spark.turn` | `gen_ai.system` / `gen_ai.request.model` / `gen_ai.conversation.id` / `spark.turn.resumed` / `spark.usage.source` |
| turn 结束 | span 结束 + metrics | `gen_ai.usage.input_tokens` / `output_tokens` / `reasoning_tokens`、`spark.turn.latency_ms` |
| 一次工具调用 | 子 span `spark.tool` | `gen_ai.tool.name` / `spark.tool.arguments` / `spark.tool.ok` / `spark.tool.approval` / `spark.tool.duration_ms` |
| 指标 | histogram | `spark.turn.tokens`（kind=input/output/reasoning）、`spark.turn.latency_ms`、`spark.tool.duration_ms` |
| 审批 | span `spark.approval` + log `spark.approval.decision` | 工具名、摘要、决定、等待毫秒 |
| 工作流 | span `spark.workflow` / `.step` + log `spark.workflow.finished` | 工作流名、step、attempt、status、失败步数、总成本 |
| 审计事件 | OTel log record | `spark.tool.failed` / `spark.approval.decision` / `spark.workflow.finished` |

token 口径：provider 上报真实 usage 时为 `reported`（实测），否则 `estimated`
（启发式估算）——`spark.usage.source` 标明。

## Langfuse 自托管示例

```bash
docker compose up -d   # langfuse/langfuse:2 自带 OTLP (4317/4318)
export SPARK_OTEL_ENDPOINT=http://localhost:4317
spark web --workdir /your/project
# Langfuse UI 即可看到 spark.turn / spark.tool 树形 trace 与 token 指标
```

## 测试模式

`SPARK_OTEL_ENDPOINT=inmemory`：span、metric、log 都记录在进程内
（`InMemorySpanExporter` / `InMemoryMetricReader` / `InMemoryLogRecordExporter`），
供测试断言；生产环境勿用。

## 实现说明

- `src/spark/otel.py`：**自管 provider**（OTel 全局 `set_tracer_provider`
  只能生效一次，自管模式支持测试与多实例重置）。
- 未启用时 tracer 为 NoOp、meter/logger provider 为 None——观测代码路径零分支、零开销。
- 生产端点：默认走 gRPC OTLP，优先 `grpc://host:4317`。若装可选依赖
  `opentelemetry-exporter-otlp-proto-http`，`http(s)://host:4318` 会自动走
  HTTP exporter（按 `/v1/traces`、`/v1/metrics`、`/v1/logs` 分路）；未安装时
  回退 gRPC，端口不匹配不会导出。
- 审计 attributes 出进程前统一脱敏：`api_key`、`password`、`secret`、
  `authorization`、`cookie` 等键写 `[REDACTED]`；token 计数不算凭据，保留原值。
- `SPARK_OTEL_BRIDGE_LOGGING=1` 时 stdlib logging 全量桥接；默认关，因为本地
  编程助手逐行日志会让 OTLP 很吵。
- 审计日志是**显式事件**（审批决定/工具失败/工作流结束），不是 Python
  `logging` 全量桥接；全量 stdlib 日志桥接与 HTTP OTLP 仍未做。
- 成本计价暂未进观测路径（`fetch_catalog` 首次调用会同步走网络，
  不能阻塞 turn 边界）；成本归账随上下文合同/replay 批次一起落。
