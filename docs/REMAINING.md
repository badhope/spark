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

## 待做（按"能不能直接动手"排序）

### 可以直接做（无产品决策、成本可控）

| 能力 | 状态 | 完成标准 |
| --- | --- | --- |
| 子会话导航（子代理树） | 📋 | Web 侧能展开父 turn 看到每个子代理的独立轨迹；`subtask_*` 事件已有，缺 UI 聚合 |
| `spark uninstall` 卸载命令 | 📋 | 一条命令清干净：venv/配置/会话/knowledge，且先打印将删除的清单等确认 |
| 包管理器分发（brew/scoop） | 📋 | 至少一份 scoop manifest + 安装文档；brew 需要 tap 仓库 |
| 状态栏定制（TUI） | 📋 | 有序项配置，复用 `agent.display` 思路 |
| 压缩提示词可编程干预 | 📋 | 允许 `prompts/compact.md` 覆盖内置压缩提示词 |

### 需要产品决策（会改变交互形态）

| 能力 | 状态 | 为什么不能自己决定 |
| --- | --- | --- |
| 侧聊 / 旁支（`/side`） | ⏸ | 引入"一条主对话 + 多条分支"的状态模型；会话表结构要改，且 UI 需要新的导航层。做错比不做更糟 |
| Goal 长程模式（`/goal`） | ⏸ | 需要定义"目标—证据—验收"的推进协议；没有证据门槛的 goal 只是自动循环，会烧钱 |
| 主 agent 切换（build/plan 双档） | ⏸ | 与现有 approval profile 语义重叠，合并还是并存需要拍板 |
| 插件市场 | ⏸ | 涉及签名、安装权限、沙箱边界。没有签名机制的市场是供应链攻击面 |

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
