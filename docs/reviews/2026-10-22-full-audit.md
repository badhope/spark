# 全维度审查与对标报告（2026-10-22）

> **定位说明（2026-11-23）**：本文写作于“本地优先”旧口径时期，该口径已废止。现行定位见 [PRODUCT_POSITIONING](../PRODUCT_POSITIONING.md)；本文仅作历史审计与事实记录，不再作为路线依据。


> 审查对象：`spark-agent` 0.9.11（HEAD `238ffce`）
> 审查方法：全部结论均有 `文件:行号` 或实跑命令输出支撑；无法取证的一律标 `[未确认]`。
> 审查期间未修改任何代码。

## 状态（2026-10-22 更新）

| 发现 | 状态 | 验证方式 |
|---|---|---|
| F-001 SSRF 云元数据 | **已修复** | `_is_public_host` 改为 `is_global` 白名单式；8 个攻击地址全部拒绝，3 个公网地址放行；`tests/test_approval_and_ssrf.py` 新增 9 条参数化用例 |
| F-002 依赖 CVE | **部分修复** | 27 → 9；langgraph 组清零（0.6.11→1.2.16、checkpoint-sqlite 2.0.11→3.1.2、certifi 2026.10.19）；余 chromadb（无修复版本，本地 Client 模式，暴露面低于 CVSS 假设）与 nltk（llama-index 传递依赖，spark 未直接调用） |
| F-003 覆盖率 | **部分修复** | 71.8%→**74.8%**，分支 63.4%→**66.4%**。新增 `test_routes_sweep.py`（44 条，覆盖全部 GET/POST 路由与 PTY）、`test_cli_onboarding.py` +14 条 CLI、`test_helpers_units.py`（36 条纯函数）、`test_tui_app.py`（6 条驱动真实 Textual app）。**仍未达 90%/85% 红线**：剩余大头是 TUI(39%)、cli(43%)、pty(34%)、desktop(36%)，见 R3 续 |
| F-004 安全头 | **已修复** | 加 CSP + Permissions-Policy；`Server` 头改为 `spark`；新增响应头断言测试 |
| F-005 数据备份/迁移 | **已修复** | 加 `SCHEMA_VERSION` / `PRAGMA user_version` / `backup_to()`（VACUUM INTO）/ `spark doctor --backup`；新增 2 条测试 |
| F-006 启动耗时 | **已修复** | CLI 惰性导入 loop/probe/factory；`--version` 5 次中位数 **1.87s**（原 12.7s，冷缓存首跑仍有波动属 Windows 磁盘/杀软） |
| F-007 文档漂移 | **已修复** | `docs/competitive-comparison.md` 已同步 6 项（斜杠命令/消息回溯/Fork/导出/@引用/ask_user），删除 1 条重复行，新增 5 项独有能力，表头加「改代码必须同步本表」约定 |
| F-009 复杂度 | **部分修复（第六轮）** | E/F 级 7 → **0**（`radon cc -n E` 空）；D 级 29 → 22，平均复杂度 A(4.74)。余 22 个 D 级未拆，理由见第十三节 13.1 |
| F-010 用户提问工具 | **已修复** | `ask_user` 工具 + `question` 事件 + 问答卡；无交互通道时优雅报错；新增 2 条测试，浏览器实测可选「pnpm」并回灌 |
| F-014 审计流 | **已修复** | 新增 `src/spark/audit.py`：默认关闭（`SPARK_AUDIT_LOG`），JSONL 追加写，写入前走 `sanitize.redact_secrets`，永不抛错。已埋点：工具被策略/审批拒绝、sandbox 路径越界、配置损坏自愈。`/api/diagnostics` 返回最近 20 条。5 条测试（含端到端：被拒的 `write_file` 落到审计） |


---

## 一、总体结论

**整体评级：C+（可发布，未达生产级）。**

功能完整度与真机可用性已实测通过（两个真实模型各 6/6 冒烟、真实编码闭环 6/6、静态门禁全绿），但**三项硬指标不达标**：

1. 行覆盖 **71.8%**（红线 90%）
2. 分支覆盖 **63.4%**（红线 85%）
3. 依赖层 **27 个已知 CVE / 7 个包**，其中 SSRF 白名单漏掉云元数据网段，属于可在阿里云 ECS 上直接利用的 High。

**最大风险**：F-001 SSRF（云元数据可读）与 F-002 依赖 CVE（chromadb 越权 / langgraph 反序列化）。

**最优先三项行动**：R1 修 SSRF → R2 处理依赖 CVE → R3 补覆盖率。

---

## 二、证据与基线

| 项 | 实测值 |
|---|---|
| 项目类型 | 本地自托管 AI 编程 agent（CLI + TUI + Web + Desktop + ACP + MCP server），**非 SaaS** |
| 版本 | `spark.__version__ = 0.9.11`，与 `pyproject.toml` 一致 |
| 技术栈 | Python 3.13.15 / litellm 1.106.0 / langgraph 0.6.11 / mcp 1.30.0 / pydantic 2.14.1 / chromadb 1.5.9 / textual 0.89.1 / pywebview 6.2.1 / React 19 |
| 规模 | 后端 24,504 行（93 文件）；前端 9,472 行（30 组件）；测试 83 文件 |
| 用户角色 | 单用户本机；多会话；无账号体系 |
| 数据敏感级 | **高**（模型 API Key、源代码、shell 执行权） |
| 部署形态 | 默认仅监听 `127.0.0.1`；可选 `0.0.0.0` + `X-Spark-Token` |

### 实际运行命令与输出

```
pytest         799 passed, 5 skipped (213.94s)
coverage       line 9088/12200 = 71.8% | branch 2524/3982 = 63.4%
ruff           All checks passed
mypy           95 files, 0 issues
bandit         0 High / 0 Medium / 0 Low
lint-imports   5 contracts kept
mutation       16/16 mutants killed
radon cc       avg A (4.85)；97 blocks ≥C；7 blocks ≥E
duplication    6 行窗口 27,634，跨文件重复 61 = 0.22%
frontend       dcl 165ms，JS 传输 698 KB
startup        import spark 0.12s；import spark.core.loop 5.32s
```

### 对标对象（具名）

| 竞品 | 选择理由 |
|---|---|
| **opencode（sst）** | 同为开源、BYO 模型、TUI+Web+ACP 的自托管 agent，形态最接近 |
| **Codex CLI（OpenAI）** | CLI agent 交互基准（审批粒度、回溯编辑、`/export`、`/fork`） |
| **ZCode（智谱）** | 桌面+Web+CLI 三形态，中文场景最接近的商用产品 |

---

## 三、发现清单（按严重级别排序）

| ID | 级别 | 置信度 | 维度 | 位置 | 问题 | 证据 | 建议修复 | 对标含义 |
|---|---|---|---|---|---|---|---|---|
| **F-001** | **High** | 高 | 安全 A01 | `src/spark/tools/web.py:140` | SSRF 白名单漏掉云元数据网段：`100.100.100.200`（阿里云）被判为公网 | `_is_public_host('100.100.100.200') = True`；`fetch_page` 发起真实 TCP 连接（`ConnectTimeout WinError 10060`，说明未被拦截） | 私有地址改用 `ipaddress` 判定：加 `100.64.0.0/10`、`169.254.0.0/16`、`fc00::/7`、`fe80::/10` | opencode/codex 均硬拒绝 metadata 端点；**关闭差距** |
| **F-002** | **High** | 高 | 供应链 A03 | `pyproject.toml:25,31,32,35` | 27 个已知 CVE / 7 包；含 chromadb 越权读写、langgraph 反序列化 RCE、checkpoint-sqlite 命名空间越界 | `pip-audit -s osv`：`chromadb 1.5.9` 覆盖至 `last_affected 1.5.9`；`langgraph 0.6.11` 修复版 `1.0.10` | 升级 langgraph≥1.0.10、checkpoint-sqlite≥3.1.1、certifi；chromadb 降为可选 extra | 竞品均锁定并跑 SCA；**关闭差距** |
| **F-003** | **High** | 高 | 可维护性 | 全局 | 行覆盖 71.8% < 90%，分支 63.4% < 85%；CI 门槛只卡 65% | `pytest --cov-branch`；`.github/workflows/ci.yml:59` `--cov-fail-under=65` | 见 R3 | 内部质量，**关闭差距** |
| F-004 | Medium | 高 | 安全 A02 | `src/spark/web/routes.py:145` | 缺 CSP、Permissions-Policy；`Server` 头泄露 `Python/3.13.15` | 实抓响应头：CSP `<ABSENT>`、Permissions-Policy `<ABSENT>`、Server 暴露版本 | 加 CSP + Permissions-Policy；覆写 Server 为 `spark` | 本地服务竞品同样不设 CSP（**承认**），版本泄露应关 |
| F-005 | Medium | 高 | 可靠性 | `src/spark/store.py` | 无数据库备份、无恢复演练、无 schema 版本表 | `grep backup\|restore\|user_version\|migrate` 在 store.py 为 0 条 | `VACUUM INTO` 备份 + `PRAGMA user_version` 迁移链 + `doctor --backup` | codex/opencode 有会话归档；**关闭差距** |
| F-006 | Medium | 高 | 性能 | `src/spark/core/loop.py` 导入链 | 启动 5.32s 由 litellm 主导，`--version` 也吃满 | `import spark` 0.12s vs `from spark.core import loop` 5.32s | 纯 CLI 路径惰性导入 loop | 竞品 CLI 冷启动 <1s；**关闭差距** |
| F-007 | Medium | 高 | 功能完整性 | `docs/competitive-comparison.md:60,70-72` | 文档与代码漂移：文档称「斜杠命令 ❌ / Fork 无 UI / 导出 ❌」，三项均已实现 | `slash.py:216`、`routes.py:576/578`、`Header.tsx:70` | 更新矩阵并加核对日期 | **承认差距**（文档债） |
| F-008 | Medium | 高 | 安全 A04 | `src/spark/store.py:196` | 会话 ID `uuid4().hex[:12]` = 48 bit；无认证 | 代码行 | 不修（单机单用户威胁模型），文档明示「暴露局域网等于交出 agent」 | **承认差距** |
| F-009 | Low | 高 | 可维护性 | 全局 | 97 个函数复杂度 ≥C，7 个 ≥E | `radon cc -n C` | 按 R5 拆分 | 不适用 |
| F-010 | Low | 高 | 功能完整性 | `src/spark/tools/registry.py` | 无「用户提问工具」，agent 无法主动提问 | `grep ask_user\|request_user_input` = 0 | 加 `ask_user` 工具 + 前端弹层 | codex `request_user_input`、opencode `question`；**关闭差距** |
| F-011 | Low | 高 | 功能完整性 | `src/spark/tools/` | 无 LSP、无写后格式化 | `grep lsp` = 0；`grep black\|prettier` = 0 | 见 R6 | opencode 内建 LSP + 30 formatter；**承认差距** |
| F-012 | Low | 高 | 前端 | `web/src/` | 无 `/share`、无 `/side` 侧聊、无 Vim 模式 | 关键词 grep 无实现 | 见 R7 | opencode `/share`、codex `/side`；**刻意忽略** |
| F-013 | Low | 中 | 测试有效性 | `tests/test_sandbox.py` | 2 测试仅 1 断言（比例 0.5） | 断言密度脚本 | 补负例断言 | 不适用 |
| F-014 | Low | 高 | 安全 A09 | 全局 | 拒绝命令 / 审批拒绝只进 debug 日志，无结构化审计流 | `grep audit` 仅 otel span | 加结构化审计事件 + 可选 OTLP | codex Guardian 有 OTLP 审计；**关闭差距** |

### PoC：F-001

```text
_is_public_host('100.100.100.200') = True      # 应为 False
_assert_fetchable('http://100.100.100.200/latest/meta-data/')  → 不抛异常
fetch_page(...) → httpx.ConnectTimeout(WinError 10060)  # 已发起真实连接
```

本机非阿里云 ECS，故未取回凭据；在 ECS 上该路径可直接读
`/latest/meta-data/ram/security-credentials/`。

---

## 四、查漏补缺清单（按项目类型）

**项目类型：本地 AI agent（非 Web SaaS）。**

### 不适用维度（明确说明，不凑数）

| 维度 | 判定 |
|---|---|
| 多租户隔离 / 租户越权 | 不适用：单机单用户，无租户概念 |
| 登录 / MFA / 密码策略 | 不适用：无账号体系，鉴权 = loopback 或 `X-Spark-Token` |
| 支付 / 计费一致性 | 不适用：BYO key，无计费 |
| 公网 WAF / DDoS | 不适用：默认仅监听 127.0.0.1 |
| 水平扩容 / 自动伸缩 | 不适用：单进程本地服务（容量上限另见下表） |
| K8s / 容器编排 | 不适用：分发形态为 EXE/zip |

### 本类型专属场景（已实测）

| 场景 | 状态 | 证据 |
|---|---|---|
| 环境缺失（python/git/node） | ✅ 自愈 | `selfheal.diagnose/repair`、`doctor --fix` 实测修复 `config.missing` |
| 配置损坏 | ✅ 自愈 | 坏 TOML → 备份 `config.toml.broken-<ts>` + 重建，实测通过 |
| 端口被占 | ✅ 自动选端口 | `pick_free_port`，`test_web_port.py` |
| 模型端点不可达 | ⚠️ 有重试，无降级切换 UI | `provider_retry` 3 次后报错 |
| 同会话并发 SSE | ✅ 串行化 | 实测 3 并发均 200，返回体一致（`turn_lock`） |
| 断网 / 流中断 | ✅ 有重试按钮 | 实测错误卡 + 重试，重试后 prompt 仅 1 条 |
| 长会话（数百条） | ✅ 虚拟列表 | `@tanstack/react-virtual` |
| 上下文溢出 | ✅ 自动压缩 | 85% 阈值，`test_compaction.py` |
| Windows 保留名 / 编码 | ✅ 已处理 | `.gitattributes`、`aux.py`→`auxiliary.py`、stdio UTF-8 |
| 桌面无黑框 | ✅ 已实现 | `spark-desktop.exe` subsystem=2 实测 |
| 崩溃恢复（审批中断） | ✅ 持久 checkpointer | 但**无恢复演练测试**（[未确认] 真实断电） |

---

## 五、竞品对标矩阵

| 能力 | Spark（实测） | opencode | Codex CLI | ZCode | 差距类型 | 优先级 |
|---|---|---|---|---|---|---|
| 多 provider / BYO key | ✅ 8 预设 + 自定义 | ✅ models.dev | ⚠️ | ✅ | 承认 | — |
| 审批粒度 | ✅ 6 档 + 规则引擎 | ✅ | ✅ | ✅ 4 档 | 承认 | — |
| 消息级编辑/重发 | ✅ 实测 | ✅ | ✅ Esc Esc | ✅ | 已关闭 | — |
| Fork 会话 | ✅ 实测 | ✅ `--fork` | ✅ `/fork` | ✅ | 已关闭 | — |
| 导出会话 | ✅ md/json 实测 | ✅ `/export` | ✅ `/export` | ⚠️ | 已关闭 | — |
| 自定义斜杠命令 | ✅ `.spark/commands/*.md` | ✅ | ⚠️ hooks | ✅ | 已关闭 | — |
| `@` 文件引用 | ✅ 实测 | ✅ | ✅ | ✅ | 已关闭 | — |
| 子代理并行 | ✅ 4 路 + 执行图 | ⚠️ | ⚠️ | ⚠️ | **跨越**（可视化更强） | — |
| LSP 集成 | ❌ | ✅ 40+ 语言 | ❌ | ❌ | **承认** | P2 |
| 写后格式化 | ❌ | ✅ 30+ formatter | ❌ | ❌ | **承认** | P2 |
| 用户提问工具 | ❌ | ✅ `question` | ✅ | ⚠️ | **关闭** | P2 |
| 内置浏览器（可驱动） | ❌（仅只读网页阅读） | ❌ | ⚠️ | ✅ | **刻意忽略** | — |
| Repo Wiki | ❌ | ❌ | ❌ | ✅ | **刻意忽略** | — |
| Goal 长程模式 | ❌ | ⚠️ | ❌ | ✅ | **承认** | P3 |
| 分享链接 | ❌ | ✅ | ❌ | ❌ | **刻意忽略** | — |
| 侧聊 / 旁支 | ❌ | ⚠️ | ✅ `/side` | ✅ | **承认** | P3 |
| 插件系统 | ⚠️ 仅 MCP | ✅ npm | ✅ marketplace | ✅ | **承认** | P3 |
| 自动更新 | ❌ | ✅ | ✅ | ✅ | **关闭** | P1 |
| 一键安装 | ❌ | ✅ curl | ✅ npm | ✅ | **关闭** | P1 |
| AI 审 AI | ❌ | ❌ | ✅ Guardian | ❌ | **承认** | P3 |
| 安全响应头 | ⚠️ 部分 | 不适用 | 不适用 | 不适用 | 承认 | P2 |

### 体验对标（核心页实测）

已具备：空状态 3 个建议卡；每操作有 loading/disabled/成功/失败四态；错误卡可重试；健康卡显示「全部正常」；设置 7 页签均可点且 `aria-current` 正确。

差距：`?` 帮助只有快捷键、无入口按钮（发现性差）；无首次使用引导（竞品均有 onboarding）。

---

## 六、量化指标看板

| 指标 | 当前值 | 红线 | 判定 |
|---|---|---|---|
| 行覆盖率 | **74.8%** | ≥90%（核心域 ≥80%） | **不通过** |
| 分支覆盖率 | **66.4%** | ≥85% | **不通过** |
| 变异分数 | 16/16 = **100%** | ≥80% | 通过 |
| 圈复杂度（单函数） | 97 个 ≥C，7 个 ≥E | ≤10 | **不通过** |
| 认知复杂度 | 未测（无工具） | ≤15 | **[未确认]** |
| 平均复杂度 | A (4.85) | — | 通过 |
| 技术债务比率 | 未测（无 SonarQube） | ≤5% | **[未确认]** |
| 重复行 | 0.22% | <3% | 通过 |
| Critical/High 漏洞 | 27 CVE（含 1 High SSRF） | 0 | **不通过** |
| 断言密度 | 2.30 断言/测试，763 测试 | — | 通过 |
| 零断言测试 | 0 | 0 | 通过 |
| 函数 >50 行 | 76 / 921 = 8.2% | — | 提示 |
| 前端首屏 | 165ms / 698 KB JS | — | 通过 |
| 启动耗时 | 5.32s（litellm 主导） | — | 提示 |

---

## 七、修复与补齐路线图

| # | 做什么 | 对应 | 工作量 | 验收标准 |
|---|---|---|---|---|
| R1 | 修 SSRF：`_is_public_host` 改用 `ipaddress`，加 `100.64.0.0/10`、`169.254.0.0/16`、`fc00::/7`、`fe80::/10` | F-001 | S | 参数化测试覆盖上述 4 网段 + 十进制 IP + IPv6，全部抛异常；`test_approval_and_ssrf.py` 全绿 |
| R2 | 依赖 CVE：升级 langgraph≥1.0.10、checkpoint-sqlite≥3.1.1、certifi；chromadb 降为可选 extra | F-002 | M | `pip-audit -s osv` 高危归零；799 测试通过；真机 6/6 复跑 |
| R3 | 覆盖率续：TUI(39%)、cli(43%)、pty(34%)、desktop(36%) 是大头；先补 `web/_handlers_models` 等已补到 60%+ 的余下分支 | F-003 | L | `--cov-fail-under=80`；分支 ≥70%；CI 门槛同步 |
| R4 | 数据安全：`PRAGMA user_version` 迁移链 + `VACUUM INTO` 备份 + `doctor --backup` | F-005 | M | v1 库升级后数据完整；备份可用 `restore` 打开 |
| R5 | 拆分 7 个 E 级函数 | F-009 | L | `radon cc -n D` 为 0；测试全绿 |
| R6 | 加 `ask_user` 工具 + 前端输入弹层 | F-010 | M | 模型可调用并阻塞等待；E2E 断言返回值回灌 |
| R7 | 启动优化：纯 CLI 路径惰性导入 loop | F-006 | S | `spark --version` <0.5s |
| R8 | 安全头：CSP + Permissions-Policy + 覆写 Server | F-004 | S | 响应头断言测试 |
| R9 | 文档同步：更新 `competitive-comparison.md` 已实现的 5 项 | F-007 | S | 矩阵与代码一致，加核对日期 |
| R10 | 首次使用引导 + `?` 帮助入口 | 体验 | M | 新用户 3 步引导；header 有 `?` 按钮 |
| R11 | 结构化安全审计流 | F-014 | M | `audit.jsonl` 或 OTLP，含 ts/tool/decision/reason |

**R1 / R2 是本轮仅有的两个 High，必须先做。**

---

## 八、诚实边界

1. **认知复杂度、技术债务比率**未测——环境无 SonarQube/等价工具，标 `[未确认]`，不计入通过。
2. **真实断电恢复**未验——只测了审批中断的持久化路径，未做 `kill -9` 后恢复演练。
3. **CVE 可利用性**只按 CVSS 与功能面判断，未逐个构造 exploit；F-002 定为 High 是因为 chromadb 越权在其网络服务模式下可直接利用，而本项目用本地 `EphemeralClient/PersistentClient`，实际暴露面**小于** CVSS 假设——明确标出，不夸大为 Critical。
4. **竞品对标**基于公开文档与仓库（2026-10 时点），未安装竞品实测；矩阵「✅」指其文档声明支持。
5. 本次审查未修改任何代码。


---

## 九、本轮整改记录（2026-10-22）

| 轮次 | 内容 | 测试 |
|---|---|---|
| 1 | F-001 SSRF、F-002 依赖、F-004 安全头、F-005 备份、F-006 启动、F-010 ask_user | 813 passed |
| 2 | F-007 文档同步、F-014 审计流、覆盖率 71.8→74.8、路由/CLI/helpers/TUI 四组新测试 | **918 passed** |

覆盖率未达标是**事实**，不是遗漏：本轮的 44 条路由扫描 + 14 条 CLI + 36 条纯函数 + 6 条 TUI 已把最容易腐烂的面（路由表、参数校验、平台分支）覆盖到可回归的水平；剩余缺口集中在 TUI 渲染细节、desktop 壳、pty 真机路径，这些要么需要图形环境要么依赖 POSIX，性价比显著低于已补部分。

---

## 十、第三轮：逐界面实测（2026-10-22 深夜）

不是读代码推断，是真的开浏览器/桌面窗口点出来的。每条都附证据。

### 10.1 修掉的真缺陷

| # | 缺陷 | 证据 | 影响 | 修复 |
|---|---|---|---|---|
| D-01 | 远程 MCP 全链路不可用 | registry 首屏 20 条全 `remote`，一键添加按钮 100% 禁用；手动配上后启动报 `too many values to unpack` | 注册表功能对绝大多数条目形同虚设 | 前端补 `transport` 选择 + URL 输入；`streamable_http_client` 三返回值解包修正 |
| D-02 | 单个连不上的远程 MCP 拖垮整个 `spark web` | `spark web` 启动直接 `CancelledError` 退出（`work_ui11.log`） | 一个填错的 URL 让应用起不来 | 启动循环兜 `BaseException`（放行 KeyboardInterrupt/SystemExit），错误记为 `mcp_errors` |
| D-03 | `_McpConnection.close()` 只吞 `RuntimeError` | anyio 在关闭 streamable_http 时抛 `BaseExceptionGroup(401)`，覆盖真实错误 | 错误信息误导 | 改吞 `BaseException`，保留原始失败原因 |
| D-04 | 桌面产物双击进 TUI 而非窗口 | `dist/spark-desktop/spark-desktop.exe` 打出 Textual 控制序列；GUI 子系统无控制台，等于点开没反应 | 桌面版完全不可用 | 新增 `spark.desktop_main` 专用入口，spec 改为以它为入口 |
| D-05 | diff 头被裁成裸 `+++` | 审批卡截图：`--- a/x` 下面一行孤零零 `+++` | 看不出改的哪个文件 | 解析器改按文件去重，`---`/`+++` 合并为一行文件名 |
| D-06 | diff 增删行行号重复 | 新增行左右两栏都显示 `1` | 行号不可信 | 左栏只给删除/上下文行号，右栏只给新增/上下文行号 |

### 10.2 实测通过的功能（有截图/日志）

- 记忆面板：输入 → 添加 → 列表出现 → 删除 → 消失（`D05`/`D06`/`D07`）
- MCP registry：6 秒拉回 20 条，国内可用标签、一键添加、生成的 `streamable_http` 行（`E02`/`E03`）
- 审批卡：diff 预览、参数展开（`{"path": ..., "content": ...}`）、允许本次后弹窗关闭（`F01`/`F02`）
- 桌面版：窗口 1292×764，Web UI 完整渲染，`/api/status` 200（`G04`）
- 打包产物：`spark.exe` 子系统 3（CONSOLE）、`spark-desktop.exe` 子系统 2（GUI），二者前端资产哈希与源码一致

### 10.3 门禁

```
pytest              931 passed, 5 skipped
ruff check           All checks passed
mypy                 Success: no issues found in 97 source files
import-linter        exit 0
bandit               0 High-confidence new findings（存量 6 Medium / 57 Low 均为已知并说明）
```

### 10.4 未验证

- 远程 MCP 只测了本地起的 streamable_http server 与真实的 401（鉴权失败）；没有可用的第三方远程 MCP 凭证，未测真实第三方服务。
- 桌面窗口截图用 CDP 截内嵌页面，未对内嵌 WebView 做像素级比对（Windows 上 PrintWindow 抓到的是不完整帧，不是产品问题）。

---

## 十一、第四轮：设置面板对齐 Codex / Claude Code（2026-10-23）

用户指出设置里缺 skill / prompt / plugin / 快捷键 / 知识库。核实结果：前四项
后端已有能力或部分能力但**没有任何 UI 入口**；知识库是**真缺口**（只有长期记忆，
没有用户文档库）。

### 11.1 五项现状与落地

| 项 | 落地前 | 现在 |
|---|---|---|
| 技能 | `.spark/skills/*.md` 已被 `load_skill` 工具读取，但界面上看不到、不能建 | 设置→技能：列表 / 新建（带 frontmatter 模板）/ 查看 / 删除 |
| 提示词 | `cfg.agent.system_prompt` 可改但埋在 Agent 页；`.spark/commands` 只能靠手写文件 | 设置→提示词：系统提示词编辑 + 内置提示词对照 + 斜杠命令模板增删 |
| 插件 | 无 | 设置→插件：发现 `.spark/plugins/<name>/plugin.json`，并列出真正会执行代码的 hooks（不新开第二条免审批执行通道） |
| 快捷键 | 6 个键写死在 `App.tsx`，只有只读帮助面板 | 设置→快捷键：逐项改键、冲突检测、单项/全部恢复；运行时经 `matchesBinding` 读取 |
| 知识库 | 无 | 新建 `spark.knowledge`：分块 + 中文 bigram BM25、索引存 spark home；设置→知识库支持导入 / 删除 / 检索测试 / 开关 / top-k；每轮检索注入 `<knowledge_base>` 只读围栏 |

### 11.2 真机证据

- 12 个设置 tab 全部渲染（`H00`–`H07` 截图）：模型 / Agent / 权限 / 展示 / 记忆 / MCP / 技能 / 提示词 / 插件 / 快捷键 / 知识库 / 项目
- 技能：写入 `code-review.md` → 列表出现 → 读出正文 → 删除（后端 200）
- 知识库：导入 `architecture.md` → 检索 `鉴权 权限` 命中 score 0.58 → **向模型提问「鉴权用了几层权限作用域？deny 冲突时谁优先？」得到「三层权限作用域，deny 优先」**，该信息只存在于导入文档中，证明注入链路真实生效
- 路径安全：`../../etc/passwd`、`C:/Windows/x.md` 均被 400 拒绝

### 11.3 过程中修掉的缺陷

| # | 缺陷 | 影响 |
|---|---|---|
| D-07 | `spark.knowledge` 从 `spark.memory.store` 导入 `tokenize`，把 chromadb 拖进每轮对话路径 | ACP e2e 从 23s 变 100s+ 超时；`import spark.knowledge` 4.7s → 1.1s（分词器移入 `spark.textutil`） |
| D-08 | Toast 只能显示 i18n key | API 报错统一显示成「错误」，真实原因丢失；改为可携带原文 |

### 11.4 门禁

```
pytest              973 passed, 5 skipped
ruff check          All checks passed
mypy                Success: no issues found in 100 source files
import-linter       exit 0
产物                spark.exe(console) / spark-desktop.exe(GUI) 重建通过，打包版 /api/skills /api/knowledge 实测 200
```

### 11.5 未验证

- 知识库只做了词法检索，未接 embedding（`memory` 的向量路径可用，但知识库刻意不引入 chromadb 依赖）；跨语言检索（中文查英文文档）能力有限，标 `[未确认]`
- 快捷键改键只覆盖 6 个全局键，输入框内的 Enter / Shift+Enter / `/` / `@` 仍是固定行为
- 插件只做发现与展示，没有插件安装/卸载（按设计：执行必须走 hooks）

---

## 十二、第五轮：收尾自检（2026-10-23）

### 12.1 补测时又抓到三个真 bug

| # | 缺陷 | 发现方式 | 影响 |
|---|---|---|---|
| D-09 | `delete_command` 不补 `.md` 后缀，而 `save_command` 补 | 写路由级测试时 `test_delete_command` 404 | 界面上的「删除命令」按钮永远失效（除非用户手输 `.md`） |
| D-10 | `spark.desktop_main` 的 `except Exception` 吞掉 `typer.Exit` | `test_run_desktop_nonzero_code_propagates` 期望 2 实得 1 | 桌面版所有退出码被压成 1，pywebview 缺失与真实崩溃无法区分 |
| D-11 | `create_session` 不写 `sessions_fts`；`append_message` 无条件用消息前 24 字覆盖 FTS 标题 | `test_search_matches_title` 搜不到自定义标题 | 新会话标题重启前搜不到；用户手改标题后标题命中永久失效 |

### 12.2 补上的能力

**会话全文检索接线**：`SessionStore.search()` 一直有 FTS5（trigram，中文子串友好）实现，但侧栏只在已加载的 `sessions` 行里做子串过滤，搜不到聊天正文。新增 `GET /api/sessions/search`（按 workdir 过滤、limit 上限 100、检索异常降级为空结果），侧栏改为 220ms 防抖调服务端，命中正文时在标题下显示片段。

### 12.3 覆盖变化

```
轮次      用例    行覆盖   分支覆盖
第四轮    973     74%      66%
第五轮   1025     76%      67%
```

新增模块的质量（这是本轮主要产出的代码）：

| 模块 | 行覆盖 |
|---|---|
| `src/spark/knowledge.py` | 92% |
| `src/spark/web/_handlers_content.py` | 88% |
| `src/spark/desktop_main.py` | 87% |

### 12.4 门禁（最终）

```
pytest              1025 passed, 5 skipped
pytest --cov        76% line / 67% branch
ruff check          All checks passed
mypy                Success: no issues found in 100 source files
import-linter       exit 0
```

覆盖率仍**未达 90%/85% 红线**，如实记录。剩余缺口集中在 `cli.py`（43%，大量 command 分支）、`desktop.py`（36%，需要真实窗口）、`pty_api.py`（43%，需要 POSIX PTY）、`tui/`（渲染细节）。这些要么需要图形环境，要么依赖平台特性，继续堆测试的性价比低于本轮已完成的真缺陷修复。

### 12.5 截图证据

| 图 | 内容 |
|---|---|
| `assets/settings-12-tabs.png` | 设置面板 12 个页签 |
| `assets/skills-editor.png` | 技能编辑器（frontmatter 模板 + 文件名联动） |
| `assets/knowledge-base.png` | 知识库：导入、已导入文档、检索测试命中 |
| `assets/mcp-registry.png` | MCP registry：20 条结果、国内可用标签、一键添加 |
| `assets/approval-diff.png` | 审批卡：diff 预览 + 参数展开 |
| `assets/desktop-window.png` | 桌面版窗口 |

### 12.6 未验证

- 知识库跨语言检索（中文查英文文档）能力有限，只有词法匹配；
- 会话搜索的 FTS5 只测了 SQLite 内置 trigram，未在 SQLite < 3.34 的真实环境验证 LIKE 降级（代码路径有测试覆盖，但那是模拟 DROP TABLE，不是真实旧版本）；
- 远程 MCP 仍未接真实第三方凭证。

---

## 十三、第六轮：R5 复杂度清零 + R10 首次引导（2026-10-23）

收尾路线图剩余两项：R5（7 个 E/F 级函数拆分）与 R10（首次使用引导 + `?` 帮助入口）。
除 R10 为新功能外全部为行为保持型重构——既有测试是回归网，拆分前后聚焦子集全绿。

### 13.1 R5：E/F 级复杂度 7 → 0

| 函数 | 原级 | 现级 | 拆分方式 |
|---|---|---|---|
| `web/_handlers_settings.py _handle_settings` | F (69) | C (18) | 字段提取 / provider / agent 数值 / 上下文 / hooks / MCP 六个校验 helper + 统一提交段 |
| `core/loop.py _run_tool_body` | F (67) | C (16) | 直调判定、拒绝结果构造（deny 审计原位保留）、ask_user 解析、task 参数收集、子任务事件泵（实时转发不缓冲）、执行收尾 |
| `providers/openai_compat.py _stream_impl` | F (54) | C (20) | 单目标重试 `_request_with_retries`、请求参数构造、全败抛错（`NoReturn`）、usage 提取 / 工具调用聚合模块级化 |
| `web/_handlers_chat.py _handle_chat_stream` | F (46) | C (19) | 图片校验、SSE 头、/test 执行、命令展开、回合派发、断连收尾、记忆线程七个 helper |
| `memory/service.py record_turn` | E (36) | B (7) | 抽取 / 相似检索 / 操作解析 / 嵌入 / 事务应用五段 |
| `config.py save_config` | E (34) | B (9) | provider/agent/context/memory 表 + 四个 AoT 构造器 |
| `core/checkpoints.py restore_workdir` | E (31) | A (5) | 计划校验 / 开档 / manifest 交叉校验 / 逐成员恢复 / 清扫五段 |

- 全仓 `radon cc -n E` = **0**（原 7）；D 级 29 → 22；平均复杂度 A(4.94) → A(4.74)。
- **未达 R5 验收原案（`radon cc -n D` 为 0）**，如实记录：余下 22 个 D 级多为 CLI
  命令体与分支密集但已被路由/CLI 测试钉住的函数，继续拆分的回归风险大于本轮
  收益，留待后续轮次。

### 13.2 R10：首次使用引导 + `?` 帮助入口

- Header 新增 `?` 图标按钮（此前帮助面板只能按 `?` 键唤起，无可见入口）。
- 新组件 `web/src/components/Onboarding.tsx`：三步引导（配置模型 → 审批模式 →
  开始对话），第 1/2 步按钮直达对应设置页；跳过 / 开始使用 / 关闭任一路径写
  `spark.onboarding.done` 后不再出现。
- 设计取舍（实测发现）：服务端启动即自动创建「新对话」会话（`state.py:286`），
  "会话列表为空"不构成新用户信号，故门条件为纯 localStorage 标记——老用户升级后
  会看到一次，一键关闭。

### 13.3 真机证据

- `assets/onboarding-first-run.png`：隔离 `SPARK_HOME` + mock provider 实跑
  `spark web`，首次加载弹出三步引导；header 最右为新增 `?` 按钮。
- S8.2（标记生效后不再弹）：headless 截图工具限制（重定向捕获白帧、持久 profile
  被沙箱终止），降级为代码审查验证（三行 localStorage 门），如实标注未做像素级复验。

### 13.4 环境修复（非本轮代码缺陷，但影响门禁）

- 本机 mcp 被升到 2.3.0，违反 `pyproject.toml` 的 `mcp>=1.0,<2.0`，且 2.x 残留文件
  与 1.30.0 混装导致 `import mcp` 断裂（`test_mcp_expose` 收集失败、mcp_bridge mypy
  6 错）。清理重装 mcp 1.30.0 后 mypy 恢复 0 错。
- 上一轮提交 8f6abd3 修改了 `api_spec.py` 但未重新生成快照——`export_openapi.py
  --check` 在 main 上即失败。本轮补跑 `export_openapi.py` + `orval gen:api`
  （`web/openapi.json` +102 行、`schema.ts` +75 行），CI 的 OpenAPI 门恢复绿色。

### 13.5 门禁

```
pytest（全量+覆盖）   1020 passed；行覆盖 78.47%（CI 门 75% 通过）
ruff check            All checks passed
mypy                  Success: no issues found in 100 source files
import-linter         5 kept, 0 broken
bandit                0 High / 0 Medium / 0 Low
radon cc -n E         0 blocks（原 7）
tsc -b + vite build   通过，src/spark/web/dist 已重建
OpenAPI snapshot      up to date
```

### 13.6 存量失败甄别（clean HEAD 复跑确认，非本轮引入）

| 测试 | 现象 | 判定 |
|---|---|---|
| `test_acp_server.py::test_e2e_subprocess` | ConnectionError（子进程 ACP server） | 存量环境 |
| `test_hardening_regressions.py::test_run_tests_detects_plain_pytest_layout` | run_tests 布局探测 | 存量环境 |
| `test_p1_03_testcmd.py::test_handle_test_runs_real_pytest` / `::test_handle_test_real_failure_feedback` | 真实 pytest 子进程 | 存量环境 |
| `test_p1_08_gitmemory.py::test_begin_stepcommit_loop_and_success_finish` / `::test_failure_abandon_cleans_main_tree` | git worktree | 存量环境 |

本轮唯一引入又当场修复的回归：`test_web_stream_wires_test_slash`——源码级接线见证
随 /test 段落拆分失效，按原意图升级为模块级源码见证后复绿。

### 13.7 未验证

- 引导"写入标记后不再弹"未做像素级复验（工具限制，见 13.3）；
- 行覆盖 78.47% / 分支覆盖仍未达 90%/85% 红线，理由同第十二节（缺口集中在
  cli / desktop / pty / TUI 渲染，性价比低）；
- 22 个 D 级函数未继续拆分（理由见 13.1）。

---

## 十四、第七轮：分发链路与协作闭环（2026-10-23）

用户指令：以 §12 差距清单与本文档未竟项为基线，补齐到可对企业展示的完整项目；
复用成熟方案、不重复造轮子；完整实测（浏览器截图）；收尾清理；交付远程仓库。

### 14.1 本轮关闭的差距（12 项）

| 项 | 落地 | 证据 |
|---|---|---|
| 一键安装 | `scripts/install.sh` + `install.ps1`：Release → SHA256 强校验 → 用户目录安装；支持 `SPARK_VERSION`/`SPARK_MIRROR`/`SPARK_INSTALL_DIR` | `sh -n` 通过（CI packaging job）；脚本内校验不通过即 `die` |
| 自动更新 | `src/spark/updater.py` + `spark update [--check|--force]`；https-only、SHA256、原子替换、`*.old` 备份；doctor 24h 缓存提示；`[updater] mode=off` | `tests/test_updater.py` 16 条（版本比较/资产选择/sums 解析/校验拒绝/zip 替换/备份） |
| `exec --json` | NDJSON 每行一个 TurnEvent，末行 `{"type":"final"}` | `tests/test_cli_onboarding.py::test_exec_json_*` 2 条 |
| AI 审 AI | `spark review`（uncommitted/commit/base），强制 plan-only+sandbox-only，high → exit 2 | `tests/test_review.py` 13 条 |
| `!cmd` 内联 shell | Web `POST /api/shell`，复用 run_shell 全套限制；本地卡片、不进上下文 | `tests/test_polish_round2.py::test_inline_shell_*` 2 条 |
| 会话分享 | `format=html` 自包含单文件，消息全部转义 | `test_session_export_html_is_self_contained`（断言 `<script>` 被转义） |
| 会话导入 | `POST /api/sessions/import`，仅 user/assistant 文本，拒绝 tool 载体，限 500 条/1MB | 3 条导入测试（roundtrip / JSON 字符串 / 非法载荷） |
| 桌面通知 | Web Notification，回合结束/审批等待，仅页面隐藏时；设置开关 + 权限失败提示 | 前端 `notify.ts` + SettingsModal 展示页 |
| 模型热键 | `Ctrl+Alt+M` 循环档案 | `App.tsx::cycleModel` |
| small_model | 标题/关键词生成接入 `provider.small_model`（压缩摘要此前已接） | `web/state.py::_generate_session_meta_async` |
| 全局规则 | 复核发现 `~/.agents/AGENTS.md` 全局层**早已实现**（`core/context.py`），此前矩阵标注错误 | `load_agents_md` 三级叠加；本轮修正矩阵 |
| 发布资产 | release.yml 增加 `SHA256SUMS`、Windows desktop 产物、构建后 `--version` 冒烟；CI 增加 packaging job | `ci.yml` packaging job |

### 14.2 本轮刻意不做的（如实标注）

- **LSP 只读工具**：需要 pylsp / tsserver 子进程管理与 40+ 语言矩阵，属独立一轮的
  工作量；矩阵保持 ❌，不假装做了。
- **包管理器分发**（brew/scoop/npm）：需外部仓库审核与长期维护，本轮用一键脚本覆盖。
- **云端分享链接**：与「本地优先、零云依赖」定位冲突，提供单文件 HTML 离线分享作为等价物。
- **Repo Wiki / 定时任务 / 移动端**：矩阵已列为 P2/不建议，未动。
- **sandbox 网络开关 / 子代理独立模型**：评估后降级——前者在 bwrap 路径需重排
  `--share-net` 语义且 Windows 无对应实现，后者要改 task 工具签名与配置层，
  两者都无法在本轮拿到与成本匹配的收益；留待后续，不写成"已完成"。

### 14.3 验证

- `pytest`：本轮新增 33 条（review 13 / updater 16 / exec-json 2 / share+import+shell 6 减重叠），
  全量结果见 §14.4。
- `ruff` / `mypy`（102 文件 0 issue）全绿。
- 前端 `tsc -b` + `vite build` 通过；新增 UI 均经浏览器实测（截图见 `docs/assets/`）。
- CI 新增 packaging job 覆盖安装脚本语法与版本一致性，避免"发出去才发现脚本坏"。

### 14.4 门禁结果（本机实测，2026-10-24）

```
pytest                1087 passed, 5 skipped（314s）
coverage              行 78%（CI 门 75%）；review.py 93%、updater.py 84%
ruff                  All checks passed
mypy                  102 source files, 0 issues
bandit                0 High / 0 Medium / 0 Low
import-linter         5 contracts kept, 0 broken
OpenAPI snapshot      up to date
tsc -b + vite build   通过（16 chunk，dist 无重复 hash）
sh -n install.sh      OK（dash）
```

5 个 skip 均为平台/可选依赖：`ptyprocess`×3、bubblewrap（仅 Linux）、symlink 权限。

**远端 CI（GitHub Actions，commit `fcd2842`）7/7 工作流 success**：
CI（quality / packaging / windows-checkout / test×4）、Security、docs、Evals、
build-exe、mirror。

首轮推送时 CI 暴露并修复了两个发布链缺陷（详见下方 §14.5）：

1. `install.sh` 以 CRLF 提交，Linux `dash` 的 `sh -n` 报
   `Syntax error: word unexpected (expecting "in")`——`.gitattributes` 已锁
   `*.sh eol=lf`；
2. `build-exe` / `release` 的冒烟 grep 锚定整行，而 `spark version` 会先打印
   品牌 banner——改为匹配版本子串，仍会在无版本号时失败。

### 14.5 本轮修掉的 CI 级缺陷与诚实边界

**修掉的缺陷**

| # | 缺陷 | 影响 | 修法 |
|---|---|---|---|
| D-12 | `_SSESession` 的 peer 断连探测（`select` + `MSG_PEEK`）在 Linux 上「刚建连、请求体刚读完」瞬间报可读且 `recv` 为空，被判定为断连 | `test_live_stream_keeps_sse_event_contract` 在 ubuntu 3.11/3.12 上 100% 得到空 body；Windows select 语义不同，本地永不复现 | 建连后 0.25s grace 窗口内不做 peer 检查；空闲真实关闭仍可检测（`test_sse_writer_notices_idle_socket_close`），新增 `test_peer_disconnect_is_suppressed_during_connect_grace` 强制 select 报可读来钉住 Linux 行为 |
| D-13 | `install.sh` 含 CRLF（Windows 提交） | Linux `dash` 的 `sh -n` 在 `case ... in` 处报语法错，packaging job 红 | 脚本 LF 化 + `.gitattributes` 锁 `*.sh eol=lf`、`*.ps1 eol=crlf` |
| D-14 | 冒烟 grep 锚定整行 `^x.y.z$`，但 `spark version` 打印品牌 banner + 版本行 | build-exe / release 的冒烟步骤必然 exit 1，产物其实可用 | 改为匹配版本子串（无版本号仍失败） |
| D-15 | `web/openapi.json` 快照在 `8f6abd3` 后过期 | main 的 quality job 长期红 | 重新生成快照并纳入本轮提交 |
| D-16 | `frontend` dist 里累积了 7 个同名不同 hash 的陈旧 chunk | 包体冗余、缓存语义混乱 | 清空重建，Vite 现在只产出 16 个 chunk |

**诚实边界（仍未验证）**

- 安装脚本的真实下载链路未在 CI 里跑（需要先发布 Release 资产）；CI 只做语法与
  资产名一致性校验，端到端安装需在打 tag 后手工验证一次。
- `spark update` 的 `frozen` 分支不在测试中执行（测试用 zip 替换模拟），
  真实 PyInstaller 自替换未做端到端演练。
- 桌面通知只在 Chromium 下验证；Firefox/Safari 权限模型差异未测。
- 本轮 Linux 侧结论来自 CI 实跑，本机无 Linux 环境（Docker daemon 不可用、
  WSL 未安装），无法在本地复现同一时序。

### 14.6 收尾：远端同步与行尾规范化（2026-10-24）

CI 首次全绿后又做了三件收尾工作，均已推送并复查：

| 项 | 说明 |
|---|---|
| 文档债 | §14.4 原先只写"见 §14.5"而无数据，已补实测门禁与 GitHub Actions 7/7 结果；§14.5 列出本轮修掉的 5 个 CI 级缺陷（D-12 SSE Linux 空流 / D-13 install.sh CRLF / D-14 冒烟 grep 锚定 / D-15 OpenAPI 快照过期 / D-16 dist 陈旧 chunk） |
| 宣传脚本可复现 | `scripts/build_promo.py` 原先只渲染标题帧，视频靠一次性命令合成；现补齐整条链路（帧→xfade→mp4→gif→预览帧）并自清理中间帧，实测重建产物字节数与已发布文件完全一致 |
| 行尾 | `core.autocrlf=true` 的 Windows 检出把文本写成 CRLF，而**已含 CRLF 的文件会被原样提交**——`install.sh` 就是这样进 CI 的。`.gitattributes` 现为 `* text=auto eol=lf`，对象库统一 LF，Windows 工作区仍可用 CRLF |

**最终状态**：GitHub / GitCode / 本地三端同一提交 `b4888b9`；GitHub Actions
CI、Security、docs、Evals、build-exe、mirror 全部 success。

**仍需人工验证**（不因 CI 绿而消失）：安装脚本端到端下载（需先发 Release 资产）、
`spark update` 的 frozen 自替换、桌面通知在 Firefox/Safari 下的权限行为。
