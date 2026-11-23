# 内置浏览器

Spark 内置浏览器是侧栏里的 Playwright 页面。模型和用户看到的是同一个实时页面，模型动作不会另开桌面浏览器窗口。需要复用已登录的 Chrome / Edge 标签页时走[扩展桥](BROWSER_EXTENSION_BRIDGE.md)，二者互不影响。

默认入口和搜索引擎都是 Bing：

- 空 URL 打开 `https://www.bing.com/`（国内通常落到 `https://cn.bing.com/`）。
- 地址栏输入裸搜索词走 `https://www.bing.com/search?q=...&setlang=zh-Hans`。
- `browser_search` 和 `web_search` 都以 Bing 为主；`web_search` 在 Bing 不可用时才回退 ddgs。

## 工具

| 工具 | 能做什么 |
| --- | --- |
| `browser_open` | 打开 URL；空值打开 Bing 首页 |
| `browser_search` | 在内置浏览器里搜索 Bing，返回标题、URL、摘要和页面快照 |
| `browser_snapshot` | 读标题、URL、可见文本、滚动位置、frame 列表和可交互控件 ref |
| `browser_click` | 按 ref / CSS / role+name / 可见文本点击；支持左右中键、双击、force |
| `browser_type` | 按 ref 或 selector 填入/逐字输入；可清空、回车提交 |
| `browser_press` | 在页面或控件上按键、组合键、连续按键 |
| `browser_hover` | 悬停菜单、提示框 |
| `browser_select` | 选择 `<select>` 的 value / label / index |
| `browser_scroll` | 像素滚动、到顶/到底、把某个 ref 滚进视野 |
| `browser_history` | 后退、前进、刷新 |
| `browser_tabs` | 列出、新建、切换、关闭标签页；自动拾取 popup |
| `browser_wait` | 等 selector、文本、URL 片段或固定秒数 |
| `browser_screenshot` | 视口、整页或单元素截图，落到 `.spark/browser/` |
| `browser_viewport` | 调整页面视口 |
| `browser_eval` | 在当前页面执行 JS 表达式并返回结果 |
| `browser_file_upload` | 给文件输入框上传工作目录内文件 |
| `browser_download` | 从 cookie 鉴权链接或点击下载按钮保存文件到 `.spark/browser/downloads/` |
| `browser_pdf` | 把当前页渲染成 A4 PDF，落到 `.spark/browser/pdf/` |
| `browser_console` | 读控制台日志与页面错误，可按级别/文本过滤并清空 |
| `browser_network` | 读最近的响应和失败请求；URL 会去掉 query，避免令牌泄露 |
| `browser_dialog` | 查看 alert/confirm/prompt，或预先武装 accept/dismiss |
| `browser_trace_start` | 开始 Playwright trace：截图、DOM 快照与源码引用 |
| `browser_trace_stop` | 停止 trace 并保存到 `.spark/browser/traces/*.zip` |
| `browser_metrics` | 读 Chromium DevTools 性能指标；Firefox / WebKit 明确报错 |

## ref 生命周期

`browser_snapshot` / `browser_open` / `browser_search` 会给可见控件写入 `data-spark-ref`，返回的 `interactive[].ref` 可以直接交给点击、输入、悬停、下拉、截图等工具。

ref 带文档代际前缀（例如 `r1a2b-e3`），只对当前文档有效。页面跳转、刷新或 DOM 大改后，旧 ref 会失效；工具会返回 `error_type=stale_or_missing_ref` 和重新 `browser_snapshot` 的提示。这样旧 ref 不会在新页面上误点到另一个同位置控件。

同一个文档内重复 snapshot 会保留已有 ref；新增控件继续向后编号。

snapshot/click/type/eval/screenshot 等工具带 `frame_index`：`-1` 是主页面，`0..N` 对应 `frames[].index`。iframe 内的 ref 只能配合同一个 `frame_index` 使用；跨 frame 不要复用 ref。

## 权限与边界

- 24 个 `browser_*` 全部纳入 `BROWSER_TOOLS`。`full-auto` 下普通动作自动放行，但站点规则与内置敏感站点兜底仍有否决权；其他模式默认逐条确认。
- 浏览器工具不能持久 `allow_always`。页面会跳转、登录态会变化，永久放行等于把未来所有站点都交出去。
- 如果希望浏览器自动化不打断，可在配置中只给浏览器开白名单：

```toml
[[permission_rules]]
action = "allow"
resource = "**"
tool = "browser_*"
```

- 页面只允许公网 `http/https` 和工作目录内 `file://`；每次请求还会再过一次 SSRF 防护，重定向和页面内 fetch 也拦截私网。
- 按域名授权写在 `agent.browser_site_rules`，语法是 `host=allow|read|prompt|deny`；同域取最严。`read` 只拦写动作，`prompt` 让写动作逐条确认，`deny` 连请求都 abort。
- 支付、网银、身份提供方、云控制台默认是 `prompt` 兜底；精确主机或 `*.paypal.com` 这类具体子域规则可以覆盖它，`*=allow`、`*.com` 这类泛化规则不能把敏感站点的写动作降级。
- `prompt` 在 `full-auto` 下仍会要求审批。`spark exec`、队列任务等没有审批器的路径会 fail closed（拒绝执行），不会静默放行；需要无人值守时请把该域名写成 `allow`，或不要访问敏感站点。
- 页面文本进入 untrusted 围栏，不当作系统指令。
- `browser_eval` 能执行任意页面 JS，`browser_file_upload` 能读取工作目录内文件，`browser_download` 会写入工作目录；它们按写动作处理，不能被 `read` 规则放行。
- trace 会记录截图和 DOM，可能包含页面上的个人信息；`.spark/browser/traces/` 按敏感产物管理。
- 截图默认写入工作目录 `.spark/browser/`。

## Web API

侧栏浏览器通过本机 API 驱动：

- `GET /api/browser/state`：`active/url/title/viewport/tabs/home/search_engine/search_url/revision/error`
- `GET /api/browser/shot`：当前画面 PNG；无页面时 204
- `POST /api/browser/navigate`：`{"url": "..."}`；空 URL 进 Bing，裸词走 Bing 搜索
- `POST /api/browser/action`：`{"action":"back|forward|reload|click|type|key", ...}`

打开侧栏“浏览器”tab 且没有活动页时，前端会自动调用空 URL 导航进入 Bing。

## 浏览器体检与 WebKit 部署

```bash
spark browser-doctor
spark browser-doctor --json
```

它会按当前配置真实启动一次浏览器、渲染测试页并返回版本；失败时给出最短修复命令。Chromium 缺 headless shell 时会自动回退到完整 Chromium 二进制；WebKit / Firefox 缺系统库时提示：

```bash
sudo -n env PATH="$PATH" "$(which python)" -m playwright install-deps webkit
```

Firefox 与 WebKit 的浏览器文件默认在 `~/.cache/ms-playwright`；如果部署把 `PLAYWRIGHT_BROWSERS_PATH` 指到只装了 Chromium 的目录，`browser-doctor` 会如实报缺，不会假装已支持。验收时至少在 Chromium、Firefox、WebKit 各打开一个真实本地页面并完成一次点击。
