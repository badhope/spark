# 生态调研与差距清单（2026-11-07）

> 目的：把"业界已经有的成熟方案"逐个核对一遍，判断 spark 缺什么、补什么、不补什么。
> 与 `competitive-comparison.md` 的区别：那份是**功能有无的矩阵**；这份记录**调研结论与决策依据**，
> 每一条都能落到代码或明确列为"不做"。

调研方式：9 个同类项目的 README 与站点文档（GitHub API 抓取原文，非首页宣传语），
再对照 spark 源码逐项 `grep` 核实。凡标注"已有"的，都能在 `src/` 找到实现。

---

## 一、调研对象

| 项目 | 形态 | 本轮的取用点 |
| --- | --- | --- |
| opencode (sst) | TUI + Web + 桌面 + ACP | 多语言 README 矩阵、build/plan 双 agent、`@agent` 提法、插件与 skills 目录搜索 |
| Codex CLI (OpenAI) | CLI + IDE + 桌面 | 安装器回退链、`exec --json`、审批 × 沙箱双轴、review 子命令 |
| Claude Code | CLI + IDE | AGENTS.md 层级、hooks、skills、permission 规则分级 |
| Aider | TUI + watch | **repo map 三桶算法**（已借用）、git 自动提交、lint/test 回环、voice |
| goose (Block) | 桌面 + CLI + API | MCP 70+ 扩展的目录组织、recipe 概念、provider 广度 |
| Cline | IDE + CLI + 桌面 + SDK | 多形态共享同一 engine、SDK 化、JetBrains 覆盖 |
| Roo Code | IDE | **Modes（Code/Architect/Ask/Debug/Custom）**、每 mode 独立工具与提示词 |
| Continue | IDE + CLI | 配置为中心的分层、模型角色分工 |
| OpenHands Agent Canvas | 自托管控制台 | 多 agent backend 抽象、automation 面板、容器后端 |
| Crush (Charm) | TUI | 会话内切模型保上下文、LSP 增强、跨平台打包矩阵 |
| Gemini CLI | CLI | 能力分层（免费/企业）、Code Wiki 徽章、extensions |

---

## 二、逐项核对结果

### 已经具备、且实现质量不低于业界（不重复造）

| 能力 | spark 落点 | 对照 |
| --- | --- | --- |
| repo map 三桶热度索引 | `repo_map.py` | 与 Aider 同算法，另加 git 热度 |
| 多形态共享一个 loop | `core/loop.py` + 各 surface | 与 Cline/goose 同构 |
| 审批 × 沙箱双轴 | `policy.py` / `sandbox.py` | 强于多数竞品的单轴设计 |
| 检查点回滚（非 git 可用） | `core/checkpoints.py` | 独有 |
| 任务即分支（绿测门控） | `git_memory.py` | 独有 |
| MCP client + server | `mcp_bridge.py` / `mcp_expose.py` | 与 goose 同级 |
| A2A server | `a2a_server.py` | 开源编码 agent 中少见 |
| 声明式子代理 | `agents.py` | 与 opencode 同级 |
| 定时任务 + 停机补跑 | `automations.py` | 与 ZCode 同级 |
| 多 provider + 凭据池 + 降级链 | `providers/` | 与 Crush/goose 同级 |

### 本轮新增

| 能力 | 落点 | 为什么现在做 |
| --- | --- | --- |
| **Repo Wiki** | `wiki.py` + `spark wiki` + 设置→代码文档 + `/api/wiki` | REMAINING 里成本最高的待做项，且已有 tree-sitter 基础设施，边际成本低；竞品（Qoder/ZCode）都有，缺失会影响"这个项目能不能自己解释自己"的观感 |
| Wiki 引用校验 | `wiki.verify_citation` | 竞品的 wiki 常见"行号漂移"，宁可拒绝也不写悬空引用 |
| 快捷键面板跟随用户绑定 | `ShortcutsHelp.tsx` | 支持改键却展示固定键位，是自相矛盾的 UI |
| i18n 字典完整性门禁 | `tests/test_frontend_invariants.py` | 缺 key 会直接把 `dotted.key` 渲染给用户 |
| MCP serverInfo 版本 | `mcp_expose.py` | 客户端日志里报的是 SDK 版本，不是产品版本 |

### 明确不跟进（附理由，避免反复讨论）

| 能力 | 不做的理由 |
| --- | --- |
| 内置可驱动浏览器（点击/填表） | 需要捆绑 Chromium 或接管用户浏览器，与"本地优先、零账号"的定位冲突；`web_fetch` 只读模式已覆盖查资料场景 |
| 内置 LSP 语言服务器 | 会打破"只读工具不执行仓库自带二进制"的边界（`tools/diagnostics.py` 已明确拒绝 `node_modules/.bin/*`）；`find_symbol` 语法级覆盖定义/引用 |
| 云端沙箱 / 账号 / 移动端 | 与自托管定位直接冲突 |
| Tab 补全 / NES | 那是编辑器本体能力，spark 没有编辑器 |
| Git PR 工作流 | 需要长期 token 与仓库写权限，与"push/pull 明确不提供"的既有安全边界冲突 |
| Mermaid 图生成 | wiki 的价值在"可核对"，图无法被机器校验，反而增加维护成本 |

---

## 三、从竞品提取、但当前成本高于收益的候选

按"能否在不引入新依赖的前提下落地"排序，供下一轮排期参考：

1. **多 agent 编排 DSL**（ZCode Dynamic Workflows 同级）——需要沙箱化的脚本执行面，成本高；
   现有 `task` 并行 4 路已覆盖多数场景。
2. **Roo Code 式自定义 Modes**——本质是"提示词 + 工具白名单 + 审批档"三元组，
   spark 已有 `approval_profiles`；差异只在 UI 呈现与切换快捷键，属增量优化而非新架构。
3. **Cline 式 SDK**——需要稳定的 Python 公共 API 与语义化版本承诺，
   与"Alpha、快速迭代"的当前阶段矛盾。
4. **Aider watch 模式**（监听文件变更自动触发）——需要文件系统 watcher，
   在 Windows 上行为不一致，且与检查点机制有交互复杂度。
5. **语音输入**——依赖外部 ASR 服务或本地模型，违背"无外部依赖可全功能运行"。

---

## 四、结论

- 主流 AI 编码 agent 的能力集合已高度趋同：**审批、沙箱、记忆、子代理、MCP、多形态** 是标配。
  真正的差异点集中在"回滚粒度、任务即分支、协议完备度"——这三项 spark 已经在前列。
- 本轮补齐的最大空白不是功能数量，而是**"项目能否解释自己"**：Repo Wiki 之前完全缺失。
- 剩下的高成本项（浏览器、LSP、云端）不是能力问题，而是**定位取舍**，
  已在 `REMAINING.md` 的"明确不做"里写清理由，避免下一轮重新讨论。
