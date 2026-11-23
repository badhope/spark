# Spark — AI Coding Agent

浏览器原生、跨平台的 AI 编码与办公工作台，Python + React 双形态（CLI/typer + TUI/textual + Web/pywebview）。

## 架构

- `src/spark/web/server.py` — HTTP 入口（路由拆分到 routes.py，状态在 state.py，SSE 在 sse.py）
- `src/spark/core/loop.py` — 经典 ReAct 循环（LangGraph 路径在 graph.py）
- `src/spark/tools/registry.py` — 内建工具 + MCP 动态工具，dispatch table 模式
- `src/spark/tools/mcp_bridge.py` — 官方 MCP SDK (`mcp` 包)，支持 stdio/sse/streamable_http
- `src/spark/memory/store.py` — ChromaDB 向量记忆（会话 FTS5 索引在 `src/spark/store.py`）
- `src/spark/providers/openai_compat.py` — LiteLLM 统一 LLM 调用层，含指数退避重试
- `src/spark/pty/` — 真 PTY 终端后端（P1-5）
- `src/spark/providers/routing.py` — 国产模型三档路由矩阵（P1-10）
- `web/src/` — React + Zustand 前端

## 关键约定

1. **日志**：所有模块使用 `logging.getLogger(__name__)`，禁止 `print()`
2. **异常**：禁止 bare `except:` 或 `except Exception: pass`；关键路径至少 `logger.warning`
3. **类型**：使用 Pydantic v2 模型，控制 `type: ignore`（目前 12 处，多为平台差异）
4. **TOML**：配置序列化使用 `tomlkit`，不手搓字符串
5. **MCP**：使用官方 `mcp` SDK，不手写 JSON-RPC

## 验证命令

```bash
# Python
ruff check src/spark/                  # 零警告
python3 -m pytest tests/               # 全量回归（Windows 基线 623 passed / 5 skipped；PTY 用例仅 POSIX 执行）

# 前端
cd web && npx tsc --noEmit             # exit code 0
cd web && npx vite build               # 构建成功（产物写回 src/spark/web/dist/）
```

## EXE 构建（提交前必验）

**硬性顺序：先本地构建验证通过，再提交推送。** CI（.github/workflows/build-exe.yml）只是第二道保险，不替代本地验证。

任何触碰以下内容的改动，提交前必须在本地跑通：

```bash
python3 -m PyInstaller spark-onefile.spec --distpath dist --workpath build --noconfirm
./dist/spark --version                                    # 版本输出正常
SPARK_API_KEY=sk-dummy-verify ./dist/spark web --port 8399 --workdir /tmp/spark-demo \
  --approval full-auto --model mock-model &               # 起服务
curl -s -X POST http://127.0.0.1:8399/api/chat \
  -H 'Content-Type: application/json' -d '{"prompt":"list files"}'   # 事件流 200
```

- 触碰范围：`spark-onefile.spec` / `spark.spec` / `pyproject.toml` / `uv.lock` / `web/**` / `src/spark/web/dist` 的变更
- 平台：Linux/macOS 本地构建验证 spec 有效性；Windows EXE 由 CI 产出（PyInstaller 不支持交叉编译，勿在本地声称已产 .exe）
- 失败处理：构建或冒烟不过 → 修复重验 → 通过后才 `git commit` + 推送

## 工具开发

新增工具时：
1. 在 `tools/` 下实现工具函数（Pydantic Args 模型 + `ToolResult` 返回）
2. 在 `tools/registry.py` 的 `_build_dispatch_table()` 中注册
3. 添加对应的测试用例

## 安全

- 沙箱：`sandbox.py` 命令过滤 + `policy.py` 权限规则
- 审计日志：`core/loop.py` 中 tool call 记录 name+args+elapsed+ok（写进程日志；hook_block/plan_only/deny 等早退分支除外）
- 脱敏：`sanitize.py` 正则 + 密钥 tuple 双重过滤
