# 浏览器扩展桥

扩展桥解决的是“内置浏览器没有你的登录态”这件事：它不启动新的隔离浏览器，而是让 Playwright MCP 通过官方扩展接管你正在用的 Chrome / Edge 标签页。已登录的邮箱、后台、文档、代码托管平台可以继续用，不必把 Cookie 复制进 Spark。

## 它和内置浏览器的分工

| 场景 | 用哪个 |
| --- | --- |
| 从零打开网页、跑端到端测试、隔离环境 | 内置浏览器（Chromium / Chrome / Edge / Firefox / WebKit） |
| 复用已登录的 Chrome / Edge 标签页 | 扩展桥 |
| 需要 CDP 接管一个独立启动的 Chromium | `browser_cdp_endpoint` |
| 要 DevTools 级网络/性能分析 | Chrome DevTools MCP，或内置 `browser_metrics` |

扩展桥不是第六个引擎，也不绕过 MCP 审批：它只是把“页面从哪来”换成用户已经开着的浏览器；默认只放行只读工具，写工具必须显式加入白名单。

## 安装

1. 在 Chrome 或 Edge 安装 Playwright 官方扩展。安装说明随 Playwright 仓库的 `packages/extension` 更新：[microsoft/playwright › packages/extension](https://github.com/microsoft/playwright/tree/main/packages/extension#readme)。
2. 在 Spark 的“设置 → MCP”里点“Playwright 扩展桥”预设。它会写入：

```json
{
  "command": "npx",
  "args": ["-y", "@playwright/mcp@latest", "--extension"]
}
```

3. 保存后手动启动该 MCP。首次启动时，扩展会要求确认要接管的标签页；只确认你愿意让 Agent 操作的那个窗口。
4. 让 Agent 调一次 `browser_snapshot`。返回的 URL 与标题应与你当前标签页一致；如果不一致，先检查是不是开在了另一个 Chrome Profile。

`--extension` 只支持 Chrome / Edge。Firefox 与 WebKit 没有这条桥，继续用内置浏览器或 CDP。

## 审批与工具白名单

扩展桥是独立 MCP 进程，Spark 不知道它当前接管的是哪个标签页 URL，因此**内置浏览器的按域名规则不作用于扩展桥的 MCP 工具**。这不是遗漏，而是边界：宿主页面在用户自己的浏览器里，host 只有扩展和 MCP 知道。

默认预设只放行四个只读工具：

```text
browser_snapshot  browser_take_screenshot  browser_console_messages  browser_network_requests
```

需要点击、输入或切换标签页时，在“设置 → MCP → 该 server 的允许工具白名单”里显式加入 `browser_click`、`browser_type`、`browser_press_key`、`browser_tabs` 等；不要在 `full-auto` 下开着扩展桥和宽白名单。MCP 写工具会按 `is_mcp_tool` 走审批，但 `full-auto` 会跳过确认，这一点必须由用户自己守住。

内置浏览器仍保留完整的按域名授权：

- `deny`：整站阻断，连读取都不允许。
- `read`：允许读页面，点击/输入/执行 JS 等写动作拒绝。
- `prompt`：读放行，写动作逐条确认。
- `allow`：读写都放行。

同域多规则取最严；精确主机或具体子域规则可以覆盖内置敏感站点兜底，泛化规则不行。内置兜底对支付、网银、身份提供方、云控制台默认为 `prompt`，读取不打扰，写动作一定停下来。扩展桥不享受这层兜底，所以更依赖只读白名单与人工审批。

## 验收清单

- [ ] 扩展在 `chrome://extensions` 或 `edge://extensions` 里显示已启用。
- [ ] `browser_snapshot` 返回的是当前标签页，不是 `about:blank`。
- [ ] 扩展桥的允许工具白名单默认只有四个只读工具，`browser_click` 不在其中。
- [ ] 在内置浏览器打开敏感站点并点一次按钮，审批层出现 `sensitive site needs confirmation` 或 `browser_site_rules prompt`，而不是直接执行。
- [ ] 把该站点改成 `deny` 后，同一动作被拒绝且页面请求被 route 拦截。
- [ ] 关闭扩展桥 MCP 后，内置浏览器仍能独立工作，二者互不影响。

## 已知边界

- 扩展桥依赖扩展与浏览器保持运行；浏览器退出后桥会失联，需要重启 MCP。
- 扩展桥拿到的是用户真实 Profile，可能带着企业 SSO、Cookie 与本地存储；不要在不可信仓库里默认开启。
- Spark 不会替扩展桥保存密码，也不读取浏览器密码库；它只操作已经渲染出来的页面。
- 站点规则在扩展桥侧是“调用前守卫”；页面内的请求拦截与 SSRF 防护仍由内置浏览器 worker 负责。扩展桥的深层网络拦截交给浏览器自身与 MCP 的权限模型，这是当前的诚实边界。
