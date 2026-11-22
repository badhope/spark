# 开箱即用的开发套件

Spark 第一次打开一个工作区时，会把一套经过筛选的工程 Skills 与斜杠命令写入
`.spark/skills` 和 `.spark/commands`，并把默认身份设为“资深编程专家”。目标是让设置页
第一次打开就有东西可用，而不是一片空白。

## 默认会创建什么

- **9 个 Skills**：需求澄清、实施计划、TDD 红绿循环、系统化调试、代码审查、安全审查、
  前端质量、仓库上手、发布检查。
- **9 个斜杠命令**：`/explore`、`/review`、`/fix`、`/frontend`、`/secure`、
  `/refactor`、`/release`、`/explain`、`/commit`。
- **默认系统提示词**：`src/spark/prompts/system.md` 以“资深编程专家”身份开场，并写明
  最小 diff、测试先行、改完验证、安全边界、不过度设计等工程原则。
- **一个原生只读工具**：`repo_context`。一次返回项目语言、包管理器、真实 test/build/lint/dev
  命令、关键清单、顶层结构、入口候选和 Git 状态，减少在陌生仓库里反复 `list_dir`。
- **3 个内置 MCP 精选**：Git、Sequential Thinking、Memory。它们默认只出现在 MCP 设置页，
  点“一键加入”后才写入配置；不会在启动时自动拉起 `npx`/`uvx` 进程。
- **1 个真实可跑的插件**：`repo-health`。带 manifest、只读工具 `scan` 和 `/health` 命令；
  Web 首次播种时默认启用，TUI 只播种不启用。

## 播种规则

`spark.builtin_content.ensure_workspace_defaults()` 只补缺失文件：

1. 已存在的同名文件不会被覆盖，用户改过的技能/命令会保留。
2. 写入后生成 `.spark/.builtins-version`；版本相同就直接跳过，避免删掉后又被反复重建。
3. 写入失败只记日志，不影响 Spark 启动。
4. Web 与 TUI 启动时都会调用；工作区必须可写。

如果确实想恢复内置版本，删除对应的 `.spark/skills/<name>.md` 或
`.spark/commands/<name>.md`，再删除 `.spark/.builtins-version`，下次启动会重新补齐。

### 内置插件 `repo-health`

插件落在 `.spark/plugins/repo-health/`：

- `spark-plugin.json`：声明 `tools` 权限、`scan` 工具和 `health` 命令；
- `tool.py`：只读扫描器，跳过 `.git`、`.spark`、`node_modules`、`.venv`、`dist` 等目录；
- `commands/health.md`：`/health` 命令模板，要求模型调用 `plugin__repo_health__scan`。

扫描内容：TODO/FIXME、`pdb.set_trace`/`debugger`/`console.log` 等调试残留、真实密钥前缀、
凭据赋值、大于 2 MB 的大文件、敏感文件、Git 未提交变更、源码与测试文件数量。测试文件和
Markdown 文档不做密钥/凭据扫描，避免把示例和 fixture 当成泄露；报告不输出密钥原值。

Web 首次播种会把它写入全局 `[plugins].enabled`，并在工作区记 `.spark/.builtin-plugin-enabled`；
之后用户手动关闭不会在下次启动被重新打开。TUI 不自动启用。想手动开关，去
设置 → 扩展 → 插件。

插件工具走正常审批：只读工具可直接执行，任何写操作仍需确认；插件子进程 cwd 是插件目录，
`tool.py` 通过向上查找 `.spark/plugins` 得到工作区根，并拒绝逃出工作区的 `path` 参数。

## 为什么 MCP 不默认启动

- `npx` / `uvx` 第一次运行要下载包，冷启动可能几十秒，还可能因网络失败；
- Filesystem、Git、Memory 等与 Spark 原生工具大量重叠，默认全开会让模型面对重复工具；
- 外部 MCP server 是独立进程，默认拉起会绕开 Spark 的部分审批与沙箱语义。

因此这里采用“精选目录 + 一键加入”，把是否启动、是否限制工具的决定留给用户。
Git 预设默认只暴露 `git_status`、`git_diff`、`git_log` 等只读工具；Memory 的写入类工具
仍需审批。

## 设计参考

内置内容不是把别人的长文原样搬进来，而是抽取工程流程后写成本项目可执行的短工作流。
主要参考：

- [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills)：单技能可独立安装、
  围绕 code review / TDD / 需求访谈组织。
- [obra/superpowers](https://github.com/obra/superpowers)：把 brainstorming、计划、TDD、
  系统化调试、code review、verification 串成一套开发方法。
- [mattpocock/skills](https://github.com/mattpocock/skills)：`tdd`、`diagnosing-bugs`、
  需求拷问等技能划分。
- [seb1n/awesome-ai-agent-skills](https://github.com/seb1n/awesome-ai-agent-skills)：按
  Code & Development 分类的现成 SKILL.md 工作流。
- [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers)：官方
  Filesystem / Git / Memory / Sequential Thinking 等本地 server。
- [LocalNest MCP](https://github.com/wmt-mobile/LocalNest)：local-first、代码检索 + 持久记忆
  的思路；本项目没有直接内置它，因为它的能力集偏重，不符合“轻量默认”的目标。

## 自定义

- 技能：设置 → 扩展 → 技能，或直接编辑 `.spark/skills/*.md`。frontmatter 至少要有
  `name`、`description`，可选 `when_to_use`。
- 命令：设置 → 扩展 → 提示词，或编辑 `.spark/commands/*.md`；正文里的 `$ARGUMENTS`
  会被替换为用户输入。
- 系统提示词：设置 → 扩展 → 提示词。留空使用内置编程专家提示词；填写后整段覆盖。
- MCP：设置 → 扩展 → MCP。内置精选可一键加入；远程 server 仍支持 stdio / sse /
  streamable_http。
