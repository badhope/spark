# 企业控制面（/api/v1）

这是 Wave 2 的第一个增量：在现有本地 Agent 之外新增一个**企业控制面**，负责租户、项目、任务、成果物和审计。它不替换本地 Web UI，也不改变现有 AgentLoop。

## 1. 架构

```text
Keycloak (OIDC) ── Bearer JWT ──┐
                                ▼
                    FastAPI /api/v1
                    ├─ auth: dev headers / OIDC JWKS
                    ├─ authz: Casbin RBAC (domain = tenant)
                    ├─ data: SQLAlchemy (SQLite dev / PostgreSQL prod)
                    ├─ gateway: LiteLLM Router (provider fallback seam)
                    └─ audit: audit_events 表
```

- 本地/测试：SQLite + dev auth（仅回环，默认）。
- 企业：PostgreSQL + Keycloak OIDC + Casbin 策略。
- `LiteLLM Router` 已接成模型网关适配器；控制面暂不直接调用模型，后续办公/业务工作台复用它。

## 2. 本地快速启动（无容器）

```bash
cd D:/spark-workspace/spark
source ../dev.sh          # 或 PowerShell: ..\dev.ps1
uv sync --extra dev --extra platform --extra enterprise
spark enterprise --host 127.0.0.1 --port 18800
```

默认行为：

- 数据库：`$SPARK_HOME/control.db`（SQLite，自动建表）
- 身份：dev 模式，回环；默认用户 `dev`，角色 `admin`，租户 `default`
- 地址：`http://127.0.0.1:18800/api/v1/health`

快速验证：

```bash
curl -s http://127.0.0.1:18800/api/v1/health
curl -s http://127.0.0.1:18800/api/v1/me
curl -s -X POST http://127.0.0.1:18800/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"会议纪要试点"}'
```

dev 模式可用请求头模拟不同身份：

```text
X-Spark-User: alice
X-Spark-Roles: member
X-Spark-Tenant: default
```

`member` 只读，`admin` 可写。dev 模式不允许绑定非回环地址；要对外提供服务必须切到 OIDC。OIDC 模式下租户只从签名 claim `tenant`（Keycloak mapper）读取，**不信任 `X-Spark-Tenant` 请求头**，避免用户自行切换租户。

## 3. 数据库迁移

```bash
export SPARK_CONTROL_DATABASE_URL='sqlite:///D:/spark-workspace/.spark-home/control.db'
alembic upgrade head
```

- `alembic.ini` / `migrations/` 已就绪，初始版本 `7dbcd0bb0272`。
- 生产 PostgreSQL 建议使用 `postgresql+psycopg://...`；迁移会使用同步驱动。
- `SPARK_CONTROL_AUTO_CREATE_TABLES=false` 可关闭 SQLite 的自动建表，强制走迁移。

## 4. 容器：PostgreSQL + Keycloak

> 当前机器没有 Docker/Podman，以下路径未在本机实跑；文件已按 Compose 语法校验。有容器环境时直接使用。

```bash
cd D:/spark-workspace/spark
uv sync --extra enterprise --extra postgres
cd deploy
cp .env.example .env        # 修改所有密码，不要提交 .env
docker compose -f compose.yml up -d
```

端口：

| 服务 | 地址 |
|---|---|
| PostgreSQL | `127.0.0.1:55432` |
| Keycloak | `http://127.0.0.1:18080` |
| 控制面 | `http://127.0.0.1:18800` |

Realm 导入：`deploy/keycloak/spark-realm.json`

- realm：`spark`
- client：`spark-api`（public + direct access grants）
- roles：`admin`、`member`

### 4.1 创建开发用户

用 Keycloak 管理台（`http://127.0.0.1:18080`，账号来自 `deploy/.env`）创建用户，或在容器内执行：

```bash
docker compose -f compose.yml exec keycloak \
  /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 --realm master \
  --user "$KEYCLOAK_ADMIN" --password "$KEYCLOAK_ADMIN_PASSWORD"

docker compose -f compose.yml exec keycloak \
  /opt/keycloak/bin/kcadm.sh create users -r spark \
  -s username=dev -s enabled=true -s email=dev@example.com

docker compose -f compose.yml exec keycloak \
  /opt/keycloak/bin/kcadm.sh set-password -r spark \
  --username dev --new-password 'replace-with-a-strong-password'

docker compose -f compose.yml exec keycloak \
  /opt/keycloak/bin/kcadm.sh add-roles -r spark \
  --uusername dev --rolename admin
```

### 4.2 取 token 并调用控制面

```bash
TOKEN=$(curl -s -X POST \
  http://127.0.0.1:18080/realms/spark/protocol/openid-connect/token \
  -d grant_type=password -d client_id=spark-api \
  -d username=dev -d password='replace-with-a-strong-password' \
  | jq -r .access_token)

SPARK_CONTROL_DATABASE_URL='postgresql+psycopg://spark:CHANGE_ME@127.0.0.1:55432/spark' \
SPARK_CONTROL_AUTH_MODE=oidc \
SPARK_CONTROL_OIDC_ISSUER='http://127.0.0.1:18080/realms/spark' \
SPARK_CONTROL_OIDC_AUDIENCE='spark-api' \
SPARK_CONTROL_OIDC_JWKS_URL='http://127.0.0.1:18080/realms/spark/protocol/openid-connect/certs' \
spark enterprise --host 127.0.0.1 --port 18800
```

```bash
curl -s http://127.0.0.1:18800/api/v1/me \
  -H "Authorization: Bearer $TOKEN"
```

## 5. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SPARK_CONTROL_DATABASE_URL` | `$SPARK_HOME/control.db`（SQLite） | 生产用 `postgresql+psycopg://...` |
| `SPARK_CONTROL_AUTH_MODE` | `dev` | `dev` / `oidc` |
| `SPARK_CONTROL_ALLOW_DEV_AUTH` | `true` | 生产必须设 `false` |
| `SPARK_CONTROL_DEV_USER` / `_DEV_ROLES` / `_DEV_TENANT` | `dev` / `admin` / `default` | dev 身份默认值 |
| `SPARK_CONTROL_OIDC_ISSUER` | 空 | 例如 `http://127.0.0.1:18080/realms/spark` |
| `SPARK_CONTROL_OIDC_AUDIENCE` | 空 | 例如 `spark-api` |
| `SPARK_CONTROL_OIDC_JWKS_URL` | 空 | 留空时用 issuer + Keycloak 标准路径 |
| `SPARK_CONTROL_CASBIN_MODEL` | 包内 `control/policies/model.conf` | 自定义策略模型 |
| `SPARK_CONTROL_CASBIN_POLICY` | 包内 `control/policies/policy.csv` | 自定义策略 |
| `SPARK_CONTROL_AUTO_CREATE_TABLES` | SQLite 为 true | 生产建议 false，走 Alembic |
| `SPARK_CONTROL_ARTIFACT_ROOT` | `$SPARK_HOME/artifacts` | 办公成果物本地存储根目录 |
| `SPARK_CONTROL_DOCUMENT_ROOT` | `$SPARK_HOME/documents` | 文档导入根目录（API 只读此目录） |
| `SPARK_CONTROL_IMAP_HOST` | 空 | IMAP 服务器；与 USER/PASSWORD 同时配置才启用 |
| `SPARK_CONTROL_IMAP_PORT` | `993` | IMAPS 端口 |
| `SPARK_CONTROL_IMAP_USER` / `_IMAP_PASSWORD` | 空 | 邮箱凭据，只从环境读取，API 不返回 |
| `SPARK_CONTROL_IMAP_MAILBOX` | `INBOX` | 默认邮箱 |
| `SPARK_CONTROL_IMAP_SSL` | `true` | 是否使用 IMAPS |

## 6. API（第一增量）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康（DB/auth/gateway 状态） |
| GET | `/api/v1/me` | 当前身份与租户 |
| GET | `/api/v1/models` | 模型网关状态（admin） |
| GET/POST | `/api/v1/tenants` | 租户（admin） |
| GET/POST | `/api/v1/projects` | 项目（租户隔离） |
| GET/POST | `/api/v1/tasks` | 任务（可按 project_id 过滤） |
| GET | `/api/v1/tasks/{id}` | 任务详情 |
| GET/POST | `/api/v1/tasks/{id}/artifacts` | 成果物 |
| GET | `/api/v1/audit` | 审计事件（admin） |

## 7. 办公工作台：会议纪要与行动项

打开 `http://127.0.0.1:18800/office`（dev 模式；OIDC 模式在页面右上角填 Bearer Token）。

第一增量是**确定性**的：不依赖模型，把会议转写解析为

- 参会人员
- 议题
- 决议/结论（带来源行号）
- 行动项（负责人、截止时间、来源行号；缺失标“待确认”）
- Markdown + docx + PPTX + PDF 成果物
- 人工“批准纪要”状态

API：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/office/meetings` | 最近会议 |
| POST | `/api/v1/office/meetings` | 生成纪要（自动创建 Office 项目） |
| GET | `/api/v1/office/meetings/{id}` | 纪要详情 |
| POST | `/api/v1/office/meetings/{id}/approve` | 人工批准 |
| GET | `/api/v1/office/meetings/{id}/download?format=md\|docx\|pptx\|pdf` | 下载成果物 |

说明：

- 转写只在生成时处理，不写入任务 payload；持久化的是结构化纪要和成果物。
- 行动项必须带负责人/截止时间，缺失时页面明确显示“待确认”，不伪装成确定结论。
- 下一步再接模型摘要、会议室音频输入、模板与审批流。

### 7.1 报告 / 表格

同一 `/office` 页面的“报告 / 表格”页签：

- 指标格式：`指标,实际,目标,单位[,lower]`；`lower` 表示越低越好（返工率、时延）。
- 输出 Markdown、Excel（含“实际 vs 目标”柱状图）、Word、PPTX、PDF。
- API：`/api/v1/office/reports` 列表/生成/详情/批准/下载（md/xlsx/docx/pptx/pdf）。
- 当前同样是确定性生成；模型撰写报告正文后置。

### 7.2 文档导入

- 支持 `.docx` / `.xlsx` / `.pptx` / `.pdf` / `.eml` / `.msg` / `.txt` / `.md` / `.csv` / `.json` / `.yaml` / `.toml` / `.html` / `.png` / `.jpg` / `.jpeg` / `.bmp` / `.tif` / `.tiff` / `.webp`。
- 两种导入方式：
  - 浏览器上传：`POST /api/v1/office/documents/upload`（multipart `file`，最大 25MB），文件存到 `SPARK_CONTROL_DOCUMENT_ROOT/uploads/`；
  - 已有文件：`POST /api/v1/office/documents/extract`，body `{"filename": "meeting.docx"}`。
- IMAP 拉取：配置 `SPARK_CONTROL_IMAP_*` 后，`POST /api/v1/office/documents/imap`（body `{"limit":5,"search":"ALL"}`）拉取最新邮件，保存为 `inbox/*.eml` 并返回可解析文本；`/office` 页面有“从 IMAP 拉取最新邮件”按钮。
- 生成会议纪要时可传 `document_filename`；文档文本与手写 transcript 合并解析。
- 扫描版 PDF / 图片走 RapidOCR（本地 ONNX，不调用外部 OCR 服务）；PDF 无嵌入文本时自动 OCR 前 30 页。
- Outlook `.msg` 通过 extract-msg 解析；不接受任意服务器路径。

## 8. 当前边界

- 这是控制面第一增量：**没有** 完整 OIDC 登录页、SCIM、组织成员管理 UI、RLS 迁移、对象存储、Temporal 工作流。
- 会议纪要/报告是确定性解析；模型摘要与正文撰写尚未接入。
- 会议室音频转写未接入；邮箱支持 IMAP 与 `.eml/.msg` 文件，IM/日历连接器未做。
- 审计目前写 `audit_events` 表；企业版后续加导出/SIEM/防篡改。
- Casbin 策略先用文件；后续可接数据库 adapter 与 OPA。
- `LiteLLM Router` 是适配器，未接管现有 AgentLoop 的 provider；Wave 2 后续再接预算/限流/虚拟 key。
- dev auth 只允许回环；生产务必 `SPARK_CONTROL_AUTH_MODE=oidc`、`SPARK_CONTROL_ALLOW_DEV_AUTH=false`。
