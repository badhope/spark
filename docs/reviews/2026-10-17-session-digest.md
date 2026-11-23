# 会话精华锚点（2026-10-17）——新会话从这里接手

> **定位说明（2026-11-23）**：本文写作于“本地优先”旧口径时期，该口径已废止。现行定位见 [PRODUCT_POSITIONING](../PRODUCT_POSITIONING.md)；本文仅作历史审计与事实记录，不再作为路线依据。


> **终态更新（2026-10-18 收官）**：P0/P1 全部交付、P2 首批交付，当前基线 **v0.9.6 @ ba622a8 系**
> ——五仓对齐、PyPI 已上线（pip install spark-agent / uvx spark-agent）、主仓 10/10 检查绿、
> 文档站 gh-pages 已部署、发布链 tag 触发全自动化、架构图表入库 docs/assets/diagrams/。
> 密钥已安放主仓 Actions Secrets（MIRROR_*×4 + PYPI_API_TOKEN，轮换方法见 docs/diagrams-ops.md）。
> 本地工作区已清空——**远程仓库为唯一权威副本**。
>
> 下一步接续：P2 余项（LangGraph 迁入专项 / ACP server / OTel→Langfuse / 双粒度时间轴 UI /
> 前端造轮子替换 / 火焰图 Web 面板）→ P3 信任链+AEF。发版 SOP 见 docs/diagrams-ops.md。

> 本文档是当天的浓缩快照，替代零散对话记录。旧内容以本文档为准。

## 项目一句话
Spark = 本地自托管 AI 编程 Agent harness（Python 3.11+，MIT，v0.9.6）。
战略主轴：**做证据标准的主人**（AEF），残余优势 = R1 信任链一体化 / R2 内嵌vs旁挂 / R3 开放格式反锁定 / R4 工程执行。所有"独有"主张必须先证伪式调研。

## 五平台发布矩阵（已全部跑通）
- 主仓 GitHub/badhope；纯镜像：Gitee/badhope、GitCode/badhope、GitHub/X33834、GitHub/Morningstar202604
- 凭据：`/home/z/my-project/.env`（**会被平台重置！备份在 probe/env.backup**，推送前 awk 校验长度）
- 本地推送：`bash /home/z/my-project/spark_push.sh [分支]`（token 动态注入）
- CI 镜像：`.github/workflows/mirror.yml`（MIRROR_* 四 secrets 已注入；**checkout 的 github.com extraheader 会覆盖 URL 凭据 → 推镜像前必须 unset**）
- 全部仓库 public + MIT

## 当前状态（2026-10-17 深夜）
- v0.9.3（3a6e124 + 190d9cd）：三 P0 修复（bwrap --share-net / FTS session 子查询 / embedding opt-in+独立端点）+ registry 缓存 + Compactor 走 Provider 抽象（MockProvider 跳过）+ mirror CI + CHANGELOG
- 测试基线：**630 passed / 5 skipped**（全量 ~100s；分片 4 份跑防会话 504）
- 质检门槛：ruff check src/spark tests ＋ mypy src/spark --ignore-missing-imports ＋ bandit -r src/spark -ll -ii -c bandit.yaml（提交前本地全跑）

## 已拍板决策（按时间序）
1. 五平台矩阵、主仓+镜像、MIT 全公开
2. 定位不变：本地优先/密钥在手/BYO模型；原则=生态优先不造轮子，"好的就换"
3. LangGraph **迁入**（interrupt=审批/SqliteSaver=turn恢复/子图=子代理，68 测试做行为等价网）
4. IDE 模式：一键切 IDE（ACP）+ 一键切 agent（agent profiles）——Spark 统一座舱
5. 分发双轨：pip/uvx 先行 + 桌面 EXE 软件轨
6. 竞品教训全量纳入（H1–H18 横向扩展地图，master plan §5.5）
7. 大招 A：**AEF 开放标准**（任务证据文件）+ 参考实现 + aef-export 转换器
8. 大招 B：座舱五件套拆可嵌入库（审批/审计/回放/回滚/计账）
9. 全量纳入原则：连"不推荐"的（IM 网关/插件市场）也要做，排在信任链之后
10. 扩展≠照抄：每项标"先例+我们改什么"，否则不立项
11. 工作方式：大块工作一次提交（勿碎提交）；文档少而精、重可视化；CHANGELOG 只写重点

## 路线图（master plan §10，DoD 制）
P0 卫生周 ✅ → **P1 v0.10 模块化+分发（下一步）** → P2 v0.11 座舱+协议+火焰图 → P3 v0.12 信任链+AEF → P4 v0.13 横向扩展全量 → P5 v0.14-15 AEF 推广+合规包

## P1 开工清单（照做即可）
1. 拆包：spark-core/providers/tools/mcp/web/tui + import-linter（core 禁反向依赖）
2. Web 服务端换 FastAPI+uvicorn+sse-starlette（OpenAPI→前端 orval/openapi-typescript codegen）
3. litellm：fallback 链 + 凭据池 + usage 全回填（修 estimated-only）+ tag 成本归账
4. 模型目录：models.dev/Catwalk 替换 routing.py 硬编码
5. LSP 诊断闭环（pygls，py/ts 先行）；repo_map 加 git PageRank
6. 文档站 v1（mkdocs-material + Diátaxis + mkdocstrings 自动配置参考）
7. uvx/pip 发布链 + rich CLI + `spark agent use` / `spark ide attach` 命令骨架

## 真空白候选池（先例核验为零，P3/P4 落地）
MCP 沙箱运行时（"MCP 的 systemd"）/ PR 证据包 / provenance blame / 夜间安全包 / 团队记忆 MR 化 / 上下文火焰图

## 环境坑备忘（本沙盒）
- PyPI 大文件（>20MB）卡死 → 一律 `UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/`
- 前台命令 >60s 易 504 → setsid 分离 + 短轮询；pytest 分片跑；大提交前后别跑长命令
- raw.githubusercontent 被墙 → jsDelivr（cdn.jsdelivr.net/gh/user/repo@branch/file）
- 探索脚本与调研 JSON 留档在 /home/z/my-project/probe/（search*.json、pages.json、analysis_notes.md）

## 关键文档索引
- docs/reviews/2026-10-17-audit-and-direction.md —— 审查+方向+核验+决议（六部分）
- docs/reviews/2026-10-17-master-plan.md —— 11 章建设计划（架构/全端分册/优化/风险/里程碑）
- docs/competitive-comparison.md —— 官方竞品矩阵（2026-10-14）
