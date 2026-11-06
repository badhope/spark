# 功能总览（以实现为准）

> 本文替代以往"愿景式"功能列表：每条都能在代码里找到落点，并在
> `tests/` 中有对应断言。数字会变，能力不会。

## 一、能做什么

| 能力 | 实现落点 | 怎么用 |
| --- | --- | --- |
| 与模型对话（流式） | `providers/openai_compat.py` | Web / TUI / CLI 任一入口输入即可 |
| 让模型读写文件 | `tools/fs.py`、`edit/` | 模型自行调用；写入受沙箱与审批约束 |
| 让模型执行命令 | `tools/shell.py` | 命令先过 `sandbox.check_shell`，再按审批档放行 |
| 让模型跑测试并自我修正 | `tools/gitops.run_tests` + `core/loop.py` | 模型调用 `run_tests`，失败输出自动回灌 |
| 让模型操作 Git | `tools/gitops.py` | status/diff/log/branch/add/commit；push/pull 明确不提供 |
| 任务即分支（自动提交） | `git_memory.py` | `task_begin` → 绿测后 `task_step_commit` → `task_finish` |
| 联网查资料 | `tools/web.py` | `web_search` / `web_fetch`，含 SSRF 防护 |
| 接第三方能力 | `tools/mcp_bridge.py` | stdio / sse / streamable_http 三种传输；也可从内置 registry 一键添加，工具以 `mcp__<server>__<tool>` 出现 |
| 子代理并行 | `core/loop.py:spawn_subtasks_parallel` | 模型调用 `task`，最多 4 路并行 |
| 声明式专家代理 | `agents.py` + `.claude/agents/*.md` | 放一个 md 文件即多一个可委派的专家 |
| 项目技能 | `skills.py` + `.spark/skills/*.md` | 放文件即生效，模型按需 `load_skill`；设置→技能可增删改 |
| 自定义斜杠命令 | `slash.py` + `.spark/commands/*.md` | `/name 参数` 展开为提示词；设置→提示词可管理 |
| 长期记忆 | `memory/` | 自动抽取 + 关键词/可选向量检索 + 网页面板管理 |
| 项目知识库 | `knowledge.py` | 导入文档（md/txt/rst/json/yaml/csv/toml），BM25 检索后按轮注入只读参考 |
| 会话全文检索 | `store.search()` + FTS5 | 侧栏搜索命中聊天正文，结果带片段 |
| 回滚 | `core/checkpoints.py` | 每轮写操作后自动快照，可整体回滚 |
| 崩溃恢复 | `core/turn_store.py` + `core/runtime.py` | 审批中断的 turn 落盘，重启后仍可列出并恢复 |
| 可观测 | `otel.py` | `SPARK_OTEL_ENDPOINT=...` 后 trace=turn / span=tool |
| 逐轮复盘 | `core/replay.py` | 每 turn 一行 JSON，CLI 与 Web 都可查 |
| 定时任务 | `automations.py` + 设置→定时任务 | cron 触发一段提示词；进程不在线时下次启动补跑一次；忙则跳过不排队 |
| 额外目录 | `sandbox.py:extra_roots` | `--add-dir DIR` 或配置 `agent.extra_dirs`；只放行文件读写，不放宽 shell |
| 写后格式化 | `tools/formatting.py` | `agent.format_on_write = true`；只调用项目已有的 ruff / prettier，失败不阻断写入 |
| 符号定位 | `tools/symbols.py` | `find_symbol` 工具：tree-sitter 找定义 + 文本扫引用，免审批只读 |
| 自动审查 | `core/review.py` | `agent.auto_review`：写/执行前用独立模型审一次，block 即拒绝；默认关 |
| 断网执行 | `tools/shell.py` | `agent.network_access = false`：bwrap 下跑空网络命名空间；不可用时如实告警 |
| 配色主题 | `web/src/theme.ts` + `index.css` | 5 套强调色（Ember/Ocean/Forest/Violet/Mono），与明暗正交 |
| 代码文档（Repo Wiki） | `wiki.py` + `spark wiki` + 设置→代码文档 | 用 tree-sitter 从源码结构生成本地文档：每个模块一张符号表，行号可核对；按内容哈希增量，可重复可 diff；`--summarize` 才调模型 |

## 二、形态与入口

```
spark                     # 终端 TUI
spark web --workdir DIR   # 浏览器 UI（默认只监听 127.0.0.1）
spark desktop             # 原生窗口（pywebview）
spark exec "任务"          # 一次性执行，适合脚本/CI
spark acp                 # 作为 ACP agent 供 Zed 等编辑器拉起
spark mcp-serve           # 把内置工具暴露给别的 MCP 客户端
spark --version           # 版本号（与 pyproject / PyPI 同源）
```

## 三、安全模型（三层，逐层收窄）

1. **审批档**：六档，从 `plan-only`（只出计划）到 `full-auto`。
   默认 `suggest`：写文件与执行命令都要确认。
2. **沙箱档**：`sandbox-only` / `workspace`（默认）/ `full-access` / `unrestricted`。
   路径越界、受保护路径（系统目录、`~/.spark`、Spark 自身源码、`.git/hooks`）一律拒绝。
3. **权限规则**：`[[permission_rules]]` 按"工具 + 资源"通配匹配，
   支持三层作用域（user / project / local），**deny 跨层优先**，
   克隆下来的仓库只能收紧权限、无法给自己放行。

补充约束：

- 审批授权绑定到 `(工具, 参数)` 的签名，批准一次某命令不会连带批准另一条。
- shell、worktree、task 类工具**永不允许"始终允许"**。
- 项目级 `.spark.toml` 会被剥离 MCP / hooks / 模型档案 / 权限规则 / 凭据。
- 只有 `$SPARK_HOME/.env` 能提供配置，仓库里自带 `.env` 无效。
- 所有对外输出（SSE、错误文案）经过脱敏：密钥、家目录、堆栈。

> 边界诚实说明：shell 检查是 token 级启发式，能挡住常见的越界与危险命令，
> 但挡不住刻意构造的绕过（例如用解释器动态拼接路径）。
> 对凭据敏感的场景请用 `suggest` / `auto-edit`，不要开 `full-auto`。

## 四、Windows 与 Linux 的差异处理

差异集中收敛在 `src/spark/platform_compat.py`，其余模块只调用它：

| 关注点 | Windows | Linux / macOS |
| --- | --- | --- |
| 进程树终止 | `taskkill /F /T` | `start_new_session` + `killpg` |
| 子进程黑框 | `CREATE_NO_WINDOW` | 无此概念（标志为 0） |
| 默认 shell | `%COMSPEC%`（cmd.exe） | `$SHELL`，回退 `/bin/sh` |
| Python 可执行名 | `python` | `python3`（自动回退） |
| 内置终端 | 不支持，接口返回 501 + 明确说明 | `ptyprocess` |
| 保留文件名 | 拒绝 `CON/PRN/AUX/NUL/COM1-9/LPT1-9` | 同样拒绝（保证可移植） |
| 破坏性命令 | 额外匹配 PowerShell 别名（`ri`/`del`/`rd` → `remove-item`） | POSIX 名称 |

**安装与克隆**：仓库中不存在任何 Windows 保留文件名（`aux.py` 已改名
`auxiliary.py`），Windows 上可直接 `git clone`。

## 五、界面

- **三态主题**：亮 / 暗 / 跟随系统，切换即时生效并记住。
- **响应式**：≥1024px 侧栏常驻且可拖拽调宽（双击复位，方向键微调）；
  窄屏侧栏变抽屉，设置页 tab 变横向可滚动。
- **按需加载**：终端（xterm）、执行图（xyflow）、设置、回放等重面板首次打开才加载；
  首屏 JS 从 680 KB 降到 141 KB。
- **键盘**：`Ctrl+K` 命令面板、`Ctrl+N` 新会话、`Ctrl+\` 切换侧栏、
  `Ctrl+` 加反引号开终端、`Esc` 停止生成、`?` 帮助。六个全局键均可在
  设置→快捷键里改键并做冲突检测。
- **审批卡片**：展示工具名、摘要、diff，并可展开查看完整参数。
- **多级导航**：会话侧栏 → 会话 → 消息内工具块折叠 → 设置分 12 页签
  （模型 / Agent / 权限 / 展示 / 记忆 / MCP / 技能 / 提示词 / 插件 / 快捷键 /
  知识库 / 项目），默认只呈现一层。
- **会话检索**：侧栏搜索走 FTS5（trigram，中文子串友好），命中消息正文时
  在标题下显示片段。

## 六、如何验证它真的能用

```bash
# 离线：全部单测（约 2.5 分钟）
python -m pytest tests -q

# 静态门禁
ruff check src tests
mypy src/spark --ignore-missing-imports
bandit -r src/spark -ll -ii -c bandit.yaml
lint-imports

# 真实模型全链路（需要你自己的 key，不会写进任何文件）
export SPARK_TEST_BASE_URL=https://your-endpoint/v1
export SPARK_TEST_API_KEY=sk-...
export SPARK_TEST_MODEL=your-model
python scripts/live_smoke.py
```

`live_smoke.py` 分 6 个阶段各自独立超时：非流式补全、流式增量、
工具写文件、工具执行命令、审批拒绝生效、上下文计数。
任一阶段卡住不会拖垮整轮，最终给出 `x/6` 的结论。
