// Spark — VS Code 扩展 v1：把工作区一键接入 Spark 的三种使用面。
//
// 设计取舍（诚实边界）：不内嵌 WebView 聊天面板（Web UI 本身就是全功能
// 前端，重复实现只会漂移）；扩展做的是"胶水"——从编辑器上下文（当前
// 工作区 / 选中文本）以正确的 workdir 启动对应形态。

const vscode = require("vscode");
const { spawn } = require("child_process");
const http = require("http");

let webProc = null;
let webUrl = null;

function execPath() {
  return vscode.workspace.getConfiguration("spark").get("executable", "spark");
}

function approvalArg() {
  const mode = vscode.workspace
    .getConfiguration("spark")
    .get("approval", "suggest");
  return ["--approval", mode];
}

function workdir() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    vscode.window.showErrorMessage(
      "Spark: open a folder first (the working directory is the workspace root)."
    );
    return null;
  }
  return folders[0].uri.fsPath;
}

/** 等待 spark web 起服务（轮询 /health，最多 40s——冷启动含重依赖导入）。 */
function waitForServer(port) {
  const url = `http://127.0.0.1:${port}/health`;
  const deadline = Date.now() + 40000;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(url, (res) => {
        res.resume();
        if (res.statusCode === 200) return resolve(true);
        retry();
      });
      req.on("error", retry);
      req.setTimeout(1500, () => {
        req.destroy();
        retry();
      });
    };
    const retry = () => {
      if (Date.now() > deadline) return reject(new Error("spark web startup timeout"));
      setTimeout(attempt, 700);
    };
    attempt();
  });
}

/** 从 `spark web` 的 stdout 里解析实际监听地址。 */
function parseUrlFromOutput(text) {
  const m = text.match(/http:\/\/127\.0\.0\.1:\d+/);
  return m ? m[0] : null;
}

async function openWeb() {
  const wd = workdir();
  if (!wd) return;
  if (webProc && webUrl) {
    vscode.env.openExternal(vscode.Uri.parse(webUrl));
    return;
  }
  const output = vscode.window.createOutputChannel("Spark");
  output.show(true);
  webProc = spawn(execPath(), ["web", "--workdir", wd, ...approvalArg()], {
    cwd: wd,
    shell: process.platform === "win32",
  });
  let buffer = "";
  webProc.stdout.on("data", (d) => {
    buffer += d.toString();
    output.append(d.toString());
    if (!webUrl) {
      const url = parseUrlFromOutput(buffer);
      if (url) {
        webUrl = url;
        vscode.window.showInformationMessage(`Spark Web: ${url}`);
        vscode.env.openExternal(vscode.Uri.parse(url));
      }
    }
  });
  webProc.stderr.on("data", (d) => output.append(d.toString()));
  webProc.on("exit", (code) => {
    output.appendLine(`\n[spark web exited: ${code}]`);
    webProc = null;
    webUrl = null;
  });
}

function openTui() {
  const wd = workdir();
  if (!wd) return;
  const terminal = vscode.window.createTerminal({
    name: "Spark TUI",
    cwd: wd,
  });
  terminal.show();
  terminal.sendText(
    [execPath(), ...approvalArg()].join(" ")
  );
}

async function askAgent() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showErrorMessage("Spark: open a file and select text first.");
    return;
  }
  const selection = editor.document.getText(editor.selection);
  if (!selection.trim()) {
    vscode.window.showInformationMessage(
      "Spark: select some code, then run this command — the question opens in the Web UI with the selection as context."
    );
    return;
  }
  const question = await vscode.window.showInputBox({
    prompt: "Ask Spark about the selected code (opens Web UI)",
    placeHolder: "e.g. 这段代码有什么边界问题？",
  });
  if (!question) return;
  const wd = workdir();
  if (!wd) return;

  // 复用已起的 Web UI；没有就现起一个，再走它的输入框（预填 prompt 用
  // URL hash 传给前端，前端已有 ?prompt= 支持则直接带入，否则用户粘贴）。
  if (!webProc || !webUrl) {
    await openWeb();
  }
  const payload = encodeURIComponent(
    `${question}\n\n\`\`\`\n${selection.slice(0, 4000)}\n\`\`\``
  );
  const target = webUrl ? `${webUrl}?onboarding=skip&q=${payload}` : webUrl;
  if (target) {
    vscode.env.openExternal(vscode.Uri.parse(target));
  }
}

function deactivate() {
  if (webProc) {
    try {
      webProc.kill();
    } catch {
      /* 进程已退出 */
    }
  }
}

module.exports = {
  activate(context) {
    context.subscriptions.push(
      vscode.commands.registerCommand("spark.openWeb", openWeb),
      vscode.commands.registerCommand("spark.openTui", openTui),
      vscode.commands.registerCommand("spark.askAgent", askAgent)
    );
  },
  deactivate,
};
