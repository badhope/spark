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

## 全模型 UI 回归（2026-11-20）

服务：`127.0.0.1:8768`，`provider=llama_cpp`，`model=/home/user/.spark/models/MiniCPM5-1B-Q4_K_M.gguf`，16K。

- `test_ui_a11y.py`: 8 passed
- `test_ui_browser.py`: 9 passed（含真实发送与工具调用）
- `test_ui_full_interaction.py`: 8 passed（全设置页、工作台五页、下载/重发/移动端）
- `test_settings_roundtrip.py` + `test_settings_shelf_actions.py`: 12 passed
- 合计 **37 passed**，全部使用真实 MiniCPM5-1B 作为后端。
