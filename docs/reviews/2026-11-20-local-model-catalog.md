# Spark 本地小模型目录与下载器（2026-11-20）

## 结论

- 默认推荐从过时的 Qwen2.5-0.5B 切换为 **MiniCPM5-1B Q4_K_M**：1.08B、原生 128K 上下文、中文优先、Apache-2.0、XML 工具调用。权重约 656MB。
- 增加 **BotPsy-0.8B Q4_K_M**：2026-11 新端侧 Agent 模型，0.8B、Apache-2.0，工具调用格式为 `<function=...>`；英文优先，32K 部署上下文。用于“更强工具调用、英文任务”的实验档。
- 不把 Qwen3.5-0.8B 放进默认目录：262K 上下文很大，但实测直接复述 prompt、不触发工具调用，不适合 Spark 的 Agent 回合。
- 设置页新增“本地模型（内置推荐）”：目录、体积、上下文、许可证、下载进度、SHA256 落盘、断点续传、安装后一键“使用此模型”。

## 适配层

- 新 provider：`llama_cpp`，直接通过 `llama-cpp-python` 在进程内加载 GGUF，不需要 Ollama/端口。
- 使用 GGUF 自带 Jinja chat template；不能强制 `chatml`（MiniCPM5 会输出 `****` 乱码）。
- 用底层 `generate` + `detokenize(special=True)`，否则 `<function` / `<|im_end|>` 等特殊 token 会被吞掉。
- 解析三种工具格式：MiniCPM5 `<function name=.<param ...>`、Qwen3.5/BotPsy `<function=...><parameter=...>`、JSON `<tool_call>{...}</tool_call>`。
- 停止条件按 `<|im_end|>` 特殊 token，避免模型生成下一轮假对话。

## 实测

- MiniCPM5-1B 直接生成：`<think>...` 推理 + 中文正文正常。
- MiniCPM5-1B 工具调用：真实 `read_file` 调用参数正确；工具结果回填后第二轮可生成总结。
- BotPsy-0.8B：英文工具调用 `/function=read_file` 解析正确。
- Qwen3.5-0.8B：能加载、能聊天，但工具调用失败，未进入目录。

## 风险

- 16K 上下文在纯 CPU 上首轮较慢；默认 `SPARK_LOCAL_CTX=16384`，可由高级用户调高。
- 下载源为 ModelScope / Hugging Face 直链；未固定官方 SHA256，当前校验大小 + GGUF magic + 本地哈希，后续应补官方哈希。
- 1B 模型仍不能保证复杂多轮 Agent；4B 级才是“稳定干活”的下限。

## 全模型 UI 回归（2026-11-21 最终）

服务：`127.0.0.1:8768`，`provider=llama_cpp`，`model=/home/user/.spark/models/MiniCPM5-1B-Q4_K_M.gguf`，16K。

- `test_ui_a11y.py`: 8 passed
- `test_ui_browser.py`: 9 passed（含真实发送与工具调用）
- `test_ui_full_interaction.py`: 8 passed（全设置页、工作台五页、下载/重发/移动端）
- `test_settings_roundtrip.py` + `test_settings_shelf_actions.py`: 12 passed
- 合计 **37 passed**，耗时 6m20s，全部使用真实 MiniCPM5-1B 作为后端。

## 全局回归（2026-11-21 最终）

- 全量 pytest：**1761 passed, 13 skipped**，1774 collected，耗时 15m34s，EXIT=0。
- 真实 MiniCPM5 后端 UI：**37 passed**（8 a11y + 9 browser + 8 full interaction + 12 settings）。
- 本地模型：**15 passed**（目录、下载校验、活动状态、一键启用、两种工具格式）。
- 额外真实模型任务：写入文件、shell 调用、多轮暗号记忆，见交付说明。

## 真实模型能力矩阵（最终复测）

服务：`127.0.0.1:8768`，`provider=llama_cpp`，MiniCPM5-1B-Q4_K_M，16K 上下文。

- 写文件：新会话“只把 hello world 写入 notes.txt，然后回答”，真实调用 2 次工具后收束，`notes.txt` 落盘 11 bytes，内容为 `hello world`。
- shell：同一会话紧接“只运行 pwd 一次，告诉我实际 stdout”，真实调用 1 次 `run_shell`，3.2s 回答 `/tmp/spark-ui-model-work`，工具 `tool_end.result.stdout` 与该路径一致。
- 多轮记忆：新会话“记住暗号是蓝鲸，只回复：收到”答“收到”；下一轮“暗号是什么”答“蓝鲸”。
- 结论：清晰、单步指令下，1B 能完成写文件、shell、两轮记忆；模糊任务仍可能反复调用，由循环保护兜底。

## 工具循环保护

- `max_tool_rounds` 触发时返回结构化 `tool_loop_limit`：标题“模型在工具调用里绕不出来”，说明工具可能已成功、结果保留在工作区，建议拆小任务或换 4B。
- 相同参数重复调用触发 `tool_repeat_loop`：标题“模型重复调用同一个工具”，重复调用被拦截，不污染工作区。
- Web 错误卡按 code 显示中英文文案与短标签；ACP 将两类 code 映射为 `max_turn_requests`；TUI 对中文标题同样给出可操作提示。
- 定向回归：`test_loop.py` 3 passed；`test_reliability.py`、`test_adversarial_security.py`、`test_acp_server.py`、`test_tui_welcome.py` 全部通过。
