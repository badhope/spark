# Spark Web 收口计划（2026-11-20）

> 目标：把上一轮列出的剩余项全部落地，统一构建、浏览器验收、文档更新后一起交付。
> 版本：Spark `0.15.2`（本轮不 bump）；分支 `main`。

## 1. 范围与已定契约

| 编号 | 项目 | 交付定义 |
| --- | --- | --- |
| C1 | PDF / 二进制附件 | 前端按文件类型读取；PDF、DOCX、XLSX、PPTX 在服务端抽取文本注入上下文；其余二进制保存到 `.spark/attachments/<session>/` 并把可读路径交给模型；历史只展示文件名，不泄漏正文。 |
| C2 | 消息赞 / 踩 | `message_feedback` 持久化表（up/down + 可选 comment），`POST /api/messages/feedback` 支持设置/切换/取消；历史回填状态；助手消息显示高亮按钮。 |
| C3 | 执行图导出 | 复制/导出按钮补 `aria-label`；下载链接挂到 DOM 后触发并在延迟回收；真实浏览器下载 SVG 并检查内容。 |
| C4 | 移动端消息操作 | 消息复制/编辑/删除/赞踩按钮在窄屏放大到 36px 触控目标，图标同步放大；桌面维持 24px。 |
| C5 | 减少动画 | 统一 `prefers-reduced-motion: reduce` 门控：停止无限循环、关闭延迟、滚动行为回退；浏览器模拟 reduced motion 验收。 |
| C6 | 队列日期 / 实时耗时 | 跨天任务显示 `MM/DD HH:MM`；运行中任务每秒刷新耗时，结束后冻结。 |
| C7 | 最终交付 | lint/build、服务重启、API 端到端、浏览器截图、文档矩阵、计划完成清单合并交付。 |

## 2. 实施顺序

1. C1 后端契约与解析器；前端上传/芯片/发送链路；API 测试。
2. C2 数据库迁移与存储方法；反馈 API；历史回填；前端按钮与 store。
3. C3–C6 小步修改并逐项浏览器验收。
4. C7 全量 lint/build、服务重启、API/浏览器复验、文档更新、截图归档。

## 3. 验收标准

- PDF 上传后能回答文档中的事实；DOCX/XLSX/PPTX 至少抽取主要文本；未知二进制返回路径且不丢附件名。
- 赞/踩刷新页面后仍保持；再次点击同方向取消；跨会话不串数据。
- 执行图下载得到的 `.svg` 包含节点文本与边。
- 390px 宽度下消息操作按钮可点击区域 ≥ 36px。
- 系统 reduced motion 下无无限动画、无过渡延迟。
- 队列跨天时间带日期；running 任务耗时持续增长，finished 后固定。
- `npm run lint`、`npm run build`、相关 pytest 全通过；无新增控制台报错。

## 4. 非目标

- 音视频附件转写、压缩包递归解包、OCR 扫描 PDF；这些需要独立管线，本轮只保证文件被接收、元数据可读、可定位。
- 消息反馈的原因问卷/后台分析面板；本轮只做 up/down 持久化与 UI。

## 5. 执行结果（已做完）

- [x] **C1 PDF / 二进制附件**：后端新增 PDF / DOCX / XLSX / PPTX 文本抽取与未知二进制物化；前端支持 `*/*`、类型/大小 chip、失败重试携带附件。API 验证 PDF 命中 `Spark PDF fixture: answer is 42`，DOCX 命中 `ORION`，XLSX 命中 `99`，PPTX 命中 `COMET`，`blob.bin` 注入 `.spark/attachments/<session>/` 路径；浏览器实测 PDF chip `fixture.pdf PDF 1.3 KB`，数据库消息含抽取正文与路径。删除会话后附件目录同步移除。
- [x] **C2 消息赞 / 踩**：`message_feedback` 表、schema v3、`POST /api/messages/feedback`、历史回填、前端乐观更新/同向取消/失败回滚全部落地。API 验证 `up → down → null` 均 200，非当前会话或 user 消息返回 404；浏览器刷新后 `aria-pressed` 保持；测试反馈已清理。
- [x] **C3 执行图导出**：复制/导出补 `aria-label`，下载 anchor 先挂 DOM、点击后延迟回收。浏览器真实下载 `spark-execution-1795168919711.svg`，1414 B、520x264、含 1 条连线及“主代理 / 产出”节点文本。
- [x] **C4 移动端消息操作**：390x844 视口实测消息操作按钮 `36 × 36`（桌面为 `24 × 24`），图标 16px/12px 同步；消息区与附件 chip 截图见 `spark-ui-mobile-actions.png`。
- [x] **C5 减少动画**：模拟 `prefers-reduced-motion: reduce` 后实测 `animation-duration 1e-05s`、`iteration-count 1`、`transition-duration 0.01ms`、`scroll-behavior auto`；正常模式对照为 `1.8s / infinite`。
- [x] **C6 队列日期 / 实时耗时**：同日任务显示 `17:37`；跨天任务显示 `11/19 17:37`；running 任务耗时 2.2 秒内从 `19.7s` 增至 `21.7s`，结束后冻结。测试行已恢复/删除，队列回到两条原始任务。
- [x] **C7 最终交付**：`pytest tests/test_web_attachments_feedback.py` 3 passed；`npm run lint`、`npm run build` 通过；服务重启后返回最新入口 `index-DAp0XIc8.js`；计划、UI 对标矩阵、截图与清理结果统一归档。

### 验收数据与清理

- 最新构建入口：`index-DAp0XIc8.js`；服务日志 `/tmp/spark-web-37.log`。
- 截图：`spark-ui-mobile-actions.png`、`spark-ui-queue-datetime-live.png`；执行图导出文件 `/home/user/Downloads/spark-execution-1795168919711.svg`。
- 回归测试：`tests/test_web_attachments_feedback.py`（3 passed）。
- 清理：PDF 测试会话 `69a1ad56099b`、空会话 `75a40c76d953` 已删除；`.spark/attachments/69a1ad56099b/` 已随会话删除；`message_feedback` 测试记录已清空；队列两条原始任务时间戳已还原。

## 6. 后置加固：全量交互与真实模型（已完成）

- [x] **C8 全量交互自动化**：新增 `tests/test_ui_full_interaction.py`（8 个用例），覆盖 18 个设置页 65 个动作按钮、MCP Registry、消息复制/编辑/赞踩/重生成/删除、侧栏新建/重命名/删除、附件选择、顶部 7 项工具、Markdown/HTML/SVG/README 下载、工作台五标签、纯键盘菜单、`Ctrl+K`、`?`、四档响应式宽度。与既有 UI 测试合并 **25 passed**（278.80s）。
- [x] **C9 真实模型验收**：本地 `Qwen2.5-0.5B-Instruct Q4_K_M` + `llama_cpp.server`（16k、`logits_all=false`）；Spark `openai_compat`，`demo_mode=false`，真实 UI 对话 2m27s，消息元数据展示模型名；直接推理 usage `36/15/51`。`SPARK_REAL_BASE=http://127.0.0.1:8767` 时 `tests/test_real_provider_smoke.py` **1 passed**。
- [x] **最新构建**：`npm run lint`、`npm run build` 通过，入口 `index-HDsdAVrS.js`；Mock “测试连接”不再发 502。
- 详细记录与已知边界见 `docs/reviews/2026-11-20-ui-automation-audit.md`。

**状态：已做完（C1–C9）。**
