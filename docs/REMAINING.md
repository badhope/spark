# 剩余能力清单（可追踪）

> 这份文件是 `docs/competitive-comparison.md` 的**执行视图**：矩阵回答"差在哪"，
> 这里回答"还剩哪些、为什么没做、做了算完成的标准是什么"。
> 每次关闭一项就在这里改状态，避免"文档说要做什么"和"实际做了什么"再次分叉。

状态：✅ 已交付 ｜ 🚧 进行中 ｜ 📋 已排期 ｜ ⏸ 需产品决策 ｜ ❌ 明确不做

## 已关闭（本轮及之前）

| 能力 | 状态 | 落点 |
| --- | --- | --- |
| 定时任务 | ✅ | `automations.py` + 设置→定时任务 |
| 写后格式化 | ✅ | `tools/formatting.py`，`agent.format_on_write` |
| 额外目录 `--add-dir` | ✅ | `sandbox.py:extra_roots`，`agent.extra_dirs` |
| 断网执行 | ✅ | `agent.network_access` + bwrap 空网络命名空间 |
| 自动审查 | ✅ | `core/review.py`，`agent.auto_review` |
| 符号定位 | ✅ | `tools/symbols.py`，`find_symbol` |
| 配色主题 | ✅ | `theme.ts` + `index.css`，5 套与明暗正交 |
| 调试诊断 | ✅ | `web/_handlers_debug.py` + 设置→诊断 |
| 权限/审批治理写接口 | ✅ | `web/_handlers_governance.py` |
| 消息级 undo 带文件还原 | ✅ | `/api/messages/truncate` `restore_files` |
| 子代理独立模型 | ✅ | `DeclaredAgent.model` |
| 子会话导航（子代理树） | ✅ | `subtask_start` 事件携带 `agent`/`prompt`（`core/loop.py`）+ ToolBlock 子代理轨迹卡片（声明名/任务描述/进度条） |
| `spark uninstall` 卸载命令 | ✅ | `cli.py uninstall_cmd`：清单 + 确认 + 家目录守卫；`--list` / `--yes`；文档见 INSTALL.md |
| 包管理器分发（brew/scoop） | ✅ | `bucket/spark.json`（scoop bucket，带 autoupdate，哈希取自 Release asset digest）；brew 需独立 tap 仓库，仍未做 |
| 状态栏定制（TUI） | ✅ | `agent.status_items` 有序项（`tui/welcome.py:status_text`），未知项跳过、目录空自动省略 |
| 压缩提示词可编程干预 | ✅ | `.spark/prompts/compact.md`（项目）→ `~/.spark/prompts/compact.md`（全局）覆盖内置提示词，摘要与合并两路径都生效 |
| A2A Server（Agent2Agent 协议） | ✅ | `spark a2a-serve`：Agent Card + message/send + tasks/get（`a2a_server.py`，9 测试）；流式列为 ⏸ |

## 待做（按"能不能直接动手"排序）

### 可以直接做（无产品决策、成本可控）

| 能力 | 状态 | 完成标准 |
| --- | --- | --- |
| （空） | — | 上表四项已全部关闭；新的可直接做项出现时在此登记 |

### 需要产品决策（会改变交互形态）

| 能力 | 状态 | 为什么不能自己决定 |
| --- | --- | --- |
| 侧聊 / 旁支（`/side`） | ⏸ | 引入"一条主对话 + 多条分支"的状态模型；会话表结构要改，且 UI 需要新的导航层。做错比不做更糟 |
| Goal 长程模式（`/goal`） | ⏸ | 需要定义"目标—证据—验收"的推进协议；没有证据门槛的 goal 只是自动循环，会烧钱 |
| 主 agent 切换（build/plan 双档） | ⏸ | 与现有 approval profile 语义重叠，合并还是并存需要拍板 |
| 插件市场 | ⏸ | 涉及签名、安装权限、沙箱边界。没有签名机制的市场是供应链攻击面 |
| A2A 流式（message/stream SSE） | ⏸ | 当前 A2A 非流式已满足编排器主路径；流式要引入 SSE 事件转发与取消语义，成本/收益需拍板 |

### 成本明显更高

| 能力 | 状态 | 估算 |
| --- | --- | --- |
| Repo Wiki（生成式代码文档） | 📋 | 复用 tree-sitter + LLM 摘要，产物落本地带 file:line 溯源；需要分块、增量更新、引用校验 |
| 三平台 OS 级沙箱 | 📋 | macOS Seatbelt / Linux Landlock / Windows job object 各一套；当前只有 bwrap（Linux） |
| IDE/ACP 深度集成 | 📋 | ACP server 已有，缺的是 VS Code 扩展与自动安装 |
| 内置可驱动浏览器 | 📋 | 现在的 web_fetch 是只读阅读，不是可点击/填表的浏览器 |
| Git PR 工作流 | 📋 | `pr <num>` + GitHub Action；需要 token 权限模型 |

## 明确不做

| 能力 | 理由 |
| --- | --- |
| 云端沙箱 / 账号体系 / 移动 App | 与"本地自托管、代码与密钥不出本机"的定位直接冲突 |
| Tab 补全 / NES | 那是编辑器形态的能力，Spark 没有编辑器本体 |
| 内置 LSP 语言服务器子进程 | 破坏"只读工具不执行仓库自带二进制"的既有边界；已用语法级 `find_symbol` 覆盖主要需求 |

## 维护约定

1. 关闭一项时：改本表状态 + 在 `competitive-comparison.md` 同步矩阵格 + CHANGELOG 记一行。
2. 新增能力前先在这张表里加一行，写清"完成标准"；没有标准的不算排期。
3. 标 ⏸ 的项只有在用户明确拍板后才允许开工。
