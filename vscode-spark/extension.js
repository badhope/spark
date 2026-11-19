// Spark — VS Code extension host.
//
// Architecture: this extension is a thin editor-native client for the Spark
// HTTP control plane. It owns the VS Code surface (chat panel, diff preview,
// diagnostics, file jumps), while every model/tool/session decision stays on
// the Spark side. No second agent loop is implemented here on purpose: the
// Web UI, TUI, ACP and this panel all drive the same SessionRuntime.

const vscode = require("vscode");
const http = require("http");
const https = require("https");
const net = require("net");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawn } = require("child_process");
const P = require("./src/protocol");

let output;
let manager;
let currentPanel;
let diagnosticCollection;
let statusItem;

function log(line) {
  if (!output) output = vscode.window.createOutputChannel("Spark");
  output.appendLine(`[${new Date().toISOString()}] ${line}`);
}

function config() {
  return vscode.workspace.getConfiguration("spark");
}

function executable() {
  return config().get("executable", "spark");
}

function workdir() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || !folders.length) {
    vscode.window.showErrorMessage("Spark: 先打开一个文件夹（工作区根目录就是 agent 的 workdir）。");
    return null;
  }
  return folders[0].uri.fsPath;
}

function relativePath(abs) {
  const root = workdir();
  if (!root) return abs;
  return path.relative(root, abs).split(path.sep).join("/");
}

function resolvePath(p) {
  if (!p) return null;
  if (path.isAbsolute(p)) return p;
  const root = workdir();
  return root ? path.join(root, p) : p;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function transportFor(url) {
  return url.protocol === "https:" ? https : http;
}

function requestJson(baseUrl, method, apiPath, body, token, timeoutMs) {
  return new Promise((resolve, reject) => {
    const url = new URL(apiPath, baseUrl);
    const payload = body == null ? null : Buffer.from(JSON.stringify(body), "utf8");
    const headers = { Accept: "application/json" };
    if (payload) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = payload.length;
    }
    if (token) headers["X-Spark-Token"] = token;
    const req = transportFor(url).request(
      url,
      { method, headers, timeout: timeoutMs || 120000 },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          if (res.statusCode < 200 || res.statusCode >= 300) {
            reject(new Error(`HTTP ${res.statusCode}: ${text.slice(0, 400)}`));
            return;
          }
          try {
            resolve(text ? JSON.parse(text) : {});
          } catch (err) {
            reject(new Error(`invalid JSON: ${err.message}`));
          }
        });
      }
    );
    req.on("timeout", () => req.destroy(new Error("request timeout")));
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function streamSse(baseUrl, apiPath, body, token, onEvent, signal) {
  return new Promise((resolve, reject) => {
    const url = new URL(apiPath, baseUrl);
    const payload = Buffer.from(JSON.stringify(body), "utf8");
    const headers = {
      "Content-Type": "application/json",
      "Content-Length": payload.length,
      Accept: "text/event-stream",
    };
    if (token) headers["X-Spark-Token"] = token;
    const req = transportFor(url).request(url, { method: "POST", headers }, (res) => {
      if (res.statusCode !== 200) {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () =>
          reject(new Error(`HTTP ${res.statusCode}: ${Buffer.concat(chunks).toString("utf8").slice(0, 400)}`))
        );
        return;
      }
      let buffer = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        buffer += chunk;
        const parsed = P.parseSseText(buffer);
        buffer = parsed.rest;
        for (const item of parsed.events) onEvent(item.payload);
      });
      res.on("end", () => {
        const parsed = P.parseSseText(buffer + "\n\n");
        for (const item of parsed.events) onEvent(item.payload);
        resolve();
      });
      res.on("error", reject);
    });
    req.on("error", reject);
    const abort = () => req.destroy(new Error("cancelled"));
    if (signal) {
      if (signal.aborted) abort();
      else signal.addEventListener("abort", abort, { once: true });
    }
    req.write(payload);
    req.end();
  });
}

function findPort(host) {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, host || "127.0.0.1", () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
  });
}

class ServerManager {
  constructor(context) {
    this.context = context;
    this.proc = null;
    this.baseUrl = "";
    this.token = "";
    this.workdir = "";
  }

  async ensure() {
    const wd = workdir();
    if (!wd) throw new Error("open a workspace folder first");
    const configured = String(config().get("serverUrl", "") || "").trim().replace(/\/$/, "");
    if (configured) {
      this.baseUrl = configured;
      this.token = String(config().get("token", "") || "");
      this.workdir = wd;
      await this.waitHealthy(6000);
      return this.baseUrl;
    }
    if (this.proc && this.baseUrl && this.workdir === wd) {
      try {
        await this.waitHealthy(3000);
        return this.baseUrl;
      } catch {
        this.stop();
      }
    }
    if (this.proc) this.stop();
    const host = "127.0.0.1";
    const port = await findPort(host);
    this.workdir = wd;
    this.token = "";
    this.baseUrl = `http://${host}:${port}`;
    const args = [
      "web",
      "--workdir",
      wd,
      "--host",
      host,
      "--port",
      String(port),
      "--approval",
      config().get("approval", "suggest"),
    ];
    log(`spawn ${executable()} ${args.join(" ")}`);
    this.proc = spawn(executable(), args, {
      cwd: wd,
      shell: process.platform === "win32",
      windowsHide: true,
    });
    let stderr = "";
    this.proc.stdout.on("data", (d) => log(d.toString().trimEnd()));
    this.proc.stderr.on("data", (d) => {
      stderr += d.toString();
      log(d.toString().trimEnd());
    });
    this.proc.on("exit", (code) => {
      log(`spark web exited (${code})`);
      if (this.proc) {
        this.proc = null;
        this.baseUrl = "";
        currentPanel && currentPanel.onServerDown(stderr || `spark web exited (${code})`);
      }
    });
    try {
      await this.waitHealthy(40000);
    } catch (err) {
      this.stop();
      throw new Error(
        `无法启动 Spark Web（${err.message}）。确认 spark.executable 指向已安装的 Spark，或先手动运行 spark web。`
      );
    }
    return this.baseUrl;
  }

  async waitHealthy(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try {
        await requestJson(this.baseUrl, "GET", "/health", null, this.token, 1500);
        return;
      } catch {
        await sleep(400);
      }
    }
    throw new Error("spark web startup timeout");
  }

  stop() {
    if (this.proc) {
      const proc = this.proc;
      this.proc = null;
      try {
        proc.kill();
      } catch {
        /* already gone */
      }
    }
    this.baseUrl = "";
  }
}

function readText(file) {
  try {
    return fs.readFileSync(file, "utf8");
  } catch {
    return "";
  }
}

function selectionContext(editor) {
  if (!editor) return null;
  const doc = editor.document;
  const sel = editor.selection;
  if (sel.isEmpty) return null;
  const text = doc.getText(sel);
  if (!text.trim()) return null;
  const limited = text.length > 12000 ? text.slice(0, 12000) + "\n… (truncated)" : text;
  return {
    file: doc.isUntitled ? doc.fileName : relativePath(doc.uri.fsPath),
    uri: doc.uri.toString(),
    startLine: sel.start.line + 1,
    endLine: sel.end.line + 1,
    language: doc.languageId || "",
    text: limited,
    truncated: text.length > 12000,
  };
}

function proposalUri(file, content) {
  const id = crypto.createHash("sha1").update(file + "\0" + content).digest("hex").slice(0, 12);
  return {
    id,
    uri: vscode.Uri.from({
      scheme: "spark-proposal",
      path: "/" + path.basename(file || "proposal.txt"),
      query: `id=${id}`,
    }),
  };
}

class SparkPanel {
  constructor(context) {
    this.context = context;
    this.disposables = [];
    this.sessionId = "";
    this.busy = false;
    this.abort = null;
    this.lastApproval = null;
    this.proposals = new Map();
    this.serverError = "";
    this.baseUrl = "";
    this.panel = vscode.window.createWebviewPanel("spark.chat", "Spark", vscode.ViewColumn.Beside, {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
    });
    this.panel.iconPath = vscode.Uri.joinPath(context.extensionUri, "icon.png");
    this.panel.webview.html = this.html();
    this.disposables.push(
      this.panel.webview.onDidReceiveMessage((msg) => this.onMessage(msg)),
      this.panel.onDidDispose(() => this.dispose())
    );
    this.start();
  }

  html() {
    const webview = this.panel.webview;
    const media = vscode.Uri.joinPath(this.context.extensionUri, "media");
    const htmlPath = path.join(this.context.extensionPath, "media", "index.html");
    const nonce = crypto.randomBytes(24).toString("base64");
    let html = fs.readFileSync(htmlPath, "utf8");
    html = html
      .replace(/{{cspSource}}/g, webview.cspSource)
      .replace(/{{nonce}}/g, nonce)
      .replace(/{{styleUri}}/g, webview.asWebviewUri(vscode.Uri.joinPath(media, "style.css")).toString())
      .replace(/{{scriptUri}}/g, webview.asWebviewUri(vscode.Uri.joinPath(media, "main.js")).toString());
    return html;
  }

  post(msg) {
    try {
      this.panel.webview.postMessage(msg);
    } catch {
      /* panel disposed */
    }
  }

  async start() {
    this.post({ type: "connection", ok: false, text: "启动 Spark Web…" });
    try {
      this.baseUrl = await manager.ensure();
      this.post({ type: "connection", ok: true, text: "已连接" });
      await this.newSession(false);
    } catch (err) {
      this.serverError = String(err.message || err);
      this.post({ type: "connection", ok: false, text: "未连接" });
      this.post({ type: "event", event: { type: "turn_error", text: this.serverError } });
      log(this.serverError);
    }
  }

  async refreshStatus() {
    if (!this.baseUrl) return null;
    try {
      const sid = this.sessionId ? `?session_id=${encodeURIComponent(this.sessionId)}` : "";
      const status = await requestJson(this.baseUrl, "GET", `/api/status${sid}`, null, manager.token, 10000);
      this.sessionId = status.session_id || this.sessionId;
      const cost = status.session_cost || {};
      this.post({
        type: "status",
        status: {
          session_id: this.sessionId,
          model: status.model || (status.routing && status.routing.model) || "",
          effective_approval: status.approval || status.effective_approval || config().get("approval", "suggest"),
          session_cost: {
            spent: Number(cost.spent_usd || 0),
            budget: Number(cost.budget_usd || 0),
          },
        },
      });
      return status;
    } catch (err) {
      log(`status failed: ${err.message}`);
      return null;
    }
  }

  async newSession(notify) {
    if (!this.baseUrl) return;
    if (this.busy) {
      vscode.window.showWarningMessage("Spark 正在执行回合，先停止再新建会话。");
      return;
    }
    try {
      const body = { isolated: true, title: "VS Code" };
      const created = await requestJson(this.baseUrl, "POST", "/api/sessions/new", body, manager.token, 30000);
      this.sessionId = created.session_id || "";
      this.lastApproval = null;
      this.post({ type: "reset" });
      await this.refreshStatus();
      if (notify) vscode.window.showInformationMessage("Spark: 已新建会话");
    } catch (err) {
      vscode.window.showErrorMessage(`Spark 新建会话失败: ${err.message}`);
    }
  }

  async onMessage(msg) {
    if (!msg || typeof msg.type !== "string") return;
    switch (msg.type) {
      case "ready":
        this.post({ type: "connection", ok: !!this.baseUrl, text: this.baseUrl ? "已连接" : "连接中…" });
        if (this.baseUrl) this.refreshStatus();
        break;
      case "send":
        await this.send(msg.prompt, msg.context);
        break;
      case "approval":
        await this.approval(msg.decision);
        break;
      case "answer":
        await this.answer(msg.answer);
        break;
      case "openDiff":
        await this.openDiff(msg.approval || this.lastApproval);
        break;
      case "openFile":
        await this.openFile(msg.path, msg.line, msg.column);
        break;
      case "cancel":
        await this.cancel();
        break;
      case "newSession":
        await this.newSession(true);
        break;
      default:
        break;
    }
  }

  async send(question, context) {
    if (!this.baseUrl) {
      await this.start();
      if (!this.baseUrl) return;
    }
    if (this.busy) return;
    const prompt = P.buildSelectionPrompt(question, context || {});
    if (!prompt.trim()) return;
    this.busy = true;
    this.post({ type: "busy", value: true });
    const controller = new AbortController();
    this.abort = controller;
    let sawDone = false;

    const onEvent = (event) => {
      if (!event || !event.type) return;
      if (event.type === "tool_end") {
        event._summary = P.summarizeToolResult(event.tool && event.tool.name, event.result, event.ok);
        event._refs = P.extractFileReferences(event.result);
        this.applyDiagnostics(event.result);
      }
      if (event.type === "plan") this.updatePlan(event.data && event.data.steps);
      if (event.type === "approval_needed") {
        this.lastApproval = event.approval || null;
        if (this.lastApproval && config().get("autoOpenDiff", true)) {
          this.openDiff(this.lastApproval).catch((err) => log(`open diff failed: ${err.message}`));
        }
      }
      if (
        (event.type === "turn_end" || event.type === "turn_error" || event.type === "done") &&
        !sawDone
      ) {
        sawDone = true;
        this.busy = false;
        this.abort = null;
        this.post({ type: "busy", value: false });
        this.refreshStatus();
      }
      this.post({ type: "event", event });
    };

    try {
      await streamSse(
        this.baseUrl,
        "/api/chat/stream",
        { prompt, web_search: true, session_id: this.sessionId },
        manager.token,
        onEvent,
        controller.signal
      );
    } catch (err) {
      if (String(err.message).includes("cancelled")) {
        this.post({ type: "event", event: { type: "turn_error", text: "已停止当前回合" } });
      } else {
        this.post({ type: "event", event: { type: "turn_error", text: String(err.message || err) } });
      }
    } finally {
      this.busy = false;
      this.abort = null;
      this.post({ type: "busy", value: false });
      this.refreshStatus();
    }
  }

  async cancel() {
    if (!this.baseUrl) return;
    if (this.abort) this.abort.abort();
    try {
      await requestJson(this.baseUrl, "POST", "/api/cancel", { session_id: this.sessionId }, manager.token, 10000);
    } catch (err) {
      log(`cancel failed: ${err.message}`);
    }
    this.busy = false;
    this.post({ type: "busy", value: false });
  }

  async approval(decision) {
    if (!this.baseUrl) return;
    if (!["allow", "allow_always", "deny"].includes(decision)) return;
    try {
      await requestJson(
        this.baseUrl,
        "POST",
        "/api/approval",
        { decision, session_id: this.sessionId },
        manager.token,
        15000
      );
      this.lastApproval = null;
      await this.refreshStatus();
    } catch (err) {
      this.post({ type: "event", event: { type: "turn_error", text: `审批失败: ${err.message}` } });
    }
  }

  async answer(text) {
    if (!this.baseUrl) return;
    try {
      await requestJson(
        this.baseUrl,
        "POST",
        "/api/question/answer",
        { answer: String(text || ""), session_id: this.sessionId },
        manager.token,
        15000
      );
    } catch (err) {
      this.post({ type: "event", event: { type: "turn_error", text: `回答失败: ${err.message}` } });
    }
  }

  proposed(approval) {
    if (!approval) return null;
    const args = approval.arguments || {};
    const fileArg = args.path || args.file || args.target || "";
    const file = resolvePath(fileArg);
    const original = file ? readText(file) : "";
    let content = P.proposedContentFor(approval.tool, args, original, approval.diff);
    if (content == null && typeof args.content === "string") content = args.content;
    if (content == null) return null;
    return { file, original, content, exists: !!file && fs.existsSync(file) };
  }

  async openDiff(approval) {
    if (!approval) {
      vscode.window.showInformationMessage("Spark: 当前没有待审的改动。");
      return;
    }
    const proposed = this.proposed(approval);
    if (!proposed || proposed.content == null) {
      const doc = await vscode.workspace.openTextDocument({
        content: String(approval.diff || approval.summary || ""),
        language: "diff",
      });
      await vscode.window.showTextDocument(doc, { preview: true, viewColumn: vscode.ViewColumn.Beside });
      return;
    }
    const key = proposalUri(proposed.file || "proposal", proposed.content);
    this.proposals.set(key.id, proposed.content);
    const proposal = key.uri;
    if (proposed.exists) {
      await vscode.commands.executeCommand(
        "vscode.diff",
        vscode.Uri.file(proposed.file),
        proposal,
        `Spark: ${path.basename(proposed.file)} (proposed)`,
        { preview: true, viewColumn: vscode.ViewColumn.Beside }
      );
    } else {
      const empty = vscode.Uri.from({ scheme: "spark-proposal", path: "/empty", query: "id=empty" });
      this.proposals.set("empty", "");
      await vscode.commands.executeCommand(
        "vscode.diff",
        empty,
        proposal,
        `Spark: ${path.basename(proposed.file || "new file")} (new file)`,
        { preview: true, viewColumn: vscode.ViewColumn.Beside }
      );
    }
  }

  applyDiagnostics(result) {
    if (!diagnosticCollection) return;
    const items = P.diagnosticsFromResult(result);
    if (!items.length) return;
    const byFile = new Map();
    for (const d of items) {
      const file = resolvePath(d.path);
      if (!file) continue;
      const list = byFile.get(file) || [];
      const line = Math.max(0, d.line - 1);
      const col = Math.max(0, d.column ? d.column - 1 : 0);
      const range = new vscode.Range(
        new vscode.Position(line, col),
        new vscode.Position(line, col + 1)
      );
      const severity =
        d.severity === "error"
          ? vscode.DiagnosticSeverity.Error
          : d.severity === "info" || d.severity === "information" || d.severity === "hint"
            ? vscode.DiagnosticSeverity.Information
            : vscode.DiagnosticSeverity.Warning;
      const diag = new vscode.Diagnostic(range, d.message, severity);
      diag.source = "spark";
      list.push(diag);
      byFile.set(file, list);
    }
    for (const [file, list] of byFile) diagnosticCollection.set(vscode.Uri.file(file), list);
  }

  async openFile(file, line, column) {
    if (!file) return;
    const abs = resolvePath(file);
    if (!abs || !fs.existsSync(abs)) {
      vscode.window.showWarningMessage(`Spark: 文件不存在 ${file}`);
      return;
    }
    const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(abs));
    const pos = new vscode.Position(
      Math.max(0, (Number(line) || 1) - 1),
      Math.max(0, (Number(column) || 1) - 1)
    );
    const editor = await vscode.window.showTextDocument(doc, {
      preview: false,
      viewColumn: vscode.ViewColumn.One,
    });
    editor.selection = new vscode.Selection(pos, pos);
    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
  }

  prefill(prompt, ctx) {
    this.panel.reveal(vscode.ViewColumn.Beside, true);
    this.post({ type: "prefill", prompt: prompt || "", context: ctx || null });
  }

  updatePlan(steps) {
    if (!statusItem) return;
    if (!steps || !steps.length) {
      statusItem.text = "$(sparkle) Spark";
      statusItem.tooltip = "Spark ready";
      return;
    }
    const done = steps.filter((s) => s.status === "completed" || s.done).length;
    statusItem.text = `$(sparkle) Spark ${done}/${steps.length}`;
    statusItem.tooltip = steps
      .map((s) => {
        const mark = s.status === "completed" || s.done ? "✓" : s.status === "in_progress" ? "◐" : "○";
        return `${mark} ${s.title || s.step || ""}`;
      })
      .join("\n");
  }

  onServerDown(text) {
    this.post({ type: "connection", ok: false, text: "服务已退出" });
    this.post({ type: "event", event: { type: "turn_error", text: text || "Spark Web 已退出" } });
  }

  dispose() {
    this.disposables.forEach((d) => d.dispose && d.dispose());
    this.disposables = [];
    currentPanel = undefined;
  }
}

function registerProposalProvider(context) {
  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("spark-proposal", {
      provideTextDocumentContent(uri) {
        const id = new URLSearchParams(uri.query).get("id") || "";
        const panel = currentPanel;
        if (!panel) return "";
        return panel.proposals.get(id) || "";
      },
    })
  );
}

function openChat() {
  if (currentPanel) {
    currentPanel.panel.reveal(vscode.ViewColumn.Beside, true);
    return currentPanel;
  }
  currentPanel = new SparkPanel(manager.context);
  return currentPanel;
}

function askSelection(defaultPrompt) {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showInformationMessage("Spark: 先打开一个编辑器，再选中代码。");
    return;
  }
  const ctx = selectionContext(editor);
  if (!ctx) {
    vscode.window.showInformationMessage("Spark: 先选中一段代码，再执行这个命令。");
    return;
  }
  const panel = openChat();
  panel.prefill(defaultPrompt || "", ctx);
}

function openWeb() {
  const wd = workdir();
  if (!wd) return;
  manager
    .ensure()
    .then(() => vscode.env.openExternal(vscode.Uri.parse(manager.baseUrl)))
    .catch((err) => vscode.window.showErrorMessage(String(err.message || err)));
}

function openTui() {
  const wd = workdir();
  if (!wd) return;
  const terminal = vscode.window.createTerminal({ name: "Spark TUI", cwd: wd });
  terminal.show();
  terminal.sendText([executable(), "--approval", config().get("approval", "suggest")].join(" "));
}

function newSession() {
  const panel = openChat();
  panel.newSession(true);
}

function activate(context) {
  output = vscode.window.createOutputChannel("Spark");
  context.subscriptions.push(output);
  diagnosticCollection = vscode.languages.createDiagnosticCollection("spark");
  context.subscriptions.push(diagnosticCollection);
  manager = new ServerManager(context);
  manager.context = context;

  registerProposalProvider(context);
  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusItem.command = "spark.chat.open";
  statusItem.text = "$(sparkle) Spark";
  statusItem.show();
  context.subscriptions.push(statusItem);

  context.subscriptions.push(
    vscode.commands.registerCommand("spark.openWeb", openWeb),
    vscode.commands.registerCommand("spark.openTui", openTui),
    vscode.commands.registerCommand("spark.askAgent", () => askSelection("")),
    vscode.commands.registerCommand("spark.chat.open", () => openChat()),
    vscode.commands.registerCommand("spark.chat.new", newSession),
    vscode.commands.registerCommand("spark.chat.askSelection", () => askSelection("")),
    vscode.commands.registerCommand("spark.chat.explainSelection", () =>
      askSelection("解释这段代码：它做什么、有什么边界问题和风险？")
    ),
    vscode.commands.registerCommand("spark.chat.fixSelection", () =>
      askSelection("修复这段代码的问题，给出最小改动。")
    ),
    vscode.commands.registerCommand("spark.diagnostics.open", () =>
      vscode.commands.executeCommand("workbench.action.problems.focus")
    )
  );
}

function deactivate() {
  if (manager) manager.stop();
}

module.exports = { activate, deactivate };
