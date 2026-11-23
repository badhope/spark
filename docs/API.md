# Spark HTTP API 参考

> English: [docs/en/API.md](en/API.md)
> 实现源：`src/spark/web/routes.py`（GET 路由 + POST dispatch 表）、`src/spark/web/_handlers.py`（POST 业务逻辑）。

## 认证

服务默认只监听本机 `127.0.0.1`（`spark web --host 0.0.0.0` 可显式开放到局域网，风险自负）。

- 鉴权实现位于 `routes.py` 的 `_guard` / `_authorized`；非回环来源必须带控制令牌或已配对设备令牌。
- 控制令牌在服务启动时随机生成（`SparkWebState.control_token`），只在控制台对象上提供，不会写进日志；建议作为破窗凭证。
- 手机/远程浏览器优先走短时配对：`POST /api/pair/code`（仅本机）生成 6 位码，`POST /api/pair/claim` 用码换 7 天设备令牌；设备令牌同样通过 `X-Spark-Token` 请求头提交。
- 本机（127.0.0.1 / ::1 / localhost）请求免令牌；其他来源一律 `401`，除非令牌有效。
- 设备令牌默认是“只读/运行”权限：可发起/停止回合、审批/回答、切会话和读取文件/diff；修改设置、插件、直接 shell、PTY、配对管理默认只允许本机控制令牌。本机可临时授予一次 30 分钟 `device_admin`，仅放宽非 shell 设置与插件启停，详见 `docs/REMOTE.md`。
- 远程设备可自行订阅/退订审批推送并发送在线心跳；全量远程审计只允许本机控制面读取。
- 额外防护：Host 头非本机 → `403`；带 Origin 头且非本机来源 → `403`（防 CSRF）；POST 的 Content-Type 必须是 `application/json`，否则 `415`。

## 静态资源

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 返回前端单页（优先 `src/spark/web/dist/index.html`，缺失时回落内置 `index.html`） |
| `GET` | `/<静态文件>` | 伺服 UI 目录内的资源（带路径逃逸检查）；`/assets/*` 带一年 immutable 缓存 |

## 只读 GET 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/status?session_id=` | 会话 / 沙箱模式 / 审批模式 / 上下文用量 / 计划列表 / 环境信息；带 `session_id` 可查后台/IDE 会话而不切换当前指针 |
| `GET` | `/api/config` | 设置视图（部分敏感值打码，并非全部配置）：provider、agent、context（含 `agents_md` / `repo_map_max_chars` / `knowledge_*`）、memory（含 `embedding_*` / `rerank_model`）、mcp_servers、hooks（含 `on_deny`）、replay、updater，以及 `permission_rules` / `approval_profiles` / `active_approval_profile_id` |
| `GET` | `/api/models` | 模型 profile 列表（api_key 打码，含 `active` 标记） |
| `GET` | `/api/agents_md` | 读取工作目录内 AGENTS.md 的内容与状态 |
| `GET` | `/api/remote/status` | 远程入口状态：`local` / `lan` / `public`、公网 origin、临时局域网 URL、已配对设备数；仅本机控制令牌可读 |
| `GET` | `/api/pair/devices` | 已配对设备列表（仅本机；不含令牌明文，含 `online` / `last_path` / `last_session_id` / `scope` / `admin_expires_in` / `admin_used`） |
| `GET` | `/api/remote/audit?limit=` | 远程写操作审计（仅本机；默认 50，最多 500，不记录提示词/文件内容/令牌） |
| `GET` | `/api/push/status` | Web Push 状态：`available` / `public_key` / `subscriptions` / `devices` |
| `GET` | `/api/queue` | 空闲任务队列：`paused` / 各状态计数 / 最近 50 条任务 |
| `GET` | `/api/memory` | 长期记忆：`items` 全量、`search?q=关键词` 检索（带 score）、`stats` 统计 |
| `GET` | `/api/sessions` | 当前工作目录的会话列表（最近 50 条）+ `current` 会话 id |
| `GET` | `/api/session/lease` | 当前会话的租约：`caller` / `lease.owner` / `expires_in`；设备可读 |
| `GET` | `/api/checkpoints` | 当前会话的检查点列表 |
| `GET` | `/api/history` | 当前会话消息历史（含图片的 base64 data URI） |

## POST 接口

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/chat/stream` | `{prompt, images?, session_id?}` | SSE 流式对话；带 `session_id` 时定向到该 runtime（IDE/多客户端不抢 Web 当前会话）。事件：`text_delta` / `reasoning_delta` / `tool_start` / `tool_end` / `approval_needed`（含 diff）/ `compaction` / `context` / `plan` / `turn_end` / `turn_error` / `done`；心跳 `: ping` |
| `POST` | `/api/chat` | `{prompt}` | 非流式对话，返回完整事件数组（调试用） |
| `POST` | `/api/cancel` | — | 取消当前回合与待审批 |
| `POST` | `/api/approval` | `{decision}` | 审批决定：`allow` / `allow_always` / `deny`；仅租约持有设备或本机 |
| `POST` | `/api/session/lease` | `{action, session_id?, takeover?}` | `status` / `acquire` / `renew` / `release` / `takeover`；非持有设备写返回 `409 lease_held` |
| `POST` | `/api/queue` | `{action, prompt?, title?, id?}` | `list` / `create` / `pause` / `resume` / `cancel`；任务只在没有会话忙碌时执行，暂停跨重启保留 |
| `POST` | `/api/pair/code` | `{}` | 生成 6 位短时配对码（仅本机，5 分钟有效） |
| `POST` | `/api/pair/claim` | `{code, name}` | 远程设备用配对码换设备令牌；这是唯一免鉴权的写接口，Host/Origin 仍校验 |
| `POST` | `/api/pair/revoke` | `{device_id}` | 吊销设备令牌（仅本机） |
| `POST` | `/api/remote/admin` | `{action, device_id}` | 本机授予/收回一次 30 分钟 `device_admin` 管理档：`grant` / `revoke`；授予、到期、收回均写审计，一次配对只能申请一次 |
| `POST` | `/api/remote/link` | `{mode?}` | 生成一次性远程链接与 SVG 二维码 data URL；`mode` 为 `auto` / `public` / `lan`；自动模式优先公网，否则按需开启局域网监听（仅本机） |
| `POST` | `/api/remote/stop` | `{}` | 吊销全部设备、清空配对码并关闭临时局域网监听（仅本机） |
| `POST` | `/api/push/subscribe` | `{subscription, events?}` | 注册浏览器 Push 订阅；`events` 默认 `["approval"]`，设备令牌可用 |
| `POST` | `/api/push/unsubscribe` | `{endpoint}` | 删除 Push 订阅；设备令牌可用 |
| `POST` | `/api/push/test` | `{}` | 向本机当前订阅发送测试通知（仅本机控制面） |
| `POST` | `/api/presence/heartbeat` | `{session_id?}` | 刷新设备在线心跳并续租当前会话；需要设备令牌，返回设备公开信息与 `lease` |
| `POST` | `/api/models/save` | `{name, base_url, model, provider, api_key, id?}` | 新建或更新模型 profile |
| `POST` | `/api/models/delete` | `{id}` | 删除 profile；若删除的是活跃 profile 则清空 `active_profile_id` |
| `POST` | `/api/models/activate` | `{id}` | 切换活跃 profile 并重建 AgentLoop；agent 忙时 `409` |
| `POST` | `/api/demo` | `{demo: true/false}` | 一键进入/退出 Mock 展示模式；进入前把真实 provider 保存为 `__last_real__` 档案，退出原样恢复；无真实档案退出返回 `409`，非布尔参数 `400`；agent 忙时 `409` |
| `POST` | `/api/agents_md` | `{content}` | 写工作目录内的 AGENTS.md（文件名受白名单校验） |
| `POST` | `/api/memory` | `{action}` | `add`（content/type/importance）/ `delete`（id）/ `optimize` |
| `POST` | `/api/sessions/new` | `{isolated?: true, title?}` | 新建会话；`isolated=true` 创建 runtime 但不移动 Web 当前指针，返回 `session_id` |
| `POST` | `/api/sessions/delete` | `{session_id}` | 删除会话；删当前会话时自动切换 |
| `POST` | `/api/sessions/archive` | `{session_id, archived}` | 归档/恢复会话；归档保留消息与检查点、默认列表隐藏；归档当前会话时自动切换到下一个活跃会话，切到归档会话隐式恢复；仅本机或 `device_admin` |
| `POST` | `/api/sessions/switch` | `{session_id}` | 切换会话并返回消息历史；目标是归档会话时自动恢复 |
| `POST` | `/api/checkpoints/rollback` | `{checkpoint_id}` | 回滚到检查点（恢复工作区文件 + 对话历史） |
| `POST` | `/api/messages/truncate` | `{message_id, restore_files?}` | 消息级 undo：删除该消息及其后全部消息。`restore_files: true` 时一并把工作区恢复到该消息之前最近的检查点；没有可用快照时返回 `files_restored: false` + `restore_reason`，不假装还原过 |
| `POST` | `/api/settings` | provider/agent/context/memory/hooks/mcp_servers/replay/updater 各节 | 保存设置并重建 AgentLoop；部分字段校验失败返回 `400` 且不落盘，agent 忙 `409`；缺省字段通常保持现状（切换目标时凭据池例外） |
| `POST` | `/api/inline_edit` | `{path, selected, instruction, mode?, start_line?, end_line?}` | 单次内联编辑/下一编辑建议（`mode=edit` 需 `selected`，`nes` 只需光标上下文）；返回 `{ok,text,changed,latency_ms,base_sha256}`，不写盘；仅本机或 `device_admin` |
| `POST` | `/api/inline_edit/apply` | `{path, selected, replacement, base_sha256}` | 显式应用内联预览：`base_sha256` 必须匹配当前文件，`selected` 必须在文件里唯一出现，否则 `409`；成功写回并返回 unified diff |
| `POST` | `/api/plugins` | `{action, name?, require_signature?}` | `enable` / `disable` / `policy`；`policy` 写 `[plugins].require_signature`，仅本机或 `device_admin`，开启后未签名插件 enable 返回 `400` |
| `POST` | `/api/test` | `{base_url, model, api_key?}` | 探活模型接口（拒绝向与已配置不同的地址发送存储的密钥） |

未知路径一律 `404`；请求体超过 32 MiB 返回 `413`；JSON 解析失败 `400`。

## `/api/settings` 字段契约

缺省字段通常保留现值，但切换 provider 或地址且未提交 `api_keys` 时会清空旧凭据池。部分字段先转换类型，非预期类型的节可能被忽略，未知字段不保证报错。数值越界等校验失败返回 `400`，例如 `{"error": "invalid settings: agent.shell_timeout_sec: Input should be less than or equal to 3600"}`；候选配置校验失败会回滚内存且不保存。保存或重建失败可能返回 `500`。

对 `openai_compat` 而言，切换到不同的 provider/地址必须有显式凭据来源：请求带上 `api_key`，或带上 `api_key_env` 绑定一个已存在的环境变量名。两者都没有时返回 `400`——存储的 `api_key` 与旧的 `api_key_env` 都只属于旧端点，静默沿用等于把凭据发给新主机。切换端点且未提交 `api_key_env` 时该绑定会被清空；环境变量的值只用于可用性校验，绝不写进配置文件。`mock` / `ollama` 不需要凭据，不做此校验。

| 节 | 可写字段 |
| --- | --- |
| `provider` | `name`、`base_url`、`model`、`api_key`（空 = 保留）、`api_key_env`（合法环境变量名）、`api_keys`、`fallback_models`、`small_model` |
| `agent` | `approval`、`sandbox_mode`、`effort`、`workdir_only`、`protected_paths`、`system_prompt`、`show_*`、`shell_timeout_sec`、`max_tool_rounds`、`tool_preset`、`max_repeat_calls`、`max_turn_tokens`、`max_output_chars` |
| `context` | `agents_md`（仅裸文件名）、`max_fragment_chars`、`history_budget_chars`、`max_context_tokens`、`compact_threshold`、`keep_recent_messages`、`repo_map_enabled`、`repo_map_max_chars`、`knowledge_enabled`、`knowledge_top_k`、`knowledge_max_chars` |
| `memory` | `enabled`、`top_k`、`capacity`、`embedding_model`、`auto_extract`、`embedding_enabled`、`embedding_base_url`、`embedding_api_key`（空 = 保留）、`rerank_model` |
| `replay` | `enabled` |
| `updater` | `mode`（`notify` / `off`） |
| `hooks` | `[{event, command, args, name, timeout_sec, on_deny}]`；`on_deny` 缺省时沿用同事件、同命令的既有值 |
| `mcp_servers` | 与 `/api/config` 回显结构一致；敏感 env / header 值回传 `[REDACTED]` 时按原值恢复 |

主要数值边界见 `src/spark/config.py` 的 pydantic `ge`/`le`；hooks 的 `timeout_sec` 则单独转换并钳制到至少 1，空值使用 15。

`api_keys` 提交时整体替换：不要将 GET 返回的掩码重新作为密钥提交；空数组清空。长密钥掩码会暴露首尾各四位。MCP env 默认合并，`env_replace: true` 才整体替换；更改目标时旧 env 不会自动带往新目标。MCP header 的占位符仅在同名服务器和相同 URL 下恢复，否则被丢弃；env 占位符无法安全恢复时返回错误。配置文件中密钥以明文保存，应限制访问。

### `permission_rules` / `approval_profiles` 写接口

`GET /api/config` 返回 `permission_rules`、`approval_profiles`、
`active_approval_profile_id`，其中权限规则仅为用户层，不含 project/local 层。`permission_scopes` 不在该响应中。`POST /api/settings` 收到这四个键中的任何一个仍会明确返回 `400`（`... is read-only over /api/settings`）——治理面走下面的独立端点，两套语义不混用。

之所以单独开端点而不是塞进 `/api/settings`：权限规则是 deny 跨层优先的安全边界
（`spark.policy.decide` + `_load_scoped_permission_rules`），审批档案激活会改写
审批模式、工具白名单与 system prompt。它们的失败模式与普通设置不同（会静默削弱
或放大权限），因此需要独立反馈、独立审计。

| 方法 | 路径 | 请求体 | 语义 |
| --- | --- | --- | --- |
| `POST` | `/api/permissions/rules` | `{scope: "user", rules: [{action, resource, tool}]}` | 整体替换 `user` 层规则；`project` / `local` 层由工作目录文件装配，`scope` 传其它值返回 `400` |
| `POST` | `/api/permissions/rules/delete` | `{scope, index}` | 按序号删除（glob 字符串不能当稳定主键）；越界返回 `404`，负数返回 `400` |
| `POST` | `/api/approval_profiles/save` | `{id?, name, approval, tools, system_prompt, model_profile_id}` | 与 `/api/models/save` 同构：无 `id` 为新建；新建时 `approval` 必填；`model_profile_id` 不存在返回 `404` |
| `POST` | `/api/approval_profiles/delete` | `{id}` | 删除档案；删除活跃档案时清空 `active_approval_profile_id` |
| `POST` | `/api/approval_profiles/activate` | `{id}` | 激活并重建 AgentLoop（走 `settings_lock` → 非阻塞 `turn_lock`）；档案带 `model_profile_id` 时联动切换模型档案 |

共同约束：全部在 `state.settings_lock` 内完成「校验 → 应用 → `save_config`」；
规则类写入额外用 pydantic 模型复核整份配置，失败回滚内存并返回 `400`；每个成功的
写操作都会落一条审计事件（`permission_rules_written` / `approval_profile_written`
/ `approval_profile_deleted` / `approval_profile_activated`，见 `spark.audit`）。
非法规则条目一律报错而非静默丢弃——用户以为加了一条 `deny` 而它没生效是更糟的结果。

## SSE 事件格式

`/api/chat/stream` 返回 `text/event-stream`，每条事件为 `data: {JSON}\n\n`：

```jsonc
{"type": "text_delta", "text": "…"}                    // 增量文本（已脱敏）
{"type": "reasoning_delta", "text": "…"}               // 思考流（模型支持时）
{"type": "tool_start", "tool_call": {…}}               // 工具开始执行
{"type": "tool_end", "tool_call": {…}, "result": {…}}  // 工具结果
{"type": "approval_needed", "approval": {"summary": "…", "diff": "…"}}  // 等待审批
{"type": "compaction", "text": "…"}                    // 上下文压缩
{"type": "context", "data": {"usage": {"used": 1, "limit": 2}}}  // 上下文用量
{"type": "metrics", "data": {"ttft_ms": 1234, "total_ms": 5678}}  // 本轮延迟账本
{"type": "plan", "plan": [ … ]}                        // 计划更新
{"type": "turn_end", "text": "…"}                      // 回合结束
{"type": "turn_error", "text": "…"}                    // 回合出错
{"type": "done"}                                       // 流结束
```

连接断开（写失败或对端关闭）会自动取消当前回合；15 秒无数据发送 `: ping` 心跳。

> AI生成
