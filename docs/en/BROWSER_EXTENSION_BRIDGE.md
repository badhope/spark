# Browser extension bridge

The extension bridge exists for one job: reuse the Chrome / Edge tabs you are already signed in to, instead of launching another isolated browser. Playwright MCP's official extension attaches to the running browser, so mail, dashboards, docs and code hosts keep their existing session without exporting cookies into Spark.

## How it differs from the built-in browser

| Need | Use |
| --- | --- |
| Open pages from scratch, run E2E, stay isolated | Built-in browser (Chromium / Chrome / Edge / Firefox / WebKit) |
| Reuse a signed-in Chrome / Edge tab | Extension bridge |
| Attach to a separately launched Chromium over CDP | `browser_cdp_endpoint` |
| DevTools-grade network and performance inspection | Chrome DevTools MCP, or built-in `browser_metrics` |

The bridge is not a sixth engine and does not bypass MCP approvals. It changes where the page comes from; only read-only tools are exposed by default, and write tools must be added to the allowlist explicitly.

## Install

1. Install the official Playwright extension in Chrome or Edge. Follow the current instructions in [microsoft/playwright › packages/extension](https://github.com/microsoft/playwright/tree/main/packages/extension#readme).
2. In Spark, open Settings → MCP and add the **Playwright extension bridge** preset. It writes:

```json
{
  "command": "npx",
  "args": ["-y", "@playwright/mcp@latest", "--extension"]
}
```

3. Save, then start that MCP manually. On first launch the extension asks which tab to attach to; approve only the window you want the agent to control.
4. Ask the agent for `browser_snapshot`. The returned URL and title must match your active tab. If they do not, check whether the extension is installed in a different Chrome profile.

`--extension` supports Chrome and Edge only. Firefox and WebKit keep using the built-in browser or CDP.

## Approval and tool allowlist

The bridge is a separate MCP process. Spark does not know which tab URL it currently controls, so the built-in browser's per-domain rules do **not** apply to the bridge's MCP tools. That is a boundary, not an omission: the host lives in the user's browser and only the extension and MCP know it.

The preset only allows four read-only tools:

```text
browser_snapshot  browser_take_screenshot  browser_console_messages  browser_network_requests
```

To click, type or switch tabs, explicitly add `browser_click`, `browser_type`, `browser_press_key`, `browser_tabs` (and only what you need) to that MCP server's allowed-tools list. Do not combine `full-auto` with the bridge and a broad allowlist: MCP write tools otherwise follow the normal approval path, but `full-auto` skips the prompt, and that is a decision the user must own.

The built-in browser keeps full per-domain rules:

- `deny`: block the whole site, including reads.
- `read`: allow reading; clicks, typing and JS evaluation are refused.
- `prompt`: reads pass; every write asks for approval.
- `allow`: reads and writes pass.

When several rules match a host, the strictest wins. A concrete host or subdomain rule (for example `*.paypal.com=allow`) may override the built-in sensitive-site fallback; broad rules such as `*=allow` or `*.com=allow` may not unlock sensitive writes. The fallback covers payment, banking, identity providers and cloud consoles with `prompt`: reads stay quiet, writes always stop. The bridge does not get that fallback, which is why its default allowlist is read-only and approval must stay human.

## Acceptance checklist

- [ ] The extension is enabled in `chrome://extensions` or `edge://extensions`.
- [ ] `browser_snapshot` returns the active tab, not `about:blank`.
- [ ] The bridge's allowlist contains only the four read-only tools; `browser_click` is absent.
- [ ] In the built-in browser, clicking a button on a sensitive host raises `sensitive site needs confirmation` or `browser_site_rules prompt` instead of executing.
- [ ] After switching that host to `deny`, the same action is refused and the page request is aborted by the route guard.
- [ ] Stopping the bridge MCP leaves the built-in browser working; neither depends on the other.

## Known boundaries

- The bridge needs the extension and browser to stay running. If the browser exits, the bridge loses its connection and the MCP must restart.
- It attaches to your real profile, including enterprise SSO, cookies and local storage. Do not enable it by default for untrusted repositories.
- Spark never stores passwords or reads the browser's password store; it only operates the rendered page.
- Per-domain rules and SSRF checks live in the built-in browser worker. The bridge relies on the browser, the extension and the MCP allowlist; Spark cannot see its current host before a call. Deep network interception on the bridge is therefore outside the native site-rule layer.
