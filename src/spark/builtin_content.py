"""Spark 开箱即用的内置开发套件。

首次打开一个工作区时，把经过筛选的工程 Skills 与斜杠命令播种到
``<workdir>/.spark/{skills,commands}``。这里只写**缺失**的文件，不覆盖用户
已经改写过的内容；并用版本标记避免删除后又被反复重建。

设计目标：
* 打开设置页就能看到可用的技能/命令，而不是空白；
* 正文按需由 ``load_skill`` 加载，不占用每轮上下文；
* 内容面向真实工程流程（需求、计划、TDD、调试、审查、安全、前端、发布）。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BUILTIN_VERSION = "3"

_SKILLS: dict[str, str] = {
    "requirements-clarify": '''---
name: requirements-clarify
description: "在动手写代码前把模糊需求收敛成可验收的合同：目标、范围、验收标准、非目标与约束。"
when_to_use: "需求含糊、涉及多角色/多模块、或用户只说“优化一下/做个功能”时。"
---
# 需求澄清

目标不是把用户问烦，而是避免做错方向后返工。先读仓库里已有的实现、文档、测试和命名，能自己确认的不要问。

## 步骤

1. 用一句话复述目标：谁在什么场景下，要完成什么动作，得到什么结果。
2. 找出**阻塞性未知**，最多问 3 个问题；每个问题给出你倾向的默认选项，方便用户直接确认。
3. 写出一份可验收的合同：
   - 必须做到（验收标准，尽量可测）
   - 明确不做（非目标，防止范围膨胀）
   - 兼容/性能/安全/部署约束
   - 受影响模块与不能破坏的现有行为
4. 如果信息仍然不足，先做最小可逆实现，并把假设写在回复开头。

## 输出模板

```
目标：
验收：
- [ ] ...
非目标：
约束/假设：
风险：
```

不要为了显得严谨而列一堆与本任务无关的通用问题。'''
    ,
    "implementation-plan": '''---
name: implementation-plan
description: "把已确认的需求拆成可执行、可验证、可回滚的小步计划，先找影响面再动代码。"
when_to_use: "任务超过一个文件、涉及接口/数据/UI 联动，或需要多轮工具调用时。"
---
# 实施计划

## 先勘察

- 用 `repo_context`（若可用）拿项目概况，再用 `grep`/`glob` 找相关实现与测试。
- 找出入口、数据流、边界和现有约定；不要凭文件名猜架构。
- 记录“改这里会影响谁”：调用方、测试、配置、文档、迁移脚本。

## 再拆步

计划必须能逐步验证，而不是“第一步：重构整个项目”。

1. 最小骨架/接口（可先写失败测试）
2. 核心逻辑
3. 边界与错误处理
4. 集成与 UI/CLI 接线
5. 测试、lint、类型检查、构建
6. 文档/迁移/回滚说明

每一步写清：改哪些文件、怎么验证、失败时怎么退回。用 `update_plan` 维护进度，完成一步就更新，不要一次吞掉全部步骤。

## 风险

优先处理不可逆操作（数据迁移、删除、发布、强推）。这类步骤必须单独列出并等待用户确认。'''
    ,
    "tdd-loop": '''---
name: tdd-loop
description: "用红-绿-重构闭环写代码：先写能失败的测试，再写最小实现，最后保持测试全绿地重构。"
when_to_use: "新增业务逻辑、修 Bug、改边界条件，或仓库已有测试框架时。"
---
# TDD 闭环

## 红

1. 找到现有测试目录、命名习惯和运行命令（`package.json`、`pyproject.toml`、`Makefile`）。
2. 写一个最小测试，精确描述期望行为；只测行为，不测私有实现细节。
3. 先运行它，确认**因为预期原因失败**，而不是导入错误/环境错误。

## 绿

4. 写让测试通过的最小实现；不要顺手加未要求的抽象。
5. 先跑目标测试，再跑受影响测试；确认没有破坏既有行为。

## 重构

6. 在测试全绿的前提下消除重复、改善命名、拆小函数。
7. 再跑一次目标测试 + 相关测试；必要时跑全量。

## 仓库没有测试设施时

先加最小可运行的测试入口（一个测试文件 + 一条命令），不要为了 TDD 引入庞大框架。无法自动测试的 UI/集成改动，至少写清手工验证步骤并实际执行。'''
    ,
    "debug-systematically": '''---
name: debug-systematically
description: "系统化定位 Bug：复现、缩小范围、提出假设、验证根因，再修复并补回归测试。"
when_to_use: "出现报错、行为不符、间歇性失败，或“改了几处还是不对”时。"
---
# 系统化调试

## 1. 复现

- 拿到最小复现命令/输入/环境；记录完整错误与堆栈。
- 间歇性问题先找触发条件（时间、并发、缓存、数据顺序、平台差异）。

## 2. 缩小

- 从入口到失败点二分：日志、断点、临时探针、逐层打印输入输出。
- 一次只验证一个假设；不要同时改五个地方。
- 区分“症状”和“根因”：异常被吞、错误默认值、竞态、边界条件、依赖版本。

## 3. 验证根因

写一句可证伪的假设，例如“空列表时 index 越界，因为分页返回 0 条”。然后做最小实验确认；实验失败就换假设，不要硬套。

## 4. 修复

- 改根因，不掩盖症状；保持最小 diff。
- 加一个会在修复前失败、修复后通过的回归测试。
- 跑目标测试 + 相邻测试；检查是否引入性能或兼容问题。

## 5. 收尾

说明：根因、为什么之前没发现、改了什么、如何验证、还有什么未覆盖。'''
    ,
    "code-review": '''---
name: code-review
description: "按正确性、安全、性能、可维护性、测试五个维度审查变更，给出带证据的分级问题。"
when_to_use: "提交前、合并前、用户要求 review，或完成较大改动后自检时。"
---
# 代码审查

先看 diff 和调用链，不要只看被改文件。每条问题必须带文件/行号或可复现命令，按严重程度排序：

- **P0 阻断**：数据损坏、安全漏洞、必然崩溃、破坏核心流程。
- **P1 应修**：边界错误、竞态、明显性能退化、缺少关键测试。
- **P2 建议**：命名、重复、可读性、可维护性。

## 五个维度

1. **正确性**：输入边界、空值、错误路径、状态转换、并发、幂等、兼容旧数据。
2. **安全**：信任边界、权限校验、注入、路径穿越、密钥泄露、SSRF、反序列化。
3. **性能**：N+1、无界循环、全量加载、重复计算、阻塞主线程、缓存失效。
4. **可维护性**：职责是否单一、命名是否准确、抽象是否过度、日志是否可诊断。
5. **测试**：是否覆盖新行为与失败路径；测试是否稳定、是否只测实现细节。

## 输出

```
结论：可以合并 / 需修改 / 阻断
P0：
- file:line — 问题、影响、建议
P1：
...
验证建议：
```

不要为了凑数提无关的风格问题；不要在没有依据时断言“可能有性能问题”。'''
    ,
    "security-review": '''---
name: security-review
description: "从信任边界出发检查输入、权限、密钥、文件与网络访问，输出可利用场景和修复优先级。"
when_to_use: "新增接口/上传/登录/命令执行/文件读写，或处理用户输入、第三方回调时。"
---
# 安全审查

## 先画信任边界

用户输入 → API → 业务逻辑 → 数据/文件/命令/外部服务。标出哪些数据不可信、哪些操作有副作用。

## 检查清单

- **输入**：类型/长度/编码/路径规范化；白名单优先于黑名单。
- **权限**：每个敏感操作是否校验“谁可以做什么”；不要只在前端隐藏按钮。
- **密钥**：是否硬编码、写日志、进前端、进仓库；是否可用环境变量/密钥管理。
- **文件**：路径穿越、符号链接、覆盖写、临时文件权限、压缩炸弹。
- **命令**：是否拼接用户输入；能否用参数数组/库替代 shell。
- **网络**：SSRF、重定向、超时、TLS 校验、回调签名/重放。
- **数据**：SQL/NoSQL 注入、反序列化、越权查询、敏感字段返回。
- **依赖**：新增依赖的来源、维护状态、许可证、已知漏洞。

## 输出

按“可利用性 × 影响”排序；每个问题写清攻击者需要什么条件、影响是什么、最小修复是什么、如何验证。防御性审查可以描述攻击场景，但不提供可直接复用的攻击载荷或绕过步骤。'''
    ,
    "frontend-quality": '''---
name: frontend-quality
description: "把前端做到可用且耐看：信息层级、响应式、状态覆盖、可访问性、明暗主题与性能。"
when_to_use: "新增页面/组件、改交互、用户反馈“丑/乱/难用/手机上错位”时。"
---
# 前端质量

## 先定结构

- 一屏只保留一个主任务；主操作视觉权重最高，次要操作降级。
- 用真实内容长度测试，不要用 lorem ipsum 掩盖溢出。
- 先画状态：默认、加载、空、错误、无权限、超长文本、窄屏。

## 检查

1. **布局**：小屏 320px、平板、桌面都不横向滚动；表格/代码块可横向滚动但不撑破页面。
2. **交互**：点击区域足够大；焦点可见；键盘可完成主流程；危险操作二次确认。
3. **可访问性**：语义标签、label、alt、对比度、`prefers-reduced-motion`。
4. **主题**：亮/暗两套都检查，不用纯黑纯白硬编码；颜色走 token。
5. **性能**：避免首屏加载大图/大包；列表虚拟化；动画只动 transform/opacity。
6. **一致性**：间距、圆角、字号、按钮高度沿用现有设计系统，不另起一套。

## 验收

用浏览器工具实际打开页面，截取关键状态；不要只凭代码判断“应该没问题”。'''
    ,
    "repo-onboarding": '''---
name: repo-onboarding
description: "快速读懂一个陌生仓库：技术栈、入口、目录职责、运行命令、测试方式、约定与风险。"
when_to_use: "第一次进入项目、接手他人代码、或需要给用户做项目解读时。"
---
# 仓库上手

## 顺序

1. 先看根目录：README、清单文件（`pyproject.toml`/`package.json`/`go.mod`/`Cargo.toml`）、CI、容器/部署文件。
2. 用 `repo_context`（若可用）拿语言占比、包管理器、脚本、Git 状态；没有再手工勘察。
3. 找入口：`main`/`cli`/`app`/`server`/路由注册/命令注册。
4. 找数据流：请求 → 服务 → 存储 → 返回；或 CLI 参数 → 业务 → 输出。
5. 找测试：测试目录、夹具、运行命令、CI 门槛。
6. 找约定：目录分层、命名、错误处理、日志、配置注入、代码生成。

## 输出

```
一句话定位：
技术栈：
启动/测试/构建命令：
核心模块（按职责，不按目录罗列）：
关键数据流：
项目约定：
风险/待确认：
```

只写有证据的结论；不确定的标“待确认”，不要编造架构。'''
    ,
    "release-check": '''---
name: release-check
description: "发布前最后一道闸：差异、测试、构建、迁移、配置、安全与回滚逐项确认。"
when_to_use: "准备提交/合并/打包/上线，或用户说“收尾/发布/给我最终版”时。"
---
# 发布检查

## 必查

1. **差异**：`git_diff` 逐文件过一遍；有没有调试代码、临时文件、无关格式化。
2. **测试**：目标测试 + 受影响测试；能跑全量就跑全量。
3. **静态检查**：lint、类型检查、构建；不能跑的要说明原因。
4. **数据**：迁移是否可逆、是否有备份、是否兼容旧数据、是否要灰度。
5. **配置**：新增环境变量/密钥是否有默认与文档；不要把密钥写进仓库。
6. **安全**：新增输入/权限/文件/网络操作是否过安全审查。
7. **文档**：README/变更说明/接口文档是否同步；破坏性变更是否标注。
8. **回滚**：失败时怎么退回；数据库迁移能否回滚；产物是否可重建。

## 输出

```
状态：可发布 / 有条件发布 / 不可发布
已验证：
未验证/风险：
回滚方案：
```

没有实际运行的检查不要写成“已通过”。'''
    ,
}

_COMMANDS: dict[str, str] = {
    "explore": '''---
name: explore
description: "快速解读当前仓库：架构、技术栈、入口、关键流程与风险"
---
请对当前工作区做一次完整的项目上手分析。先调用 `repo_context`（若可用），再按需读关键文件，不要凭目录名猜。

输出：
1. 一句话定位
2. 技术栈与运行/测试/构建命令
3. 目录职责与核心模块
4. 一条关键数据流（从入口到输出）
5. 项目约定与风险/待确认

范围：$ARGUMENTS
没有额外范围就分析整个仓库。只写有证据的结论。''',
    "review": '''---
name: review
description: "审查当前变更，按 P0/P1/P2 给出带证据的问题与验证建议"
---
审查当前工作区的代码变更。先用 `git_status` / `git_diff` 看真实 diff，再读相关调用链和测试。

按正确性、安全、性能、可维护性、测试五个维度检查；问题按 P0（阻断）/P1（应修）/P2（建议）排序，每条带 `file:line`、影响和最小修复建议。

审查范围：$ARGUMENTS
没有指定就审查未提交变更；若工作区干净，审查最近一次提交。不要为了凑数提无关风格问题。''',
    "fix": '''---
name: fix
description: "系统化修 Bug：复现、缩小、根因、最小修复、回归测试"
---
请修复这个问题：$ARGUMENTS

按系统化调试流程：
1. 先复现并记录完整错误；
2. 二分缩小范围，一次验证一个假设；
3. 写清根因，不要只改症状；
4. 做最小修复；
5. 补一个修复前失败、修复后通过的回归测试；
6. 跑目标测试和相关测试。

最后说明根因、改动、验证结果和仍未覆盖的风险。''',
    "frontend": '''---
name: frontend
description: "实现或改进前端界面，覆盖加载/空/错误/窄屏/明暗主题"
---
请处理这个前端任务：$ARGUMENTS

要求：
- 先读现有设计系统与同类组件，沿用间距、字号、圆角、颜色 token；
- 覆盖默认、加载、空、错误、无权限、超长文本、窄屏；
- 主操作视觉权重最高，点击区域足够大，键盘可操作；
- 亮/暗主题都检查；
- 完成后用浏览器工具实际打开并截图关键状态，不要只凭代码判断。

如果任务描述不足，先按最小可逆方案实现，并列出假设。''',
    "secure": '''---
name: secure
description: "对当前改动做安全审查，输出可利用场景与修复优先级"
---
请对当前变更做安全审查：$ARGUMENTS

先画信任边界（用户输入 → API → 业务 → 数据/文件/命令/外部服务），再检查输入校验、权限、密钥、路径穿越、命令注入、SSRF、注入、反序列化、敏感数据返回和新增依赖。

按“可利用性 × 影响”排序，每个问题写清攻击条件、影响、最小修复和验证方式。没有指定范围就审查未提交 diff。''',
    "refactor": '''---
name: refactor
description: "在测试保护下做小步重构，保持行为不变"
---
请重构：$ARGUMENTS

约束：
1. 先确认现有测试能跑；不能跑就先补最小 characterization test；
2. 一次只做一个可验证的小步，保持 diff 可 review；
3. 不改变对外行为、接口和错误语义，除非用户明确要求；
4. 每步后跑相关测试；
5. 不做无关格式化、不顺手升级依赖、不引入新框架。

最后说明重构前后的结构差异、行为不变证据和剩余风险。''',
    "release": '''---
name: release
description: "发布前检查：差异、测试、构建、迁移、配置、安全与回滚"
---
请做发布前检查：$ARGUMENTS

逐项确认：
- diff 是否有调试代码/临时文件/无关改动；
- 目标测试、受影响测试、全量测试；
- lint、类型检查、构建；
- 数据迁移是否可逆、是否兼容旧数据；
- 新增环境变量/密钥是否文档化；
- 安全问题；
- 回滚方案。

输出“可发布 / 有条件发布 / 不可发布”，并列出已验证与未验证项。没有实际运行的检查不要写成已通过。''',
    "explain": '''---
name: explain
description: "解释一段代码或一个模块：入口、数据流、关键决策与坑"
---
请解释：$ARGUMENTS

先读真实代码与调用方，再输出：
1. 它解决什么问题；
2. 入口与调用链；
3. 关键数据结构/状态变化；
4. 为什么这样设计（有证据才说）；
5. 容易踩的坑与修改时的影响面。

面向“要改它的人”写，不要逐行复述代码。''',
    "commit": '''---
name: commit
description: "根据真实 diff 生成规范的提交信息，不自动推送"
---
请为当前变更准备提交：$ARGUMENTS

1. 先看 `git_status` 和 `git_diff`，确认只包含本次任务相关文件；
2. 用一句话说明“为什么改”，再列关键变化；
3. 提交信息用中文或仓库现有风格，首行不超过 72 字符；
4. 不要自动 push、不要改写历史、不要提交密钥/临时文件；
5. 如果有无关文件，先列出来让用户决定。

输出建议的提交信息与待提交文件清单。''',
}


_PLUGIN_FILES: dict[str, dict[str, str]] = {
    "repo-health": {
        "spark-plugin.json": '''{
    "name": "repo-health",
    "version": "0.1.0",
    "description": "只读仓库体检：调试残留、TODO/FIXME、疑似密钥、大文件、敏感文件与测试覆盖提示。",
    "entry": "",
    "permissions": ["tools"],
    "tools": [
        {
            "name": "scan",
            "description": "扫描当前工作区并返回仓库体检报告。只读，不修改任何文件。",
            "command": "tool.py",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对工作区的子目录，默认 '.'"
                    },
                    "max_files": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 5000,
                        "description": "最多扫描文件数，默认 2000"
                    }
                },
                "required": []
            },
            "timeout_sec": 30
        }
    ],
    "commands": ["health"],
    "hooks": []
}''',
        "tool.py": '''#!/usr/bin/env python3
"""Read-only repository health scan for the repo-health plugin."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".spark", ".venv", "venv", "env", "node_modules",
    "dist", "build", "out", "target", "coverage", ".next", ".nuxt", ".turbo",
    ".cache", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "vendor", ".idea", ".vscode",
}
TEXT_EXT = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".kt", ".kts", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb",
    ".php", ".swift", ".scala", ".sh", ".ps1", ".sql", ".vue", ".svelte",
    ".html", ".css", ".scss", ".md", ".toml", ".yaml", ".yml", ".json",
    ".xml", ".ini", ".cfg", ".conf", ".env.example", ".txt",
}
SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", "id_rsa", "id_dsa", "id_ed25519",
    ".npmrc", ".pypirc", "credentials.json", "secrets.json",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"}
DEBUG_RE = re.compile(r"(pdb\\.set_trace\\(|breakpoint\\(\\)|debugger;|console\\.log\\()")
TODO_RE = re.compile(r"\\b(TODO|FIXME|HACK|XXX)\\b")
SECRET_RE = re.compile(
    r"(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|"
    r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----)",
    re.IGNORECASE,
)
CREDENTIAL_RE = re.compile(
    r"(?:api[_-]?key|secret|password|token)\\s*[:=]\\s*['\\\"][^'\\\"]{8,}",
    re.IGNORECASE,
)


def workdir() -> Path:
    here = Path.cwd().resolve()
    for parent in here.parents:
        if (parent / ".spark" / "plugins").is_dir():
            return parent
    return here.parents[2] if len(here.parents) >= 3 else here


def git_tracked(root: Path) -> set[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def git_dirty(root: Path) -> int:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    return len([line for line in proc.stdout.splitlines() if line.strip()])


def scan(root: Path, max_files: int) -> dict:
    started = time.monotonic()
    findings = []
    todo_count = 0
    debug_count = 0
    secret_count = 0
    large_count = 0
    sensitive = []
    source_files = 0
    test_files = 0
    scanned = 0
    truncated = False
    tracked = git_tracked(root)

    def add(level, kind, rel, line, message):
        if len(findings) < 80:
            findings.append({"level": level, "kind": kind, "file": rel, "line": line, "message": message})

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if time.monotonic() - started > 6.0:
            truncated = True
            break
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        for name in filenames:
            if scanned >= max_files:
                truncated = True
                break
            if name.startswith(".") and name not in SENSITIVE_NAMES:
                continue
            path = Path(dirpath) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            scanned += 1
            rel = path.relative_to(root).as_posix()
            suffix = path.suffix.lower()
            is_test = "test" in name.lower() or "/tests/" in rel or rel.startswith("tests/")
            if is_test:
                test_files += 1
            elif suffix in TEXT_EXT:
                source_files += 1

            if name in SENSITIVE_NAMES or suffix in SENSITIVE_SUFFIXES:
                sensitive.append(rel)
                level = "high" if rel in tracked else "medium"
                add(level, "sensitive-file", rel, 0, "敏感文件存在" + ("，且已被 Git 跟踪" if rel in tracked else "，未被 Git 跟踪"))
                continue

            if size > 2 * 1024 * 1024:
                large_count += 1
                add("low", "large-file", rel, 0, f"文件较大（{size // 1024 // 1024} MB），确认是否需要入库")
                continue

            if suffix not in TEXT_EXT or size > 512 * 1024:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if "DEBUG_RE" in line or "TODO_RE" in line:
                    continue
                if DEBUG_RE.search(line):
                    debug_count += 1
                    add("medium", "debug-leftover", rel, lineno, "疑似调试残留")
                if TODO_RE.search(line):
                    todo_count += 1
                    if todo_count <= 8:
                        add("low", "todo", rel, lineno, "TODO/FIXME/HACK 标记")
                if is_test or suffix in {".md", ".rst", ".txt"}:
                    continue
                if SECRET_RE.search(line):
                    secret_count += 1
                    add("high", "possible-secret", rel, lineno, "疑似真实密钥前缀（值已隐藏）")
                elif CREDENTIAL_RE.search(line):
                    secret_count += 1
                    add("medium", "credential-assignment", rel, lineno, "疑似凭据赋值，确认是否为占位符")
        if truncated and scanned >= max_files:
            break

    dirty = git_dirty(root)
    summary = {
        "scanned_files": scanned,
        "source_files": source_files,
        "test_files": test_files,
        "todo": todo_count,
        "debug_leftovers": debug_count,
        "possible_secrets": secret_count,
        "large_files": large_count,
        "sensitive_files": len(sensitive),
        "dirty_files": dirty,
        "truncated": truncated,
    }
    lines = [f"# 仓库体检: {root}", ""]
    lines.append(f"- 扫描文件: {scanned}（源码 {source_files} / 测试 {test_files}）")
    lines.append(f"- TODO/FIXME: {todo_count} · 调试残留: {debug_count} · 疑似密钥: {secret_count}")
    lines.append(f"- 大文件: {large_count} · 敏感文件: {len(sensitive)} · 未提交: {dirty}")
    if truncated:
        lines.append("- 扫描达到时间/文件上限，结论是抽样。")
    if not findings:
        lines.append("")
        lines.append("没有发现明显的调试残留、TODO 堆积、密钥或敏感文件。")
    else:
        lines.append("")
        lines.append("## 发现")
        for f in findings[:40]:
            loc = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
            lines.append(f"- [{f['level']}] {loc} — {f['message']}")
        if len(findings) > 40:
            lines.append(f"- …还有 {len(findings) - 40} 条，见结构化 findings")
    if test_files == 0 and source_files > 0:
        lines.append("")
        lines.append("> 没有识别到测试文件；建议至少补一个最小测试入口。")
    return {"content": "\\n".join(lines), "summary": summary, "findings": findings}


def main() -> None:
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        request = {}
    args = request.get("arguments") if isinstance(request, dict) else {}
    args = args if isinstance(args, dict) else {}
    root = workdir()
    raw_path = str(args.get("path") or ".").strip()
    target = Path(raw_path)
    if not target.is_absolute():
        target = root / target
    try:
        target = target.resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        sys.stdout.write(json.dumps({"ok": False, "payload": {"error": "path escapes workdir"}}, ensure_ascii=False) + chr(10))
        return
    if not target.is_dir():
        sys.stdout.write(json.dumps({"ok": False, "payload": {"error": f"not a directory: {raw_path}"}}, ensure_ascii=False) + chr(10))
        return
    try:
        max_files = max(100, min(int(args.get("max_files") or 2000), 5000))
    except (TypeError, ValueError):
        max_files = 2000
    report = scan(target, max_files)
    sys.stdout.write(json.dumps({"ok": True, "payload": report}, ensure_ascii=False) + chr(10))


if __name__ == "__main__":
    main()
''',
        "commands/health.md": '''---
name: health
description: "仓库体检：调试残留、TODO、密钥、大文件、敏感文件与测试覆盖"
---
请调用 `plugin__repo_health__scan` 扫描当前工作区：$ARGUMENTS

拿到报告后：
1. 先列 high 级问题（疑似密钥、被 Git 跟踪的敏感文件），给出最小修复；
2. 再按文件给出 medium/low 级问题（调试残留、TODO 堆积、大文件）；
3. 说明测试覆盖提示与未提交变更；
4. 不要直接修改文件，先给清单和建议，等用户确认。
''',
    }
}


def _write_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return True


def ensure_workspace_defaults(
    workdir: Path, *, enable_plugins: bool = False
) -> dict[str, int]:
    """把内置 Skills/Commands/插件播种到工作区；只补缺失文件。

    返回 ``{"skills": n, "commands": n, "plugins": n}``，表示本次实际新建的数量。
    任何写入失败都只记日志，不影响 Spark 启动。

    ``enable_plugins`` 只在首次播种且本工作区尚未记录过启用标记时，把内置
    ``repo-health`` 加入全局插件启用列表；用户之后手动关闭不会再被打开。
    """

    result = {"skills": 0, "commands": 0, "plugins": 0}
    base = Path(workdir) / ".spark"
    try:
        marker = base / ".builtins-version"
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == BUILTIN_VERSION:
            enabled_marker = base / ".builtin-plugin-enabled"
            if not enable_plugins or enabled_marker.exists():
                return result
    except OSError:
        pass

    try:
        for name, content in _SKILLS.items():
            if _write_if_missing(base / "skills" / f"{name}.md", content):
                result["skills"] += 1
        for name, content in _COMMANDS.items():
            if _write_if_missing(base / "commands" / f"{name}.md", content):
                result["commands"] += 1

        for plugin_name, files in _PLUGIN_FILES.items():
            root = base / "plugins" / plugin_name
            created = False
            for rel, content in files.items():
                if _write_if_missing(root / rel, content):
                    created = True
            if created:
                result["plugins"] += 1
            tool = root / "tool.py"
            if tool.is_file():
                try:
                    tool.chmod(0o755)
                except OSError:
                    pass

        base.mkdir(parents=True, exist_ok=True)
        (base / ".builtins-version").write_text(BUILTIN_VERSION + "\n", encoding="utf-8")

        if enable_plugins and not (base / ".builtin-plugin-enabled").exists():
            try:
                from spark.plugins import set_plugin_enabled

                set_plugin_enabled("repo-health", True)
                (base / ".builtin-plugin-enabled").write_text("repo-health\n", encoding="utf-8")
            except Exception as exc:  # noqa: BLE001 - seeding must never block startup
                logger.warning("enable built-in plugin failed: %s", exc)
    except OSError as exc:
        logger.warning("seed built-in skills/commands/plugins failed: %s", exc)
    return result


def builtin_skill_names() -> list[str]:
    return list(_SKILLS)


def builtin_command_names() -> list[str]:
    return list(_COMMANDS)
