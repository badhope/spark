# Changelog

本项目遵循语义化版本。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [Unreleased]

### 真实基础设施联调、EXE 与全量 UI 自动化（2026-11-14）

- **真实 PostgreSQL 暴露并修复**：`_ensure_default_tenant` 只在自动建表时执行，
  且 `Tenant.id` 用随机值，导致 PostgreSQL 上所有写操作违反 `tenant_id` 外键
  （SQLite 默认不校验外键，所以测试一直是绿的）。现在无论建表方式都确保默认租户
  存在，且其主键固定为 `default`。新增 `tests/test_control_postgres.py`。
- **真实 Keycloak 暴露并修复**：Keycloak 把 client id 放在 `azp`、`aud` 为 `account`，
  原来只校验 `aud` 导致所有 OIDC 请求 401。现在 `azp` 与 `aud` 任一匹配即通过。
- **OIDC 端到端**：真 token → `/api/v1/me`（tenant/roles 正确）→ 写 PostgreSQL 201；
  匿名 401、member 写 403（Casbin 正确拒绝）。
- **技能保存静默丢失**：`/api/skills` 对无 YAML frontmatter 的内容返回 `ok` 但列表
  为空。现在返回 422 并说明需要 frontmatter。
- **设置页自动化**：`tests/test_settings_roundtrip.py`（改值→保存→回读→还原）与
  `tests/test_settings_shelf_actions.py`（memory/knowledge/automations/skills/
  prompts/plugins/debug 的真实业务动作）。
- **评测接真实模型**：`spark.evals` 新增 `SPARK_EVALS_REAL=1`，用配置的 provider 而
  不是脚本化 mock；`tool_sequence` 支持等效工具（`alternatives`/`args_optional`），
  不再因为「先 glob 还是直接读」判负。真实模型 4/4。
- **EXE 打包补齐**：`spark.spec` 加入 `spark.control.*`、fastapi/uvicorn/sqlalchemy/
  alembic/casbin/authlib/jwt/multipart/reportlab/docx/openpyxl/pptx/pypdf/pypdfium2/
  rapidocr/extract_msg，以及 RapidOCR 的 ONNX 模型与 reportlab 字体数据、Casbin
  策略文件。实测 EXE 内 `/api/v1`、`/office`、md/docx/pptx/pdf/xlsx 导出、图片 OCR、
  `spark exec` 全部可用。
- **桌面端**：pywebview 6.2.1 + WebView2 实测启动、加载完整前端。
- **VSIX**：重新打包为 `spark-agent-0.13.1.vsix`（旧 0.12.0 已移除）。
- **Windows Job Object**：在嵌套虚拟化（RDO/KVM）下 `KILL_ON_JOB_CLOSE` 只终止直接
  子进程，祖先孙进程存活。测试改为探测宿主能力后断言，并清理逃逸进程，不再假装通过。

### 办公模型摘要与网关修复（2026-11-13）

- **模型摘要（可选）**：`SPARK_CONTROL_MODEL_ASSIST=true` 时，会议纪要追加
  “AI 摘要”、报告追加“AI 结论 / AI 风险”章节；结构化解析与指标计算不变，
  模型失败/超时/输出被截断时静默回退确定性结果（`src/spark/control/ai.py`）。
- **网关修复**：LiteLLM Router 对自定义模型名缺少 provider hint 会抛
  `LLM Provider NOT provided`；现在复用 `litellm_model_name()` 补 `openai`。
  `create_app` 改用 `load_config`，使 `SPARK_BASE_URL` / `SPARK_MODEL` 生效。
- **办公 UI 修复**：最近报告点击被会议的全局选择器覆盖导致 `meeting not found`，
  改为 `#recent` / `#report-recent` 分区绑定；报告章节正文按换行渲染。

### 真实模型接入与 provider 兼容（2026-11-12）

- **provider 兼容**：OpenAI 兼容网关若以 400 拒绝 `reasoning_effort`，自动去掉该
  可选参数重试一次，不消耗重试预算；普通对话/工具调用不再因网关不支持该参数失败。
- **真实模型验证**：`spark-x2.6-4b`（`https://maas-api.cn-huabei-1.xf-yun.com/v2`）
  跑通普通对话、SSE 流式、`list_dir` 工具调用和 Web UI；密钥仅保存在工作区
  `.secrets/maas.key`，不进入仓库。
- **opencode 接手改动核对**：新增 `spark.proc.run_bounded`，替换多处
  `subprocess.run(timeout=...)`，修复 Windows 下子进程继承管道导致超时不生效；
  相关测试通过。

### Wave 3：会议纪要与行动项第一增量（2026-11-11）

- **新增 `/office` 工作台**：粘贴会议转写，生成参会人员、议题、决议、行动项；
  每条决议/行动附来源行号，缺失负责人/截止时间显示“待确认”。
- **成果物**：Markdown + docx（`python-docx`，`office` extra）；本地 artifact
  store 保存，API 支持下载；支持人工“批准纪要”。
- **API**：`/api/v1/office/meetings` 列表/生成/详情/批准/下载。
- **报告/表格**：`src/spark/control/report.py` + `/api/v1/office/reports`；
  指标支持 `higher`/`lower` 方向，输出 Markdown、Excel（含柱状图）、Word、PPTX。
- **PPTX**：会议纪要与报告均可导出演示文稿；`/office` 增加下载 PPTX。
- **文档导入**：`documents.py` 支持 docx/xlsx/pptx/pdf/eml/msg/text/html 以及
  图片/扫描 PDF OCR（RapidOCR 本地 ONNX）；会议生成支持 `document_filename`，
  文件限定在 `SPARK_CONTROL_DOCUMENT_ROOT` 下。
- **浏览器上传**：`POST /api/v1/office/documents/upload`（multipart，最大 25MB），
  `/office` 增加文件选择，上传后自动解析并回填文件名；文件名净化、路径逃逸防护。
- **IMAP 邮箱导入**：`POST /api/v1/office/documents/imap` 按配置拉取最新邮件，
  保存为 `inbox/*.eml` 并复用文档解析；`/office` 增加“从 IMAP 拉取最新邮件”。
- **PDF 导出**：会议纪要与报告新增 `pdf_bytes()`（ReportLab + 内置 CJK 字体），
  下载格式增加 `format=pdf`，页面增加“下载 PDF”。
- **主应用右侧栏**：默认折叠为 32px 展开把手；标题栏按钮、关闭按钮、把手均可切换；
  文件树 / 预览 / 网页阅读能力保留。
- **离线自测**：内置 MockProvider 驱动 `list_dir` 工具调用成功；右侧面板打开并预览
  `README.md` 正常；内置终端执行 `echo spark-terminal-ok` 输出正确。用于无模型 Key
  时验证 UI、工具与 PTY 链路。
- **风格统一**：`/office` 改为与主 Web 应用一致的亮色体系（#f2f2f4 / 白卡片 /
  #e8620c 橙色 / 同一字体栈），不再使用独立深色主题。
- **测试**：`tests/test_control_office.py` 4 passed、`tests/test_control_report.py`
  4 passed、`tests/test_control_documents.py` 17 passed；浏览器实测生成、下载、
  批准、上传、IMAP 按钮、报告页签与表格列宽问题已修正。
- 当前为确定性解析，不调用模型；转写不写入任务 payload。

### Wave 2：企业控制面第一增量（2026-11-11）

- **新增 `src/spark/control/`**：FastAPI `/api/v1`，提供 health/me/models/
  tenants/projects/tasks/artifacts/audit；本地 SQLite + dev auth，生产
  PostgreSQL + Keycloak OIDC。
- **数据模型与迁移**：Tenant/Organization/User/Membership/Project/Task/
  Artifact/AuditEvent；Alembic 初始版本 `7dbcd0bb0272`，SQLite upgrade/check 通过。
- **认证授权**：dev header 身份（仅回环）+ OIDC Bearer JWT（joserfc/JWKS，
  校验 exp/iss/aud）；Casbin RBAC，domain=tenant，admin 全权、member 只读。
- **模型网关接缝**：`ModelGateway` 基于 LiteLLM Router，支持 provider fallback
  与重试；暂未接管现有 AgentLoop。
- **CLI/部署**：`spark enterprise` 入口；`deploy/compose.yml`（Postgres 16 +
  Keycloak 26）、`deploy/keycloak/spark-realm.json`、`docs/ENTERPRISE.md`。
  本机无 Docker，容器路径未实跑。
- **依赖**：新增 `enterprise`/`postgres` extras（FastAPI、SQLAlchemy、Alembic、
  Authlib、Casbin、PyJWT、asyncpg、psycopg）。

### Wave 1：成熟生态替换（2026-11-11）

- **token 计数**：`core/tokens.py` 改用 tiktoken（模型已知时 `encoding_for_model`，
  否则 `o200k_base`）；tiktoken 不可用时保留原启发式。
- **工具 schema**：`tools/registry.py` 从 Pydantic 参数模型生成 JSON Schema，
  去掉手写 schema 与运行时校验的重复；22 个扁平工具已迁移。
- **定时任务**：`automations.py` 改用 croniter 校验/计算下次运行，删除手写
  cron 展开与逐分钟试探。
- **Notebook**：`tools/notebook.py` 改用 nbformat 读写；兼容缺失
  `nbformat_minor`/cell id/`execution_count` 的历史笔记本。
- **网页工具**：`tools/web.py` 优先 ddgs 搜索、trafilatura 正文抽取；
  缺失时回退 Bing/HTMLParser，SSRF 防护不变。
- **路径匹配**：`policy.py` 优先 wcmatch；`tools/search.py` 用 wcmatch 匹配 glob、
  用 pathspec 读取 `.gitignore`/`.ignore`。ripgrep 作为 opt-in 加速器留待下一小步。
- **Windows 内置终端**：`pty/__init__.py` 在 Windows 使用 pywinpty（ConPTY），
  Linux/macOS 继续 ptyprocess；`platform_compat.kill_process_tree()` 优先 psutil，
  `tools/bg.py` 复用同一进程树终止实现。
- **依赖**：核心新增 croniter、nbformat；新增 `platform`/`search`/`notebook`/`pty`
  extras（wcmatch、psutil、ddgs、trafilatura、nbclient、pywinpty）。
- **测试**：1437 passed, 3 skipped, 0 failed（497s）；Windows PTY 测试不再跳过。

### 第三轮深挖（规划模块/审查链路/自愈）

- **fix(core)：自动审查不再对鸭子类型 provider 静默失效**——`review_action`
  此前无条件传 `effort="low"`，不接收该参数的第三方 provider 会 TypeError
  落进降级分支：审查永远「放行 + degraded」，只有一条 debug 日志。现与主循环
  同一约定，先探测签名再透传。
- **fix(review)：统计与审查范围同源**——`diff_stats` 按 target（commit/base/
  staged）生成 git 参数，此前固定 `git diff HEAD`，commit/base 模式展示的是
  工作区统计（CLI 只能干脆不显示）；现在三种模式都有真实统计。`parse_review`
  兼容字符串行号（`"line": "42"`），不再静默丢弃。
- **fix(plan)：plan 事件与更新成功绑定**——失败的 update_plan 不再重发旧计划
  （此前会重复触发 approve-plan 审批门）；成功的空计划也会发事件，UI 终于
  可以清屏；TUI 渲染「(已清空)」。
- **fix(selfheal)：diagnose 恢复只读契约**——不再顺手 mkdir SPARK_HOME（看一眼
  健康报告就把目录建了，home.missing 自动修复项因此永远无法触发）。
- **test**：新增 5 个回归（review×3、plan×2）。

### 第二轮深挖（主循环/取消/崩溃恢复）

- **fix(core)：停止真正生效**——同一轮里已判定通过但尚未执行的工具调用，
  取消后不再执行，改补一条 `cancelled before execution` 结果，保持
  tool_call/result 配对不变量。
- **fix(web)：取消/断连唤醒挂起中的 ask_user**——此前按停止后，问题卡片
  挂起的协程最长要空等 600s 超时才能退出。
- **fix(graph)：本轮可见文本写入 checkpoint state**——跨进程崩溃恢复时新
  进程的 `_last_round` 为空，助手文本会静默丢失；旧 checkpoint 缺键时
  回退旧行为。
- **fix(core)：verdicts 与 tool_calls 配对 fail-loud**（zip strict=True），
  状态漂移时显式报错，不再产生孤儿 tool_call。
- **test**：新增取消排队调用回归（tests/test_cancel_round.py）。

### 修复

- **bwrap 能力探测从“存在”升级为“可用”**：非特权容器里 which 命中但命名空间
  挂载被内核拒绝时，workspace 模式每条 shell 都以 exit=1 全灭；现按既有
  “探测 → 降级”契约回落纯策略检查。
- **checkpoint msgpack 白名单**：AsyncSqliteSaver 显式登记
  `spark.models.ToolCall`——上游声明未来版本将阻断未登记类型，且阻断
  方式是静默降级成 dict，只在崩溃恢复低频路径显形。
- **sanitize 主目录替换改为边界感知**：裸 `str.replace` 会把 `/home/ali`
  前缀误伤到 `/home/alibaba`（输出 `~baba`），篡改业务文本并掩盖真实路径。

### 测试

- **e2e_subprocess 显式注入 `SPARK_HOME`**：`spawn_stdio_transport` 按白名单
  过滤环境变量，`SPARK_HOME` 被静默丢弃后子进程回退真实 `~/.spark`——网络盘
  HOME 直接崩溃，本地也污染用户会话库。
- strip-quotes 用例改 raw string，消除 SyntaxWarning。

## [0.13.1] - 2026-11-07（依赖安全修复）

### 修复

- **nltk 3.10.3 → 3.10.4**：修复高危告警；经 `llama-index-core` 进入，走知识库分词路径。
- **setuptools 84.0.0 → 84.2.1**：修复中危告警；构建后端依赖。
- **source-map-js 1.2.1 → 1.2.2**（override）：修复高危告警；仅存在于 `@tailwindcss/node`，不进运行时。
- **minimatch → 11.0.5**（override）：修复 ReDoS 告警；仅经 eslint 与 typedoc，不进运行时。
- **`uv.lock` 版本漂移**：lock 里仍记录 `spark-agent 0.12.0`，冻结安装会解析到旧版本元数据。
- **GitHub Pages 未启用**：`gh-pages` 分支一直在部署，但 Pages 站点从未开启，文档地址 404。

### 已知未修

- `filelock` 13 条告警：`litellm`（含最新 1.108.2）硬锁 `filelock<4.0`，安全修复要求 4.2.1，属上游约束。
  已记录在 `docs/REMAINING.md`，待上游放宽后自动解除。
- `chromadb` 4 条服务端告警：1.5.9 为最新且无修复版本；spark 仅用嵌入式客户端，
  不启动 Chroma HTTP 服务，攻击面不存在（已按 tolerable_risk 关闭并注明理由）。

## [0.13.0] - 2026-11-07（第十四轮：Repo Wiki + 界面一致性收口）

### 新增

- **Repo Wiki（`spark wiki` / 设置→代码文档 / `POST /api/wiki`）**：
  用 tree-sitter 从源码结构生成本地项目文档——每个模块一张符号表，
  行号为真实解析结果，可直接跳转核对。按文件内容哈希增量（未变化的文件
  跳过重建），产物可重复、可 diff、可离线生成；`--summarize` 才调用模型，
  且模型失败降级为纯结构文档而不是中止。`verify_citation()` 在写盘前校验
  `file:line`，拒绝悬空引用。产物落 `.spark/wiki/`（已 gitignore），源文件
  只读。17 条测试覆盖行号正确性、增量、确定性、配额、路径隔离。

### 修复

- **MCP server 版本号**：`initialize` 曾返回 MCP SDK 的包版本（如 `1.30.0`），
  因为 FastMCP 未设置底层 `Server.version`；现与其它入口一致返回
  `spark.__version__`。
- **快捷键帮助面板跟随用户绑定**：面板曾硬编码 `Ctrl+K/N/\/\``，
  而设置里已支持改键——改键后帮助面板仍显示旧键位。
- **侧聊卡片无法翻译**：`SideCard` 定义了恒等函数遮蔽导入的 `t()`，
  且请求了不存在的 `unavailable` key，导致旁支回答整段不可本地化。
- **Wiki 面板状态与内容不同步**：显示已生成 30 个文件的同时提示
  "还没有生成"；现在打开标签页即加载总览。
- **Wiki 面板回显错误的文件上限**：固定显示表单默认值 240，
  与实际生成使用的值无关；`status` 现回显上次运行的真实参数。
- **Wiki 面板多余的"保存设置"按钮**：该面板即时保存，统一的保存条
  会诱导用户点一个什么都不做的按钮。
- **文档语言与标题不跟随界面语言**：`<html lang>` 与 `document.title`
  曾固定为 `zh-CN`/中文，英文用户看到的文档语言声明与界面不一致。

### 文档

- 新增 `docs/reviews/2026-11-07-ecosystem-audit.md`：同类项目的
  逐项核对结果，含"已具备/本轮新增/明确不做"三档结论与理由。
- `docs/REMAINING.md` 与 `docs/competitive-comparison.md` 同步关闭 Repo Wiki。

## [0.12.0] - 2026-11-04（第十三轮：决策项清零——REMAINING 全表关闭）

用户解除维护约定后，原先标 ⏸ 的五项决策项全部落地：

### 新增

- **A2A 流式（`message/stream` SSE）**：`capabilities.streaming=true`；
  SSE 逐帧下发 `TaskStatusUpdateEvent`（working→completed/failed）与
  `TaskArtifactUpdateEvent`（增量 append，终帧全量替换 `lastChunk=true`）；
  非流式 `message/send` 复用同一执行路径（单一代码源）。11 条测试。
- **主 agent 切换（`/plan` `/build`）**：会话内切 plan-only 只读档 ↔ 常规
  审批档；复用审批档案 override 机制（零配置突变），`/build` 重套活跃档案；
  `approval_changed` 事件同步 UI。
- **侧聊（`/side <问题>`）**：辅助模型一次性作答（small_model 优先），
  带主对话最近请求作背景；不写主对话历史；demo 模式如实降级提示；
  UI 旁支卡片。
- **Goal 长程模式（`/goal`）**：`goals` 表持久化（一会话一活跃目标），
  目标契约每回合注入系统提示词（目标/清单/规则），`goal_update` 工具
  回报进展；**证据门槛**——`complete=true` 必须至少一条 `done` 且带可核查
  `evidence` 的条目，否则拒绝；`/goal status|done|cancel` 收口；UI 目标卡。
- **插件市场 v1（`spark mcp` CLI）**：`search`（live 注册表 + 离线策划索引
  兜底 + 中国可用性标注）/`add`（stdio/remote 一键写配置，必需环境变量
  校验，`--readonly` 最小权限白名单）/`list`/`remove`——CLI 与 Web 设置
  面板同源同一配置面。5 条测试。
- **OS 级沙箱三平台 v1**：Linux bwrap（既有）之外，新增 **Windows Job
  Object**（子进程树 kill-on-close + 8 GiB 进程内存上限；**不做**文件系统/
  网络隔离，能力边界如实写入工具结果 `payload.sandbox`）与 **macOS
  sandbox-exec**（写面收敛 workdir/临时目录 + 可断网）。真实 OS 行为测试
  （kill-on-close、内存超额终止、整树清理）。8 条测试。
- **VS Code 扩展（`vscode-spark/`）**：Open Web UI（起服务/解析端口/复用
  实例）+ New TUI Session + Ask about selection；本地 VSIX 可装
  （Marketplace 上架待签名与发布者账号）。
- **截图自动化（`scripts/screenshots.py`）**：本机 Edge/Chrome headless +
  真实 mock 服务（预置演示会话）重拍 `assets/screenshots/`，README 的 UI
  图恢复与当前版本一致；Web 引导支持 `?onboarding=skip`（自动化测试用）。

### 变更

- REMAINING「需要产品决策」表清零；竞品矩阵 Goal/侧聊/主 agent/插件系统/OS 沙箱五行更新。

## [0.11.0] - 2026-11-04（第十二轮：协议拼图补全 + 生态对标审查）

### 新增

- **A2A Server（`spark a2a-serve`）**：以 Linux Foundation Agent2Agent 标准
  （JSON-RPC over HTTP）把 Spark 暴露给跨编排器委派——Agent Card 发现
  （`/.well-known/agent-card.json`）、`message/send`（非流式 turn + artifact）、
  `tasks/get`、`ping`；按 contextId 分会话（LRU 64 上限），未声明能力按规范
  拒绝。MCP（agent→工具）/ ACP（编辑器）/ A2A（agent→agent）三协议全就位。
  9 条新测试。协议选型与诚实边界见 `docs/A2A.md`。
- **生态对标审查报告**：`docs/reviews/2026-11-04-ecosystem-audit.md`——
  2026-11 协议生态（MCP/A2A/AG-UI/ANP）调研结论、12 个同类项目
  （opencode/codex/Claude Code/aider/gemini-cli/goose/cline/kilocode/Zed/
  Warp/Muse/Continue）逐家学习点映射、7 项批判性发现（含撤回记录）。
- **全功能端到端验证记录**：CLI 16 项 / Web 4 项 / MCP 握手 / A2A 9 项 /
  本地 onefile EXE 构建（166MB，`--version`、web、mock 聊天）全部真实跑通。
- **scoop bucket**（`bucket/spark.json`，autoupdate 哈希取自 Release API digest）。

### 文档

- README：七形态总览、徽章加 scoop、协议边界表述更新、文档索引补 A2A；
  中文 README 同步"七种形态"。
- REMAINING：A2A 流式列为 ⏸（需拍板）；AG-UI 记为明确不做（自有 SSE 已覆盖同语义）。
- 竞品矩阵：安装形态与独有项补 A2A。

### 修复

- 撤回一条误报发现：EXE 首聊 turn_error 系测试环境差异（全新 home + 假 key
  → 默认 openai_compat 真请求失败），复核确认两端行为一致、无缺陷。

## [Unreleased] - 2026-11-03（第十一轮：REMAINING「可直接做」清零）

### 新增

- **`spark uninstall` 卸载命令**：打印 `~/.spark` 全部数据落点清单（含逐项大小）→
  确认 → 删除；`--list` 只看不删、`--yes` 跳过确认。内置守卫：`SPARK_HOME`
  指向家目录本身或盘符根时拒绝执行——环境变量写错不该有能力删掉整个家目录。
  Spark 不自建 venv，Python 包请用 `pip uninstall spark-agent` 移除。
- **scoop 分发**：仓库自带 scoop bucket（`bucket/spark.json`），支持
  `scoop install <manifest-url>` 或 `scoop bucket add spark <repo>`；manifest
  带 `autoupdate`，哈希自动取自 GitHub Release API 的 asset digest。
- **TUI 状态栏定制**：`agent.status_items` 有序项配置（model/base_url/key/
  approval/sandbox/workdir/session），空 = 默认顺序；未知项静默跳过、目录为空
  自动省略，错配置不会撑破状态栏。仅在定制过时写入 config.toml。
- **压缩提示词可编程干预**：`<workdir>/.spark/prompts/compact.md`（项目级）→
  `~/.spark/prompts/compact.md`（全局）→ 内置 5 段结构化提示词，逐级回退；
  空文件视为未提供；自定义提示词同时作用于摘要与多轮合并两条路径。

## [Unreleased] - 2026-11-03（第十轮：子会话导航）

### 新增

- **子会话导航（子代理树）**：并行 `task` 派发的子代理在 UI 里终于"看得清是谁在干什么"。
  `subtask_start` 事件现在携带声明式子代理名（`agent`）与任务描述（`prompt`）；
  ToolBlock 的子代理轨迹卡片显示声明名徽章、任务描述（两行截断、悬停看全文）、
  思考量与工具调用计数，进行中状态带扫光进度条。补 `subtask.toolShort` 中英文文案，
  `ChatEvent` 类型同步 `agent?: string` 字段。

## [Unreleased] - 2026-10-25（第九轮：全面 UI 走查与前后端 1:1 审计）

### 前端（UI 走查修复）

- **斜杠补全空态修复（真 bug）**：`Composer` 中空态提示代码（`composer.slashEmpty`）此前被
  `slashOpen = slashQuery !== null && slashItems.length > 0` 的外层条件挡住，永远不可达——
  无自定义命令时输入 `/` 无任何反馈。改为 `slashOpen` 只判前缀，空态可正常渲染；键盘处理
  加 `slashItems.length > 0` 守卫，避免空态时 Enter 被吞掉无法发送消息。截图验证：无命令时
  显示「没有匹配的自定义命令（.spark/commands/*.md）」，有命令时菜单正常、Enter 可应用。
- **终端连接失败文案去平台误导**：`terminal.connectFailed` 原先写死「Windows 不支持，
  或 pty extras 未安装」，在 Linux/macOS 上同样显示，误导排障。改为平台中性的说明
  （POSIX 需 pty 支持；Windows 需额外 pty 组件），中英文同步。

### 走查（未改代码，结论性记录）

- 对运行中的 Web UI 做了 25 张截图走查（已归档 `docs/screenshots/ui-walkthrough/`）：
  设置 12 个 tab（模型 / Agent / 权限 / 展示 / 记忆 / MCP / 技能 / 提示词 / 插件 / 快捷键 /
  知识库 / 项目）、命令面板、快捷键帮助、文件查看器、网页阅读、内置终端、检查点回滚
  （消息时间轴 + 回退按钮）、Turn 回放、斜杠补全空态与命令菜单、@ 引用菜单、内联 shell
  命令卡片（`!ls` 退出码 0）、保存设置成功提示。全部渲染正常。
- **前后端 1:1 覆盖审计**：后端约 45 个 REST 端点全部在前端有对应调用与观测/修改入口
  （清单见 `web/src/api.ts` 与各组件）；`/api/diagnostics`、`/api/selfheal` 由设置弹窗的
  「环境体检 / 一键修复」使用，`/api/history`、`/api/timeline` 由检查点回滚的时间轴使用。
- 知识库 tab 格式说明「支持 … toml」经复核为正确文本（截图 OCR 曾误读为 "tomi"），无需修复。

## [Unreleased] - 2026-10-25（第八轮：前端质量与测试密封）

### 前端

- **统一确认对话框**：新增 `ConfirmDialog`（状态在 `uiStore.requestConfirm`），替换三处
  原生 `window.confirm`（删消息 / 删命令 / 删技能）——移动端与 WebView 下样式统一、
  键盘焦点可控、支持 Esc 取消。
- **执行图不再造假**：移除静态样例数据兜底与误导性注释（事件流此前已接入真实轨迹）；
  无轨迹时显示诚实空状态（`graph.empty`），清理 `graph.sample.*` 与 `graph.edge.delegate` i18n 键。
- **lint 工具链修复**：`npm run lint` 此前因 eslint 不在 devDependencies、无配置文件、
  且脚本使用 eslint 9 已移除的 `--ext` 参数而完全不可用；现引入 eslint 9 flat config +
  typescript-eslint + react-hooks，全量 0 error 0 warning（顺手修出一个真实的
  `react-hooks/exhaustive-deps` 问题：SettingsModal 挂载时初始化 effect 的解释性豁免）。
- **CI 前端门禁**：新增 `frontend` job（eslint / tsc+vite 构建 / **提交的 dist 与全新构建
  字节比对**），前端从零门禁到全绿，杜绝 dist 静默漂移。

### 后端与测试

- **SSRF 白名单测试密封化**：`test_web_fetch_allows_public_hosts` /
  `test_public_targets_still_allowed` 此前依赖真实 DNS，在任何有 DNS 拦截的网络
  （VPN / 企业代理 / 国内访问 GitHub）都会误红；改为 stub `getaddrinfo` 返回固定公网
  地址，测试守卫逻辑本身而非网络。
- **修复未 await 协程告警**：`test_acp_server.py` 的 12 处 `*_conn.close()` 补 `await`
  （此前 WebSocket 实际未关闭，并持续产生 `RuntimeWarning`）。
- **LF 规范化补完**：上一轮「object store 全量 LF」漏掉了 README / CHANGELOG / 审查文档 /
  `build_promo.py` 与 dist 的 html/svg（CRLF 提交），而构建产物为 LF，导致工作树永久
  脏、CI 产物比对无从谈起；本次全部转 LF，重建后 dist 字节一致。

### 验证

- pytest 1086 通过 / 5 跳过，覆盖率 78.33%（门槛 75%）；ruff / mypy / lint-imports /
  bandit（项目配置）全绿；前端 lint / tsc / build 全绿；重建 dist 字节级一致。

## [0.10.0] - 2026-10-23

### 新增（第七轮：分发、审查、协作闭环）

- **一键安装**：`scripts/install.sh`（Linux/macOS）与 `scripts/install.ps1`（Windows）。
  取最新 Release → 强制校验 `SHA256SUMS` → 解压到用户目录 → 建软链/PATH。
  支持 `SPARK_VERSION` 固定版本、`SPARK_MIRROR` 内网镜像、`SPARK_INSTALL_DIR` 自定义目录；
  不 sudo、不改系统目录，校验缺失或不匹配立即中止。
- **自动更新**：`spark update [--check] [--force]`（新模块 `src/spark/updater.py`）。
  https-only、SHA256 强校验、原子替换并保留 `*.old` 备份；`doctor` 默认带 24h 缓存的
  新版本提示，`[updater] mode = "off"` 可关。源码/pip 安装会明确引导到 `pip install -U`。
- **`spark exec --json`**：NDJSON 事件流，每行一个 `TurnEvent`，末行
  `{"type":"final","text":…}`；错误行 `{"type":"turn_error"}`。对齐全 `codex exec --json`。
- **`spark review`**：AI 审 AI。默认审未提交 diff，支持 `--commit` / `--base`；
  审查回合强制 `plan-only` + `sandbox-only`，输出 severity-ranked findings，`--json`
  供 CI，存在 high 时退出码 2。新增 `src/spark/review.py`（diff 收集/截断、JSON 解析降级、渲染）。
- **`!cmd` 内联 shell**：Web 输入框以 `!` 开头直接在工作目录执行（`POST /api/shell`），
  复用 run_shell 的 workdir 限定 + 策略检查 + bwrap，结果渲染为本地卡片、**不进模型上下文**。
- **会话分享与导入**：`format=html` 生成零 JS/零外链的自包含分享文件（消息内容全部
  `html.escape`）；`POST /api/sessions/import` 支持导出 JSON 回灌（仅恢复 user/assistant
  文本，拒绝悬空 tool_call，限 500 条/1MB）。
- **桌面通知**：回合结束或等待审批时发 Web Notification，仅页面不可见时提醒；
  设置 → 展示可开（默认关），权限被拒有明确提示。
- **模型热键**：`Ctrl+Alt+M` 循环模型档案（对齐全 opencode `F2/Shift+F2`）。
- **small_model 接线标题生成**：会话标题/关键词走 `provider.small_model`（压缩摘要此前已接）。

### 修复（CI 首次全绿攻坚）

- **Linux SSE 空流**：`_SSESession` 的 peer 断连探测在「刚建连、请求体刚读完」
  瞬间于 Linux 上误判断连，整条流未写出即 abort（Windows 不复现）。建连后
  0.25s grace 窗口内跳过该探测，空闲真实关闭仍可检测。
- **`install.sh` CRLF**：Windows 提交带 CRLF，Linux `dash` 的 `sh -n` 在
  `case ... in` 处报语法错；脚本 LF 化并在 `.gitattributes` 锁
  `*.sh eol=lf` / `*.ps1 eol=crlf`。
- **发布冒烟误报**：`build-exe`/`release` 的 grep 锚定整行，而 `spark version`
  先打印品牌 banner；改为匹配版本子串。
- **OpenAPI 快照过期**：`8f6abd3` 改 api_spec 未重生成，main 的 quality job
  长期红；已重建。
- **前端 dist 冗余**：清掉 7 个同名不同 hash 的陈旧 chunk，重建后仅 16 个。

### 改进（工程门禁）

- CI 新增 `packaging` job：`sh -n` 校验安装脚本、pyproject 与 `spark.__version__`
  一致性、release.yml 产物清单（含 `SHA256SUMS`）完整性。
- `release.yml`：Windows 同时构建 `spark-desktop.spec` 并打进同一个 zip；新增
  `SHA256SUMS` 生成；构建后对产物跑 `--version` 冒烟（此前只有 build-exe 有）。
- 文档：README / README_zh 重写（一键安装、六形态、Why Spark、质量门禁说明）；
  `docs/competitive-comparison.md` 同步第七轮 12 项并标注剩余真实差距。


### 新增（首次使用引导与帮助入口）

- **首次使用引导**：新组件 Onboarding——三步（配置模型 / 审批模式 / 开始对话），
  第 1、2 步可直达对应设置页；跳过、开始使用或关闭任意路径都会写入
  `spark.onboarding.done`，此后不再出现。服务端启动即自动创建「新对话」会话，
  "无会话"不构成新用户信号，故门条件为 localStorage 标记（老用户升级后见一次）。
- **帮助入口**：Header 新增 `?` 按钮；此前快捷键面板只能按 `?` 键唤起。

### 重构（E/F 级复杂度清零，行为不变）

- 7 个 E/F 级函数全部拆分至 C 级以下（`radon cc -n E` 由 7 → 0，D 级 29 → 22）：
  `save_config`、`restore_workdir`、`AgentLoop._run_tool_body`、`record_turn`、
  `_stream_impl`、`_handle_chat_stream`、`_handle_settings`。deny 审计、密钥脱敏、
  恢复路径校验等安全逻辑逐行原位搬移，1020 条测试回归网全绿。
- `/test` 接线见证测试随拆分升级为模块级源码见证（意图不变）。

### 修复（门禁与协作）

- 上一轮 `api_spec.py` 改动未重新生成 OpenAPI 快照，`export_openapi.py --check`
  在 main 上即失败；补跑 export + orval（`web/openapi.json` +102 行、
  `schema.ts` +75 行）。

### 新增（设置面板对齐 Codex / Claude Code）

- **技能**：设置→技能可列出 `.spark/skills/**/*.md`（与模型侧 `load_skill`
  工具同源），支持新建（带 frontmatter 模板）、查看、删除；改文件名时
  自动同步 frontmatter 的 `name`。
- **提示词**：设置→提示词编辑系统提示词（附内置提示词对照与一键清空），
  并管理 `.spark/commands/*.md` 斜杠命令模板。
- **插件**：设置→插件发现 `.spark/plugins/<name>/plugin.json`，并列出真正
  会执行代码的 hooks。插件本身不提供执行入口——执行一律经 hooks，避免出现
  第二条绕过审批的代码通道。
- **快捷键**：设置→快捷键可逐项改键、冲突检测、单项/全部恢复；绑定存
  localStorage，运行时经 `matchesBinding` 读取（此前 6 个键写死在 App.tsx）。
- **知识库**：新增 `spark.knowledge`（分块 + 中文 bigram BM25，索引 JSON 存
  spark home）。设置→知识库支持导入/删除/检索测试/开关/top-k；每轮按输入
  检索并以 `<knowledge_base>` 只读围栏注入。**不引入 chromadb**：向量路径的
  冷启动 1.5s 不值得，代码/规范类文档上词法足够。

### 修复（远程 MCP 端到端）

- registry 首屏 20 条全是 remote，但一键添加只认 stdio、行编辑器没有 URL
  输入框，且 `streamable_http_client` 返回三元组被按二元解包——远程 server
  从界面到运行全链路不可用。前端补 transport 选择与 URL 输入，解包修正。
- 握手超时抛 `CancelledError`（BaseException），`except Exception` 拦不住，
  一个连不上的远程 server 会让 `spark web` 启动直接崩溃；`close()` 也只吞
  `RuntimeError`，anyio 抛的 `BaseExceptionGroup` 会覆盖真实错误。两处改成
  兜 `BaseException` 并放行 KeyboardInterrupt/SystemExit。
- 新增真实 streamable_http 集成测试（起 HTTP server、发现工具、调用）。

### 修复（桌面产物）

- `spark-desktop.exe`（GUI 子系统、无控制台）双击走的是 `__main__` → TUI，
  等于点开没反应。新增 `src/spark/desktop_main.py` 专用入口直连 `run_desktop`，
  spec 改指向它；失败写 `desktop-crash.log`（windowed 构建没有 stderr 可看）。
- 该入口的 `except Exception` 会吞掉 `typer.Exit`，把所有退出码压成 1；
  已放行。

### 修复（diff 渲染）

- `--- a/x` / `+++ b/x` 被渲染成两行且 `+++` 被裁成裸 `+++`；增删行左右
  两栏行号相同。改为按文件去重合并、左右栏各给自己的行号。

### 修复（设置面板）

- `delete_command` 只按传入的裸名拼路径，而 `save_command` 会自动补 `.md`，
  于是界面上「删除命令」按钮永远 404。两端统一。
- 技能列表显示 frontmatter 的 `name`，与磁盘文件名可以不同，用户找不到对应
  文件；文件名与 `name` 不一致时补显路径，且新建时改文件名会同步 frontmatter。
- 所有保存按钮共用 `common.save`，其字面量是「保存设置」，导致技能/提示词
  编辑器里也显示「保存设置」；拆出 `settings.saveAll` 只给底部全局按钮用。

### 修复（会话搜索）

- 侧栏搜索此前只在已加载的会话标题/keywords/model 里过滤，搜不到聊天正文。
  接上 store 里已有的 FTS5（trigram，中文子串友好），命中显示片段。
  `/api/sessions/search` 已补进 OpenAPI 合同（`/api/openapi.json`）。
- `create_session` 从不写 `sessions_fts`，新会话标题在重启前搜不到。
- `append_message` 无条件用消息前 24 字覆盖 FTS 标题，把用户手改的标题在
  索引里冲掉，自定义标题永远搜不到。

### 新增（消息级操作与分支）

- **消息级删除 / 编辑重发 / 重新生成**：历史接口现在返回真实数据库 id，
  前端按 id 操作；`POST /api/messages/delete` 新增。此前只有检查点面板能
  「回退到某条消息」，聊天区每条消息无法单独处理。
- **会话重命名 / 分叉**：`POST /api/sessions/rename|fork`，侧栏双击或铅笔
  重命名，顶部按钮分叉当前会话并切过去。

### 新增（环境自愈）

- `spark.selfheal`：`diagnose()` 体检工作目录、SPARK_HOME、凭据、运行时
  （python/git/node）、配置文件；`repair()` 只做安全可回滚的修复（建目录、
  写默认配置、备份损坏配置后重建）。
- 新增 `GET /api/diagnostics`、`POST /api/selfheal`，设置 → 项目页有「环境
  体检」卡片，可一键修复；CLI 增加 `spark doctor --fix`。
- **全局配置损坏不再 brick 启动**：`config.toml` 解析失败时备份为
  `config.toml.broken-<时间戳>` 并按默认配置启动，`cfg.recovery_notes`
  记录经过。

### 新增（前端真实性与可观测）

- **执行图接入真实事件流**：改为由 SSE 的 tool_start/tool_end 累积节点与边，
  空会话才显示结构示意（此前无论跑没跑都渲染写死的样例）。
- **演示模式徽标**：provider 因缺 Key 静默降级到 MockProvider 时，顶部显示
  「演示模式」并给出原因，不再让脚本回复看起来像真模型。
- **输入框联网开关**：关闭后本轮不向模型暴露 `web_search/web_fetch`；
  启用审批档案白名单时同样生效。
- **斜杠命令补全**：输入 `/` 弹出项目内 `.spark/commands/*.md` 命令列表，
  支持上下键选择与 Tab/Enter 补全。

### 修复（评审发现的「能点但无效」）

- **`workdir_only` 开关无效**：沙箱只读 `sandbox_mode`，该字段从未被使用。
  开关改为直接驱动 `sandbox_mode`（workspace ⇄ full-access）。
- **full-auto 切回 suggest 后审批被静默拒绝**：`WebApprover` 只在启动时非
  full-auto 才创建，切换后 loop 仍拿 None。改为按需创建。
- **MCP 每-server 环境变量**：`McpServerConfig.env` 新增并贯穿
  save/parse/stdio 启动；注册表一键添加会带出必填变量；保存时 MCP 启动
  失败会明确提示，而不是一律「已保存」。
- **测试连接不能测新端点**：现在允许在提供该端点自己的 Key 时测试任意
  地址；仍拒绝把已存密钥发往其他主机。
- **repo_map 设置不落盘**：`save_config` 补 `repo_map_enabled`/`repo_map_max_chars`。
- 会话列表上限 50 → 200；助手消息操作在触屏设备上常显（原先仅 hover 可见）。

### 修复（Windows 上会真实崩溃的问题）

- **控制台编码崩溃**：`typer.echo("◆ …")` 在 cp1252/cp936 控制台抛
  `UnicodeEncodeError` 并杀死进程——打包出的 EXE 跑 `--version` 即崩。
  包入口统一把 stdout/stderr 重配为 UTF-8 + `errors="replace"`，
  渲染不了的字符降级为 `?` 而不是 crash。
- **Windows 直接 clone 失败**：`providers/aux.py` 是系统保留名，已改名
  `auxiliary.py`；CI 增加 `windows-checkout` 作业永久防回归。

### 修复（测试套件在 CI 上的挂起与假失败）

- **已关闭的 runtime 阻塞满超时**：`get_runtime().run(...)` 在 runtime 已
  close 时不会立刻报错，而是等满 10 秒。conftest 每个用例前后各调一次，
  约 690 个用例 × 2 × 10s = 数小时静默等待。改为立刻抛
  `AgentRuntimeClosed`，并让 `get_runtime()` 自愈替换死实例。
- **后台任务输出只在结束时落盘**：运行中的 job 在 store 里 output 恒为空，
  「重启后仍能看到最后输出」对运行中的任务不成立。改为运行期每 0.5s
  增量落盘。
- **`list_bg_jobs` 不返回 output 列**：与 `get_bg_job` 行为不一致。
- **`detect_env` 每次 spawn 6 个子进程**：每个 Web 测试夹具都触发一次，
  是 CI 变慢的主因。按进程缓存后完整套件 213s → 131s。
- **HTTP 服务非 daemon 线程 + `block_on_close`**：一个卡住的 SSE 连接会让
  `server_close()` 无限 join，进程无法退出。
- **ACP 子进程冒烟在 CI 超时**：冷启动要 import litellm + langgraph +
  textual，Windows runner 上超过 90s。改为 CI 跳过、本地运行。
- **测试会走 HTTP 代理**：会话级 fixture 清空 `*_PROXY` 并固定 `NO_PROXY`
  为回环地址，避免 loopback 请求被转发到不可达代理。
- CI 增加 `timeout-minutes`（quality 20 / test 25 / windows-checkout 10）
  与 pytest `faulthandler_timeout = 600`：死锁会转储线程栈而不是静默烧时间。

### 修复（CI 假红）

- **mirror**：`git push --tags` 遇到已存在的标签会整体失败（"already exists"
  是正常状态），且并发/手动补推后远程 ref 领先 runner 的陈旧 ref。改为
  `--force` 并对标签失败降级为 warning。

### 安全

- **js-yaml 4.1.1 → 4.3.2**：命中多条 advisory，用 npm `overrides` 强制全树
  统一（orval 及其传递依赖都受影响）。

### 测试有效性（本轮重点）

- 引入**变异测试**（`scripts/mutation_check.py`，16 个定点变异）验证断言是否
  真能抓到缺陷。首轮只有 11/16 被抓到，暴露 5 个「测试写了但没测到行为」的
  缺口：引号剥离与 IFS 清洗的分层防护、`git -calias.x=` 紧贴写法、上下文设置
  校验。补齐后 16/16 全部被杀死。
- 修掉一个**真·假测试**：`test_e2e_permission_blocks_write_outside_workdir`
  的断言是 `if tool_end: pass`，拦不拦都通过。
- 新增**真实进程集成测试**：spawn 真实 MCP stdio server，验证握手、工具发现、
  调用返回、untrusted 围栏。
- 新增**真机编码闭环**（`scripts/live_coding_e2e.py`）：真实模型读文件 →
  apply_patch → run_tests，断言测试从 fail 变 pass 且测试文件未被改动。

### 工程

- 成本归账接线：`/api/status` 的 `session_tokens` 返回 `cost_usd`（本地缓存
  或内置快照，绝不联网）。
- hook 事件面从「声明 11 个、实际触发 4 个」补到 17 个且逐个验证触发。
- OpenAPI → 前端 TypeScript 类型（`scripts/export_openapi.py` + orval），
  CI 校验快照不漂移。
- CLI 打开 `--install-completion`。
- README 换成真实模型下的实拍截图（8 张，含 390px 响应式）。

### 安全（受保护路径与只读工具越权通道，2026-10-20）

全量审查 + 三路专家审计共同复现并修复；每条都有对应回归测试
（`tests/test_hardening_regressions.py`，57 例）。

- **shell 检查的多形态绕过**：引号包裹、命令替换 `$(...)`、反引号、`${IFS}`、
  重定向紧贴（`echo x>.git/hooks/pre-commit`）、cwd 相对路径（`../.git/hooks/x`）、
  环境变量间接引用（`%SPARK_HOME%/config.toml`）、cmd 中点引号拼接
  （`"C:\Win"dows\win.ini`）此前都能绕过受保护路径比对。
- **git 控制面**：`.git/{hooks,config,info,objects,refs,modules}` 在任意深度受保护
  （此前只有根目录一层，嵌套仓库与子模块可自由写 hook）；并拒绝 `git -c alias.*`
  （git 自带的任意命令执行）与 `--git-dir` / `--work-tree`。
- **凭据路径**：反斜杠形式（`.ssh\config`）与 env 展开后同样拦截；新增
  `.npmrc` / `.git-credentials`。
- **diagnostics 免审批运行**：拒绝以 `-` 开头的 target（曾可用 `--fix` 改写文件）；
  改用 `-P` 防止仓库自带 `ruff/` 包被 import；tsc 只接受全局二进制（不再经 npx
  执行 `node_modules/.bin/tsc`）。
- **PTY**：`cwd` 收敛到 workdir（此前请求体可传任意绝对路径）。
- **审批分类缓存 **：键从 `tool_call.id` 改为 `(id, name, arguments)` 哈希——
  `openai_compat` 在模型不返回 id 时统一生成 `call_0`，旧键会让只读工具的
  「放行」判定被写工具复用，从而跳过写入审批。
- **误杀回归修正**：词边界改动曾导致 `npm run format`、`echo su`、
  `2>/dev/null` 被拦；深层目标 `rm -rf /home/user` 曾被放行。现按
  「前缀 / 动词+开关 / 命令位」三类分别匹配，`/dev/null` 等设备汇例外放行。

### 修复（真实模型调用被阻断）

- **LiteLLM ≥1.100 的 MCP handler 冲突**：请求带 `tools` 时 LiteLLM 会加载
  `litellm.responses.mcp`，该模块要求 mcp 2.x API（`mcp.types.CacheableResult` /
  独立 `mcp_types` 包），而本项目锁 mcp 1.x——**所有带工具的模型调用**都会以
  `cannot import name 'CacheableResult'` 失败。加 `_skip_mcp_handler=True` 恢复。
- **probe 误判推理型模型**：`content` 为 null 而答案在 `reasoning_content` 时
  被判为「端点返回空」；现同时识别两个字段。

### 修复（正确性）

- `SessionStore.touch()` 省略 title 时违反 NOT NULL；`delete_session` 先删 FTS
  再删 messages（此前子查询总为空，留下孤儿索引行）。
- `/api/settings` 改为全部校验通过后统一提交：被 400 拒绝的请求不再顺手清空
  `protected_paths`；新增 `context` 节（Project 页此前改了存不进）。
- `/api/config` 补 `repo_map_enabled` / `repo_map_max_chars`。
- `__version__` 改从 `importlib.metadata` 读取（此前硬编码 0.9.10 与 pyproject 分裂）。
- OpenAPI 合同层改为可选依赖 `[openapi]`，未安装时端点返回 503。
- 审批事件下发 `tool` 与完整 `arguments`。

### 新增（运行时可恢复性）

- `core/runtime.py`：进程级共享事件循环。此前 `iter_turn_sync` 每轮新建 loop，
  使 `AsyncSqliteSaver` 的连接跨 loop 不可用，A10 崩溃恢复实际一直跑在内存版上。
- `persistent_checkpointer()` 接入 web / TUI；SQLite 加 `synchronous=NORMAL`
  （约省 19ms/轮）与 `wal_autocheckpoint`。
- 生产者改为 `await put` 背压：慢消费者不再独占共享 loop（曾可让所有会话卡死）。

### 新增（界面）

- 响应式：桌面侧栏可拖拽调宽（双击复位、方向键微调、持久化）；移动端设置页
  横向 tab；修复折叠后残留 270px 死区。
- 按需加载：首屏 JS 680KB → 140KB（gzip 214 → ~110KB），弹层首次打开后保持挂载。
- 快捷键：Ctrl+K 移到 App（放在面板内部导致永不触发）；Esc 与停止按钮统一走
  `handleCancel`，并跳过输入框 / 有弹层 / 待审批。
- 停止真正生效：abort 时取消 reader（此前只翻转本地标志，服务端继续跑工具）。
- 审批卡片：`alertdialog` + 焦点移入/恢复、可查看完整参数。

### 工程

- CI 测试矩阵扩为 ubuntu + windows × 3.11/3.12；新增 `windows-checkout` 作业防止
  Windows 保留文件名混入（`providers/aux.py` 已改名 `auxiliary.py`）。
- `build-exe` 冒烟测试不再用 `|| true` 吞掉失败。
- 新增 `docs/FEATURES.md`（按「能力 / 落点 / 用法」组织）与
  `scripts/live_smoke.py`（六阶段真机验证，密钥只从环境变量读）。
- 全量门禁：pytest 752 passed / 5 skipped；ruff / mypy(93 文件) / bandit /
  import-linter 全绿；真机 6/6（step-5-preview、Qwen3.8-Flash-Next）。


## [0.9.11] - 2026-10-19

P2 第六批（**上下文合同 + 双粒度时间机器 + 权限/审批扩展 + MCP 全能力推进**），
同时清偿全部已知测试失败（17 → 0）与代码级技术债。

### 新增

- **上下文合同 replay.jsonl**（`core/replay.py`，schema `spark.replay/1`）：
  - `TurnReplayRecorder` 把每个 turn 归一化为一行 JSON（prompt / 文本 /
    思考流 / 工具轨迹按 start-end 配对 / 审批请求+决策回填 / 压缩 / usage
    / 检查点 id），逐会话追加写 `$SPARK_HOME/replays/<sid>.jsonl`；
  - AgentLoop 双入口接线（`iter_turn` + 崩溃恢复 `resume_pending_turn`），
    approver 包装捕获每次审批决策；`[replay] enabled` 开关，观测路径
    永不阻塞主循环；
  - CLI `spark replay`（会话列表 / turn 明细 / `--json`）；
  - Web `GET /api/replays` + `GET /api/replays/turn?index=`；
  - 前端 **ReplayModal**（列表 + 明细：状态/耗时/工具轨迹/审批/用量，
    命令面板入口，中英双语）。
- **双粒度时间机器**：
  - `store.list_message_briefs` + `GET /api/timeline`（检查点 + 消息统一
    时间轴）+ `POST /api/messages/truncate`（消息级 undo——只回退对话、
    文件保留，与检查点回滚互补）；
  - CheckpointModal 扩展为双区时间轴（检查点回滚 + 消息级 undo）。
- **三层权限作用域**（H3）：
  - `.spark/settings.toml`（项目层，**不可信：仅 deny 被采纳**——克隆仓库
    只能收紧权限，与 `.spark.toml` 消毒同一威胁模型）+
    `.spark/settings.local.toml`（本地层，全量）+ 全局 user 层；
  - `policy.decide` 实现 **deny 跨层优先**（压过 allow_always，防过期授权
    被仓库层 deny 打穿）；扁平 `permission_rules` 向后兼容；
  - `sandbox.is_path_allowed()` 公开化（只读遍历工具过滤保护路径）。
- **审批/行为档案**（H4）：`[[approval_profiles]]`（approval 模式 + 工具
  glob 白名单 + system_prompt + 联动模型档案）；激活经 `_approval_override`
  零配置突变；CLI `spark profile list/use`。
- **自定义命令**（H5）：`.spark/commands/*.md` 文件即命令（`$ARGUMENTS`
  占位），内建 `/test` 永不遮蔽；web / TUI / CLI 三入口接线；
  `GET /api/commands` 供前端。
- **MCP 全能力推进**：
  - **sampling**：server 反向请求补全 → 经 Spark Provider（system 槽 +
    消息序列透传，失败降级 error-stop）；web / TUI / CLI / ACP 四
    surface 挂载；
  - **elicitation**：策略性 decline（hook 点就位，UI 通道后接）；
  - **tools/list_changed**：经 `message_handler` 动态重注册
    （`register_scope`，最小权限白名单在重注册时同样强制）；
  - **resource templates** 聚合进 `mcp_resources` 列表。

### 修复

- **17 个预存测试失败全部清零**，定位到 3 个真实根因：
  - `tarfile.extract(filter=)` 依赖 Python 3.11.4+ 安全回移——checkpoints
    增加 `data_filter` 特性检测 + 降级路径（路径安全仍由
    `_validated_tar_members` 逐成员校验保证）；
  - `run_tests` 在 POSIX 缺 `python` 别名（Debian/Ubuntu 常见）时直接
    失败——增加 `python`→`python3` 回退；
  - 测试用 `sys.executable`（常在 `/usr/bin`）触发 workspace 沙箱保护
    路径拦截——相关测试改用 PATH 上的 `python3`（沙箱行为本身正确）。
- `/api/memory` 查询参数未 URL 解码——前端 `encodeURIComponent` 的中文
  查询以 percent 串进入搜索导致失效，改用 `parse_qs`。
- `WebApprover._tool_call_id` 未初始化（`respond()` 先于 `__call__` 会
  AttributeError）。

### 重构 / 技术债清偿

- **审批判定双路径统一**：抽出 `_classify_call` 公共判定段（hooks →
  plan-only → 规则决策），graph 路径与直调路径收敛到同一执行分派，
  删除约 80 行重复；两条路径的 deny/no-approver payload 文案从此一致。
- **httpx 直连收敛到 LiteLLM 单一通道**：新增 `providers/aux.py`
  （`chat_once` / `embed_once`），memory 摘要/记忆抽取/web 标题生成
  统一走 LiteLLM（模型名规则抽为模块级 `litellm_model_name` 单一真相源）；
  `providers/probe.py` 保留裸 httpx 并注明设计定位（探活 = 原始线上协议
  符合性验证，经 LiteLLM 宽容解析反而掩盖不兼容端点）。
- **glob 匹配统一**：删除 `**`/非 `**` 双 matcher 分支，统一
  `_glob_to_regex`（`*` 恒为单段），语义一致。
- **memory 乐观锁方法公开化**：`_update_content_if_version` →
  `update_content_if_version`（service 跨模块私有访问消除）。
- **文档与实现不符修正**：`trim_recent` 实为字符预算（口径
  `history_budget_chars`），修正 docstring 并同步 context.py 注释；
  memory 搜索双路径重新定位（线性加权是 embedding 关闭时的默认主路径，
  两路均一等公民）；清理 config.py 错位孤立 docstring。
- **`_handlers.py` 862 行拆分**为 5 个域子模块（models / knowledge /
  sessions / chat / settings）+ 共享助手，原文件改为 facade，
  routes.py 与测试导入路径零变动。
- web 会话切换的重复消息构建复用 `_history_messages`。

### 测试

- 新增 5 个测试文件：`test_replay.py`（6）、`test_permission_scopes.py`（6）、
  `test_profiles_and_commands.py`（8）、`test_mcp_p2.py`（6）+
  既有边界测试更新。
- 全量 **687 passed / 5 skipped / 0 failed**（基线 644+17 失败）；
  ruff / mypy / tsc 全绿；前端 dist 已随批重建。

## [0.9.10] - 2026-10-18

P2 第五批（**OTel GenAI 观测**——trace=turn / span=tool / metrics，默认关）。

### 新增

- **GenAI 语义观测**（`otel.py` 扩展 + loop 接线）：
  - `spark.turn` span：`gen_ai.system` / `gen_ai.request.model` /
    `gen_ai.conversation.id` / `spark.turn.resumed`（恢复 turn）；
    终态补 `gen_ai.usage.{input,output,reasoning}_tokens`（reported 优先、
    estimated 兜底，`spark.usage.source` 标明口径）与 `spark.turn.latency_ms`。
  - `spark.tool` 子 span（显式 parent context——LangGraph 节点跨 task，
    contextvar 链不可依赖）：`gen_ai.tool.name` / `spark.tool.arguments` /
    `spark.tool.ok` / `spark.tool.approval` / `spark.tool.duration_ms`。
  - metrics histogram：`spark.turn.tokens`（kind 三分）、`spark.turn.latency_ms`、
    `spark.tool.duration_ms`。
- **默认关语义**：`SPARK_OTEL_ENDPOINT` / `SPARK_OTEL_ENABLED=1` 二者之一
  才启用；未启用全链路 NoOp（零开销）。`SPARK_OTEL_ENDPOINT=inmemory`
  保留给测试。自托管 Langfuse = OTLP 端点直连，不做专属 client。
- **doctor 一键开**：体检新增观测行（未开启 → 开启指引；已开启 → 端点展示）。
- 文档：`docs/OBSERVABILITY.md`（Langfuse 自托管示例 + 语义映射表）。

### 变更

- `otel.py` 改为**自管 provider**（OTel 全局 `set_tracer_provider` 只能生效
  一次，自管模式支持测试与多实例重置）——顺带消除全局单例污染。
- 成本计价暂未进观测路径（`fetch_catalog` 首次调用同步走网络，不能阻塞
  turn 边界）；成本归账随上下文合同/replay 批次落。
- `_run_tool` 拆为观测包装层 + `_run_tool_body`（直调测试兼容不变）。

### 测试

- `tests/test_otel.py`：默认关零 span、GenAI attributes 齐备 + 父子关系、
  工具失败 ERROR 状态、取消无泄漏、恢复 turn 标记，共 5 例。
  全量 **661 passed / 5 skipped**。

## [0.9.9] - 2026-10-18

P2 第四批（**审批 interrupt 化 + turn 级崩溃恢复**——A10 落地）。

### 新增

- **审批经 LangGraph interrupt 挂起**：`approve` 节点（新增，execute 前置）对
  prompt 档工具逐个 `interrupt()`；外层消费 `__interrupt__` 构造
  `approval_needed` 事件 → approver 回调 → `Command(resume)` 继续。三端
  （Web/TUI/ACP）approver 回调语义不变。事件顺序变化（如实记录）：
  `approval_needed` 现在先于 `tool_start`（旧序相反）——interrupt resume
  会重跑节点，tool_start 移到 execute 节点保证恢复重跑零重复；审批卡片
  先于工具卡展示（体验更直观），测试无顺序断言（已核验）。
- **turn 级崩溃恢复（A10）**：`core/turn_store.py`——`TurnCheckpointer`
  （AsyncSqliteSaver + `pending_turns` 自建索引表，同库
  `$SPARK_HOME/checkpoints.db`）/ `MemoryTurnCheckpointer`（子代理与短
  生命周期场景）。per-turn thread_id；turn 正常/错误终态自动清理 checkpoint
  与登记，审批挂起/崩溃保留。
- **恢复 API**：`AgentLoop.resume_pending_turn(thread_id)`（事件流与
  iter_turn 同构，挂起在审批的 turn 重新走审批循环）、
  `list_pending_turns()`、`discard_pending_turn(thread_id)`。
  Web UI 恢复入口随双粒度时间轴批次接线。

### 变更

- graph 拓扑：`llm → approve → execute → llm`（原 tools 节点拆分为审批段与
  执行段）——resume 重跑只发生在 approve（无副作用位点），已批准工具不重复
  执行。`_run_tool` 新增 `verdict` 参数（graph 路径跳过判定段；缺省 None
  保持旧判定路径，直调测试兼容）。
- 恢复语义边界（诚实标注）：**审批挂起点崩溃 → 零重复恢复**（核心场景）；
  **工具执行中崩溃 → checkpoint 停在上一节点边界，恢复后该轮工具重跑**
  （工具副作用非原子，LangGraph 节点级粒度的固有限制）。

### 修复

- 依赖兼容：aiosqlite 0.22 移除了 langgraph-checkpoint-sqlite 2.0.x 依赖的
  `Thread.is_alive()`——显式锁定 `aiosqlite>=0.20,<0.22`。

### 测试

- `tests/test_turn_recovery.py`：审批挂起 → 模拟崩溃 → 全新实例恢复完整执行、
  恢复后拒绝执行、pending 生命周期（登记/终态清除/挂起保留）、discard、
  消息链一致性，共 5 例。全量 **656 passed / 5 skipped**。

## [0.9.8] - 2026-10-18

P2 第三批（**LangGraph 迁入**——A11 消除，ReAct 控制流单一真相源）。

### 变更

- **生产控制流迁入 LangGraph**（拍板方案 A 落地）：
  - `core/graph.py` 重写为**唯一控制流真相源**：`build_turn_graph(loop)`
    编译 `llm → tools → llm → … → finalize / END` 状态机，节点闭包绑定
    loop 实例（state 只放可序列化轻量字段）。
  - `core/loop.py` 删除手写 while 循环：`iter_turn` 前置（turn_start hook /
    压缩 / context / 记忆 / user 入库）不变，控制流段拆为 `_llm_round` /
    `_tools_round` / `_finalize_turn` 三个协作方法，由 graph 节点驱动；
    graph 跨 turn 编译一次缓存于实例。
  - **事件泵用 LangGraph custom stream**（`get_stream_writer`）：节点内实时
    推送 TurnEvent，`astream(stream_mode="custom")` 消费——text/reasoning
    逐 delta 流式语义与旧循环逐字节等价；审批仍走 `approver` 回调。
  - 旧行为全量保留：审批六档、熔断、token 预算、plan-only、approve-plan
    计划门、auto-checkpoint、hooks、子代理（子代理同经新 graph 路径）。
  - 全量 **651 passed / 5 skipped** 零断言改动（行为等价安全网兑现）。
- **修复（迁移牵出）**：ACP 审批桥的 tool_call_id 映射在 LangGraph custom
  stream 下存在时序差（审批请求先于 tool_start 事件被客户端消费）——
  `_ToolIdMap.register` 幂等化 + 审批桥主动 ensure，两侧 id 恒一致。

### 移除

- 旧参考实现 API（`ReActState` / `build_react_graph*` / `_route_tools` /
  `_make_node_llm/tools`）——`test_langgraph_path.py` /
  `test_graph_routing.py` 同步重写为真驱动测试（含防回退接线回归：
  iter_turn 必须经 `build_turn_graph`）。

### 后续（下一批）

- `interrupt` 审批跨进程挂起 + `SqliteSaver` turn 级崩溃恢复（A10，需
  Web 审批桥配合改造）。

## [0.9.7] - 2026-10-18

P2 第二批（ACP server——Spark 进 Zed 的第一扇门）。

### 新增

- **ACP server**：`spark acp --workdir <path>`——以 [Agent Client Protocol](https://agentclientprotocol.com)
  agent 身份在 stdio 上跑 JSON-RPC，被 Zed 等编辑器拉起。基于官方 Python SDK
  （`agent-client-protocol` 0.12.x，锁 <0.13 防协议 1.0 破坏性变更）。
  - 方法面：`initialize`（loadSession + image prompt 能力公告）、`session/new`
    （cwd 校验 + 客户端传入 MCP servers 合并进会话配置）、`session/prompt`、
    `session/cancel`、`session/load`（历史回放 user/assistant 文本消息）。
  - 事件桥：`text_delta`→`agent_message_chunk`、`reasoning_delta`→`agent_thought_chunk`、
    工具 `tool_start/tool_end`→`tool_call/tool_call_update`（ToolKind 映射）、
    `update_plan`→`plan` 条目同步；StopReason 映射（取消/轮次上限/预算/真错误）。
  - 审批桥：审批六档语义不变，`decide()==prompt` 时向客户端发
    `session/request_permission`（allow_once/allow_always/reject_once 三选项），
    `allow_always` 持久化复用 Spark 既有签名缓存；通道异常按 deny 兜底。
  - 会话模式：六档审批投影为 ACP `modes`（suggest/auto-edit/full-auto/plan-only/approve-plan）。
- **分层契约**：`acp_server` 纳入 L4 surface 管控（5 条 import-linter 契约全量更新）。
- **测试**：in-process 双端 socketpair e2e（握手/流式/审批 allow/deny/取消/历史回放）+
  真实子进程 `python -m spark acp` 全链路，共 10 个用例。

### 变更

- `create_provider` 组装路径统一经 `provider_factory` 注入点（测试与嵌入场景可替换 provider）。

## [0.9.6] - 2026-10-18

P2 第一批（agent profiles + 上下文火焰图 + MCP 全能力首批）。

### 新增

- **agent profiles CLI**：`spark agent list` / `spark agent use <档案>`——
  大脑（模型/供应商组合）一键切换并持久化（P2：换大脑不换壳的命令面）。
- **上下文火焰图**：`spark context-profile`——按与 AgentLoop 相同的估算口径
  剖析会话上下文构成（system/agents.md/逐消息），输出条形剖面 + 浪费信号
  （单条消息占比过大、同文件反复读取）；为上下文合同提供数据基础。
- **MCP 全能力首批**：`mcp_resources` / `mcp_prompts` 两个工具（list/read、
  list/get），bridge 四个聚合方法，子代理与 Web/CLI 全链路传播，
  输出经 untrusted 围栏包裹；端到端测试（假 stdio server）一次通过。

## [0.9.5] - 2026-10-18

P1 阶段第二批（工程治理 + OpenAPI 合同 + 诊断闭环 + 文档站 + 发布链）。

### 新增

- **架构分层契约**（import-linter，5 条 forbidden 契约，CI 强制）：foundation /
  providers / support / core / surfaces 五层，下层不得反向依赖上层，表面之间禁止互相 import。
- **OpenAPI 合同层**：`/api/openapi.json` 提供核心只读端点的 OpenAPI 3.1 规范
  （FastAPI 类型化模型定义），`web/orval.config.ts` + `npm run gen:api` 前端 codegen 就绪。
- **diagnostics 工具**（编辑后反馈闭环）：ruff + 可用时的 tsc 诊断结构化回喂上下文，
  注册进工具表与只读白名单。
- **文档站**：mkdocs-material + GitHub Pages 自动部署（docs workflow）。
- **PyPI 发布链**：`publish.yml`（Trusted Publishing/OIDC，push tag v* 或手动触发），
  配置一次即可 `uvx spark-agent`。

### 修复

- 质量门槛新增 import-linter 后，`spark edit -> spark.tools` 的层级违规通过调整
  分层边界解决（edit 与 tools 同层互依为既有事实，边界归入 L2 support）。
## [0.9.4] - 2026-10-18

P1 阶段第一批（供应商韧性 + 真实用量 + 模型目录）。

### 新增

- **usage 真实回填**：流式请求自动带上 `stream_options.include_usage`（不支持的端点
  自动降级），流尾 usage 帧随 `ChatDelta(type="end")` 上抛；`token_usage` 表出现
  `source="reported"` 记录——成本台账从估算升级为实测。
- **fallback 链 + 凭据池**（`[provider]` 新配置 `fallback_models` / `api_keys`）：
  主模型重试耗尽后依次切换备选模型；密钥池逐次轮换；错误信息对全池密钥脱敏。
- **小模型分工**（`provider.small_model`）：压缩/摘要/合并等辅助调用走便宜模型，
  留空 = 主模型，行为不变。
- **开放模型目录**（`spark models [关键词]`）：models.dev 数据源（232+ 供应商、
  价格、上下文长度），24h 本地缓存 + 离线内置快照兜底；`estimate_cost()` 为
  成本台账提供计价基础。
- **repo map git 热度**：代码地图桶内按 git 提及频率排序（aider PageRank 轻量近似），
  高频改动文件优先进入上下文预算；非 git 仓库行为不变。

### 变更

- 压缩摘要的 litellm 兜底路径同样遵循 `small_model` 配置。

## [0.9.3] - 2026-10-17

全量源码走查后的首批修复（审查记录见 `docs/reviews/`）。

### 修复

- **bwrap 沙箱断网（P0）**：`bwrap_command` 的 `--unshare-all` 会连网络命名空间一起隔离，
  与"不隔离网络"的契约相悖——安装了 bubblewrap 的 Linux 在 workspace 模式下
  pip/npm 等网络命令全部失败。补 `--share-net` 保留网络。
- **FTS 搜索索引跨会话误删（P0）**：`delete_messages_after` / `truncate_messages`
  按全局 `rowid > X` 删 FTS，单库多会话时会连带删掉其他会话消息的索引。
  改为按 `session_id` 子查询精确删除，并先于消息本体删除。
- **向量记忆默认配置自相矛盾（P0）**：embedding 请求默认打到 chat 端点
  （如 DeepSeek，无 `/embeddings` 路由），每轮失败静默降级关键词。
  向量检索改为显式 opt-in（`memory.embedding_enabled`，默认关），
  并支持独立 `embedding_base_url` / `embedding_api_key`。

### 变更

- 压缩摘要优先走 Provider 抽象（鉴权/重试单一真相源），litellm 直连降为兜底；
  mock provider 不再误触 litellm 真实路径。
- 工具 dispatch 表随实例构建一次并缓存，不再每轮重建。

### CI

- 新增 `mirror.yml`：主仓 push 后自动同步 Gitee / GitCode / 两 GitHub 镜像仓（分支+标签）。

## [0.9.2] - 2026-10-14

CI 转绿批次：修复 GitHub Actions 长期全红的五个独立根因，全部先在本地以 CI
同款命令复现并跑通（含 Linux 平台模拟与真实 3.11 解释器），再推送。

### 修复（CI / Security）

- **bandit 配置键名错误（CI 长期红的直接原因）**：`bandit.yaml` 使用了不存在的
  `skipped_tests` 键（bandit 只认 `skips`），导致整份跳过列表静默失效，`-ll -ii`
  下 5 个误报持续触发非零退出。A/B 对照实验：旧键 exit=1/5 问题 → 新键 exit=0/0。
- **mypy 在 Linux 上报 Windows 专属常量**：`subprocess.CREATE_NO_WINDOW` /
  `CREATE_NEW_PROCESS_GROUP` 只在 Windows 的 typeshed 中存在，CI（ubuntu）报
  `Module has no attribute`（2 处），而本机 Windows 永远看不到。新增
  `src/spark/platform_compat.py` 用 `getattr(x, name, 0)` 统一取值，四个调用点收敛。
- **配置文件 ACL 判定在 GitHub runner 上误判**：`win_acl_restricted()` 用
  `len(aces) == 1` 精确计数，而 runner 上 `icacls /inheritance:r` 后残留 4 条系统条目
  （runneradmin / SYSTEM / Administrators / OWNER RIGHTS）→ 把已收紧的文件判为不安全，
  导致 2 个 Windows 测试失败、Release 的 Windows 构建整体失败。改为安全目标导向：
  只要 `BUILTIN\Users` / `Everyone` / `NT AUTHORITY\Authenticated Users` /
  `INTERACTIVE` 均不可见即达标（并保留"继承标记 (I) 必须不存在"断言）。
- **Security 的 secret 扫描命令语法错误**：`pre-commit run gitleaks trufflehog` 不被
  pre-commit 接受（它只接受单个 hook id），job 以 usage error 失败而非扫描发现问题。
  拆为两次调用；`uv audit` / `pip-audit` 步骤级补 `continue-on-error`，与 job 级
  "治理看板不阻断合并"的设计意图一致。
- **Release 资产上传冲突 + macOS runner 不可用**：三个平台 job 并发写同一个 Release，
  后到者触发 "Cannot upload asset to an immutable release"；`macos-13` 在免费额度下
  永不获得 runner（实测排队近 2 小时）。改为构建矩阵只产出 workflow artifact，
  再由单一 `publish` job 汇总上传；macOS runner 换 `macos-latest`（产物更名
  `spark-macos-arm64`）。
- **MCP 启动在 Python 3.11 上跨 task 退出 anyio cancel scope（test 3.11 剩余红）**：
  `_start_on_owner` 用 `asyncio.wait_for(conn.start(), timeout=15)` 包裹连接，超时
  会取消承载连接的 task，而 anyio 的 cancel scope 是在该 task 中进入的；之后
  `close()` 在另一 task 里 `aclose()` 抛 `RuntimeError: Attempted to exit cancel
  scope in a different task than it was entered in`（Python 3.11 + anyio 4.x）。
  改为超时内移到 `start()`（`asyncio.timeout`）实施、调用方不再包装；`close()` 对
  已部分展开的 exit stack 吞掉该 RuntimeError（资源已释放），失败日志补
  `exc_info`。真 3.11.15 全量验证：630 passed / 5 skipped。

### 新增

- `src/spark/platform_compat.py`：跨平台 subprocess 标志的安全取值（`CREATE_NO_WINDOW`
  / `CREATE_NEW_PROCESS_GROUP` / `DETACHED_PROCESS`）。
- `tests/test_acl_predicate.py`：4 例锁定 ACL 判定的安全语义（runner 形态应判为已收紧、
  含宽泛用户组应判为未收紧、系统/管理员条目不算宽泛、非 Windows 返回 False）。

### 验证

- 本地 CI 仿真：ruff PASS；mypy（`--platform linux` 与本地 Windows）双平台 PASS；
  bandit exit=0；pytest **630 passed / 5 skipped / 0 failed**，覆盖率 **73.44%**（门禁 65%）；
  gitleaks Passed。

## [0.9.1] - 2026-10-14

开源整理 + 真实模型端到端验证批次。全部改动经真实模型（OpenAI 兼容端点）验证，
全量测试 626 passed / 5 skipped / 0 failed，ruff/mypy 零错误。

### 修复（真实模型测试暴露，mock 永远测不到）

- **环境变量覆盖被配置文件压制**：`ConfigSources` 顺序错误，导致 `.env.example` 宣传的
  `SPARK_PROVIDER__*` / `SPARK_AGENT__*` 写法在有 `config.toml` 时静默失效，与文档承诺
  （环境变量 > TOML）相反。已修正源优先级，并补单元测试锁定。
- **任意自定义模型名无法使用**：`openai_compat` provider 把裸模型名直接交给 LiteLLM，
  后者只能按内置规则推断 provider——`glm-5.3-flash`、`Qwen-*`、以及 README 宣传的
  "任意 OpenAI 兼容 API" 全部报 `LLM Provider NOT provided`。已改为显式按 OpenAI 兼容处理
  （带 `/` 前缀的 LiteLLM 语法仍原样尊重）。
- **检查点回滚遇文件占用直接崩溃**：快照把活跃 SQLite 的 `-wal/-shm` 运行时文件打包，
  恢复时写回撞锁（Windows `OSError: [Errno 22]`）导致整个回滚失败。双层修复：快照侧排除
  这两个后缀（运行时文件、可重建），恢复侧单文件失败收集进 `errors` 且不中断其余文件。
- **MCP `bridge.start` 注册了工具却无法调用**：`ctx.mcp_call` 依赖调用方另行绑定，
  直接调用该 API（脚本/测试）时已注册工具全部以 "Unknown tool" 静默失败。已加兜底绑定。
- **`HookConfig.on_deny` 默认值与 TOML 路径不一致**：pydantic 默认 `warn` 而
  `HookRegistry.from_config` 默认 `block`，通过配置注册的拦截型 hook 实际只警告不拦截。
  已统一为 `block`。
- **`/api/status` 的 `has_api_key` 不识别环境变量**：部署为环境变量供 key 时误显示"未配置"。
- **进程内首次 system prompt 构建多付约 12 秒**：`detect_env` 串行 spawn 6 个版本探测
  子进程，Windows 上每个 2-3 秒。已改为并行探测。
- **`_similarity_clusters` 的 O(M²) 残留**：倒排索引优化后仍保留一段全配对循环，
  把优化完全抵消。已删除。

### 修复（此前批次，本版一并发布）

- **P1-2 旗舰工具 `apply_edit` 未纳入 WRITE_TOOLS**：full-auto / auto-edit 下被策略层
  一律拒绝、权限规则无法按路径约束它。
- **Web 保存设置损坏 MCP 配置**：往返链路丢失 `transport` / `http` / `allowed_tools`，
  在网页里保存一次即把 SSE/HTTP server 降级为 stdio 并清空 P1-9 最小权限白名单。
- **`save_config` 丢字段**：`PermissionRule.tool`（受限规则保存后静默扩大为任意工具）、
  `agent.system_prompt`（用户自定义系统提示词保存即丢失）、`streamable_http` transport。
- **项目级 `.spark.toml` 可注入 `agent.system_prompt`**：未纳入不可信配置剥离清单。
- **`web_search` 缺 `import json`**：成功路径必崩。
- **system_prompt / effort 经 Web UI 全链路不可用**：前端不发送、后端不接收、状态不回传、
  不落盘——HEAD 宣称的功能实际是死的。四层补齐。
- **审批档位三份定义不一致**：`onboard.py` 只认 3 档，`spark init --approval plan-only`
  等被错误拒绝。已统一以 config 为单一事实来源。
- **graph.py 的 interrupt resume 值被丢弃、`deny` 判决被忽略**：LangGraph 参考实现里
  用户点"拒绝"工具照样执行。已修，并配真实 graph 驱动测试。
- **`core/loop.py` checkpoint 失败路径 UnboundLocalError**、**FTS 标题被每条非 user
  消息清除**、**`/api/agents_md` GET 缺路径守卫**、**pty rows/cols 非数字 500**。
- **前端**：Terminal 退出后"按键重启"因 stale closure 永久失效；BrowserModal Enter
  双请求；流断线自动重发导致重复执行同一 prompt（改为不重发 + 提示）；FileViewer 跨行
  语法高亮 token 掉色；`fetchWithTimeout` 监听器泄漏。

### 新增

- **真实模型验证矩阵**：18 项配置面端到端验证（system_prompt 注入 / effort / 六档审批 /
  四档沙箱 / 护栏 / hooks 拦截 / 检查点回滚 / 记忆检索注入 / MCP 自环 / 上下文压缩）。
- **测试补强**：`test_web_port.py`（端口自适应）、`test_prompt_cache.py`（提示词缓存与
  失效）、checkpoint WAL/SHM 排除与恢复容错用例、provider 模型名处理用例、
  重写的 `test_langgraph_path.py`（真实 graph 驱动，替代此前 4 个伪覆盖用例）。
- **`spark/asyncutil.py`**：统一"同步上下文安全运行协程"实现（此前 4 处各自内联）。
- **`spark/tools/_common.py`**：统一文本裁剪实现（此前 3 处）。
- **`docs/competitive-comparison.md`**：与 Qoder / ZCode / opencode / Codex CLI 的
  全功能对比（13 个功能域）。
- **四语 README 真实截图展示区**（中文/English/日本語/Español）。

### 变更

- **端口自适应**：`spark web` / `spark desktop` 省略 `--port` 时从 8000 起自动选择可用端口；
  显式指定被占用时给出明确提示。启动时打印访问地址（此前 logger 无 handler，用户看不到）。
- **缺 API Key 降级**：不再拒绝启动，自动进入演示模式（mock）并提示；在 Web 设置中
  填入密钥后立即切换真实模型，无需重启。`spark doctor` 相应从 `[bad]` 改 `[warn]`。
- **i18n 全量接入**：26 个前端组件接入中英词典（约 290 个替换点、词典 370 键），
  语言切换对全界面生效（含日期/时间 locale 跟随）。
- **系统提示词缓存**：按配置键 + 30s TTL 缓存，写工具执行后主动失效，避免每个工具
  回环重扫全仓（repo_map tree-sitter）。
- **清理退役物**：删除 `requirements.lock`（引用已移除的 fastapi/uvicorn 依赖与不存在的
  `office` extra）、`tests/legacy_root/`（已退役根树的归档测试，从未被收集）、
  `examples/plugins/`（目标契约已不存在、无法加载的死示例）、过期的历史审计文档；
  相应更新全部引用。
- **版本统一**：`pyproject.toml` 与 `spark.__version__` 统一为 `0.9.1`（此前分别停留
  在 0.1.0，与 CHANGELOG 的 0.8.0 及发布 tag 脱节）。
- **Windows EXE 重建**并冒烟通过（真实模型对话 + shell 工具调用）。

## [2026-10-10] 双树收敛收尾（历史记录）

### 变更（双树收敛收尾，2026-10-10）
- **根树退役，`src/spark` 成为唯一主干**：pip 发行（`packages.find where=["src"]`）、pytest
  （`pythonpath=["src"]`）、CI 门禁（ruff/mypy/bandit 改指 `src/spark`）三方一致；根目录
  `spark/` 平行实现删除，其专属测试中不可迁移部分归档至 `tests/legacy_root/`（作为 P1 能力
  回迁规格），可迁移部分已适配 src 随主测试运行。
- **修复 `core/compact.py` 的 `MessageRole` 双轨覆盖**：`from spark.models import MessageRole`
  覆盖 llama_index 的同名枚举，导致 `.SYSTEM` AttributeError、16 个测试文件收集失败；改用
  `LlamaMessageRole` 别名。此项修复后全量测试才具备可运行性。
- **质量门禁全绿**：`pytest -q` 362 passed / 2 skipped，覆盖率 TOTAL 72%（门禁 65%）；
  `ruff check src/spark tests` 0 错误；`mypy src/spark` 0 错误（48 文件，含 Windows 平台专属
  属性的窄类型 ignore）；`bandit -r src/spark -ll -ii` HIGH/MED 0。
- **P0 出口脱敏接入**：`src/spark/sanitize.py`（密钥/主目录/堆栈三层）接入 SSE 事件出口与
  错误文案，新增 `tests/test_sanitize.py` 13 例；修复"无 secrets 时整体跳过"缺口——主目录与
  堆栈属无条件脱敏项，本机无令牌/无 API Key 时同样生效。
- **PyInstaller 打包改指 src 树并实测**：两个 spec 入口改为 `src/spark/__main__.py`，清理根树
  幽灵 hiddenimports（uvicorn 子系统、`spark.web.api_*`、subagent/codeindex/plugins、tomlkit、
  docx/openpyxl/pypdf），补 textual/chromadb/pywebview+clr，`prompts` 入包；构建脚本改装
  `.[dev,desktop]`。PyInstaller 6.22.3 目录构建 + 二进制冒烟通过（version / exec 全链路 /
  doctor / web 启动）。litellm 经 `importlib.resources` 读取的包内 JSON（模型价格表、tokenizer
  词表、endpoints 配置）由 spec 解析期递归收集，chromadb 经 Settings 配置项动态 `import_module`
  解析的默认组件类（含 Rust 绑定扩展 `chromadb_rust_bindings`、telemetry posthog）显式入
  hiddenimports——这两类是冻结首启 ModuleNotFoundError/FileNotFoundError 的根因。
- **依赖声明修正**：`chromadb` 锁 `<1.0` 在 Python 3.13 不可安装，放宽为 `>=1.0`；`pytest-cov`
  补入 dev extras（此前 CI 的 `--cov-fail-under` 步骤装不到即失败）；新增 desktop extra
  （pywebview>=5.0，Windows 用系统 WebView2）。
- **Web 安全立场**：`spark web` 默认仅监听 127.0.0.1，非本机调用方必须携带每进程控制令牌
  （`X-Spark-Token`）；暴露局域网需显式 `--host 0.0.0.0` 并在启动时打印令牌。

### 新增
- **桌面形态 `spark desktop`**：pywebview 原生窗口壳，与 `spark web` 共用同一 HTTP 服务与
  鉴权；固定绑定回环，窗口关闭优雅停机（server shutdown → server_close → store.close）。
- **`sanitize_sse_payload`**：src 树事件协议（text/result/approval/tool/data）的浅递归脱敏，
  深度限 3 层，结构与非字符串不动。

## [0.8.0] - 2026-09-30

### 前端架构（本轮重点）
- **ES Modules 化**：14 个前端模块显式 `import/export`，单入口 `<script type="module" src="js/main.js">`；删除脚本顺序依赖与 `__DEP_REQS` eval 自检。
- **Web Components 组件层**：8 个自定义元素（消息/工具卡/计划卡/思考块/会话卡/MCP 行/记忆行/git 行），模板与行为分离，支持「先 setData 后挂载」。
- 修复 `autoGrow()` 从未定义导致 `@` 文件补全失效的潜伏 bug；修复 `window.termState` 恒 undefined 导致切会话不刷新终端组。
- 设计令牌系统 + `@layer` CSS 组织 + 原生 `<dialog>` + 毛玻璃 + 智能滚动 + 渲染节流 + 主题跟随系统。

### 后端与结构
- `loop.py` 801→353 行拆分：routing / truncation / compaction / prompt / execution 五个兄弟模块。
- `server.py` 877→308 行拆分：api_common / api_config / api_sessions / api_data 四域路由。
- 审批支持逐文件勾选（`apply_patch` 透传 files 过滤）；终端按会话分组（`(sid, tab_id)` 复合键）。
- wheel 修复前端资源缺失；`uvicorn[standard]` 依赖修复；vendor NOTICE 版权声明；启动依赖自检。

### 修复
- 进程组 SIGKILL（截断重试不残留子进程）；IO 线程化；上下文截断误判；MCP 输入双向写回。

## [0.7.0] - 2026-09-20

### 新增
- MCP 接入：stdio + Streamable HTTP 双传输，懒启动，写类工具进审批门。
- Prompt 注入防护：中英文特征正则，命中注入安全警告标记。
- 内置持久终端（pty），按会话隔离目录；Textual TUI。
- 轻量代码索引（Python ast 精确 / 其他语言行级）、检查点与写前自动快照。

### 修复
- 免登录令牌逻辑；Windows PTY 平台兼容；配置 ACL（600/icacls）。

## [0.6.0] - 2026-09-10

### 新增
- 首个可用版本：AgentLoop 事件流（计划→模型→工具→审批→执行）、会话存储、长期记忆（SQLite+FTS5）、用量成本记账、国产模型 PRESETS、子 agent（explore/general）、轻量插件点。

> AI生成