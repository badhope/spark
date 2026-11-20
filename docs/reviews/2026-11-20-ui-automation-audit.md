# Spark Web 全量交互与真实模型验收（2026-11-20）

> 状态：**已做完**。本文记录设置页、主界面、工作台、键盘、响应式宽度与真实模型的自动化验收结果，以及本轮发现并修复的细节。

## 1. 结论

- 新增 `tests/test_ui_full_interaction.py`，8 个用例覆盖设置全量动作、MCP Registry、消息操作、顶部工具、工作台五标签、附件/侧栏、键盘与四档宽度、Mock 测试连接短路。
- 与既有 `tests/test_ui_browser.py`（9）和 `tests/test_ui_a11y.py`（8）合并运行：**25 passed**，耗时 278.80s。
- 新增 `tests/test_real_provider_smoke.py`（默认跳过），对本地真实 provider 运行：**1 passed**，耗时 13.66s。
- 合计 **26 项自动化通过**，无失败、无新增 5xx、无 JS 异常；期望的可选工作流状态 404 已在用例中显式区分。
- `npm run lint`、`npm run build` 通过，生产入口 `index-HDsdAVrS.js`。

## 2. 自动化覆盖

### 2.1 设置页

- 5 个设置分组、18 个标签页全部遍历。
- 共点击 **65 个动作按钮**，去重后 **57 个不同动作**。
- 包含删除档案、清除已保存配置、恢复默认界面、重置快捷键、创建定时任务、刷新诊断、保存设置等破坏性或副作用动作；测试服务使用独立临时目录，不影响真实数据。
- MCP “从 Registry 浏览”单独用例：打开、等待列表/搜索、关闭、设置页保持打开。
- Mock 模式“测试连接”不发 `/api/test` 请求，仅显示“Mock 演示模式”提示，修复原先 502。

### 2.2 主界面

- 会话：新建、重命名（Enter 提交）、删除（内联确认）。
- 消息：用户消息复制、编辑回填；助手消息赞/踩（`aria-pressed`）、复制全文、重新生成；删除走统一确认弹窗并命中 `/api/messages/delete` 200。
- 附件：打开唯一文件选择器，选择 `sweep-note.txt` 后渲染 chip。
- 顶部：主题切换、检查点、命令面板、快捷键帮助、上下文环、成本 chip（有价格时）、模型 chip、右侧面板展开/收起。
- 工具菜单 7 项全部走通：文件、网页阅读、Agent 执行图、Fork、Markdown 导出、单文件 HTML 分享、内置终端。
- 下载验收：Markdown 文件非空且以 `.md` 结尾；HTML 分享含 `<html` 与 `spark`；SVG 含 `<svg`；README 下载内容与工作区一致。
- 键盘：菜单首项自动聚焦、End 跳末项、Escape 关闭并归还触发按钮；`Ctrl+K`、`?` 打开对应弹窗；Tab 首次即离开 `body`。
- 响应式：1440×900、1024×768、768×900、390×844 四档均无页面级横向溢出；设置与发送入口可见；390px 下发送按钮 ≥36px。

### 2.3 工作台

- 文件：过滤、预览、复制相对路径、复制内容、下载、返回文件树。
- 变更：刷新、复制完整 diff（测试工作区预置 git 仓库和未提交改动）。
- 终端：输入 `echo spark-full-sweep`、清屏、重启 shell。
- 网页：填入 `https://example.com` 并点击打开，面板保持可用。
- 追踪：复制图数据（JSON 含 nodes/edges）、导出 SVG、Zoom In/Out/Fit View。
- 五个标签均无空白页、无渲染异常。

### 2.4 可访问性与既有回归

- `tests/test_ui_a11y.py`：六个界面关键烟雾检查、桌面/390px 横向溢出、亮暗双主题 axe WCAG A/AA serious+critical 为零。
- `tests/test_ui_browser.py`：既有 shell、设置、发送、文件树、终端、命令面板、工作台五标签等 9 项。
- 本轮修复的对比度、重复附件入口、按钮尺寸、菜单键盘导航均被上述用例覆盖。

## 3. 本轮细节修复

| 问题 | 修复 | 验证 |
| --- | --- | --- |
| Mock 模式“测试连接”仍请求 `localhost:9999`，返回 502 | `SettingsModal.handleTest` 在 provider 为 `mock` 时短路，展示 `model.mockNotice` | `test_settings_mock_probe_does_not_call_network` 断言未发出 `/api/test` |
| 回形针与图片按钮打开同一 `*/*` 选择器，重复入口 | 删除图片按钮，保留单一附件按钮并补 `aria-label` | 设置/主界面测试与 axe 通过 |
| 亮色成功色对比不足（axe serious） | `--ok` 从 `#0f9d58` 调整为 `#0b7a43` | 双主题 axe serious+critical 为零 |
| 桌面消息操作按钮 24px 偏小 | 提升到 28px，图标 14px；移动端保持 36px/16px | 响应式用例断言发送按钮尺寸，消息按钮在既有移动端用例中验证 |
| 顶部工具菜单无键盘行为 | 打开聚焦首项，ArrowUp/Down/Home/End 移动，Escape 关闭并归还焦点 | `test_header_tools_menu_keyboard_and_actions` 实测 `document.activeElement` |
| 工作台追踪在无工作流时请求 `latest.json` 返回 404 | 属于可选空态探测；用例区分期望 404，不把它误报为 5xx/JS 错误 | 追踪标签复制/导出/缩放全部通过 |

## 4. 真实模型验收

环境没有 DeepSeek / 豆包 / OpenAI 商业 Key，因此使用本地真实模型完成同一条 OpenAI 兼容链路，未使用 mock 推理。

- 模型：`Qwen2.5-0.5B-Instruct Q4_K_M GGUF`，469 MB。
- 推理服务：`llama_cpp.server`，`127.0.0.1:8010`，`--n_ctx 16384 --logits_all false`，解决 16k 上下文 OOM。
- Spark：`SPARK_HOME=/tmp/spark-real-home`，`openai_compat`，`base_url=http://127.0.0.1:8010/v1`，`model=qwen2.5-0.5b-instruct`，`has_api_key=true`，`demo_mode=false`，上下文 `8575/16384`（52%）。
- 直接推理：`36 prompt / 15 completion / 51 total`，返回“我是由阿里云开发的超大规模语言模型，正在正常运行。”
- Spark UI 真实对话：输入“请回复：本地真实模型验收通过”，耗时 **2m27s**，消息元数据展示 `qwen2.5-0.5b-instruct`，助手回复进入真实消息流；截图 `spark-real-model-ui.png`。
- 质量说明：0.5B 小模型把工具调用输出成文本 `<tool_call> {{...}}</tool_call>`，说明其指令跟随/工具调用能力不足；这属于模型能力限制，不是 Spark mock 或 UI 渲染问题。真实商业模型需提供 API Key 后复测。

## 5. 复现命令

```bash
cd /home/user/Doubao/chats/38454959947244034/spark

# 全量 UI 回归（8 + 9 + 8 = 25）
.venv/bin/python -m pytest -q \
  tests/test_ui_full_interaction.py \
  tests/test_ui_browser.py \
  tests/test_ui_a11y.py

# 真实 provider（需先启动 8767 与本地模型）
SPARK_REAL_BASE=http://127.0.0.1:8767 \
  .venv/bin/python -m pytest -q tests/test_real_provider_smoke.py
```

## 6. 已知边界

- 商业模型 Key 不在环境中，无法验证 DeepSeek/豆包/OpenAI 的真实账号计费、限流与工具调用质量。
- 0.5B 模型只适合验证链路连通与 UI 元数据，不适合作为 Agent 工具调用质量基准。
- 未定价模型不显示成本 chip，设置页成本显示“—”，这是防止伪造 0 成本的预期行为。
- 本轮未纳入：扫描 PDF OCR、压缩包递归解包、音视频转写、反馈原因问卷。

**状态：已做完。**
