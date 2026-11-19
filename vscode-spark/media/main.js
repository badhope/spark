// Spark chat webview. No frameworks: the extension host owns all network I/O;
// this file only renders events and posts user intents.
(function () {
  "use strict";

  const vscode = acquireVsCodeApi();
  const messages = document.getElementById("messages");
  const emptyState = document.getElementById("empty-state");
  const prompt = document.getElementById("prompt");
  const sendBtn = document.getElementById("send");
  const cancelBtn = document.getElementById("cancel-turn");
  const newBtn = document.getElementById("new-session");
  const sessionLabel = document.getElementById("session-label");
  const costChip = document.getElementById("cost-chip");
  const hint = document.getElementById("hint");
  const connDot = document.getElementById("conn-dot");
  const contextStrip = document.getElementById("context-strip");
  const contextLabel = document.getElementById("context-label");
  const clearContext = document.getElementById("clear-context");

  let busy = false;
  let context = null;
  let currentTurn = null;
  let sessionId = "";
  const toolCards = new Map();

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderMarkdown(text) {
    let src = escapeHtml(text);
    const codeBlocks = [];
    src = src.replace(/```([A-Za-z0-9_+-]*)\n([\s\S]*?)```/g, function (_, lang, body) {
      const id = "CODEBLOCK" + codeBlocks.length;
      codeBlocks.push(`<pre class="code"><code>${body.replace(/\n$/, "")}</code></pre>`);
      return id;
    });
    src = src.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    src = src.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
    src = src.replace(/^#### (.+)$/gm, "<h4>$1</h4>");
    src = src.replace(/^### (.+)$/gm, "<h4>$1</h4>");
    src = src.replace(/^## (.+)$/gm, "<h4>$1</h4>");
    for (let i = 0; i < codeBlocks.length; i++) {
      src = src.replace("CODEBLOCK" + i, codeBlocks[i]);
    }
    return src;
  }

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  function setBusy(value) {
    busy = !!value;
    sendBtn.disabled = busy;
    cancelBtn.disabled = !busy;
    prompt.disabled = busy;
    if (!busy) prompt.focus();
  }

  function setStatus(status) {
    if (!status) return;
    if (status.session_id) sessionId = status.session_id;
    const model = status.model || "";
    sessionLabel.textContent = sessionId
      ? `session ${String(sessionId).slice(0, 8)}${model ? " · " + model : ""}`
      : "new session";
    if (status.session_cost != null) {
      const spent = Number(status.session_cost.spent || 0);
      costChip.textContent = spent > 0 ? "$" + spent.toFixed(4) : "$0.0000";
      costChip.title = `spent $${spent.toFixed(4)} / budget $${Number(status.session_cost.budget || 0).toFixed(4)}`;
    }
    if (status.approval || status.effective_approval) {
      hint.textContent = "审批：" + (status.effective_approval || status.approval);
    }
    connDot.className = "dot ok";
  }

  function addTurn(role, text) {
    if (emptyState) emptyState.hidden = true;
    const turn = document.createElement("section");
    turn.className = "turn" + (role === "user" ? " user-turn" : "");
    const head = document.createElement("div");
    head.className = "turn-head";
    const who = document.createElement("span");
    who.textContent = role === "user" ? "You" : "Spark";
    const when = document.createElement("span");
    when.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    head.appendChild(who);
    head.appendChild(when);
    const body = document.createElement("div");
    body.className = "turn-body";
    const textEl = document.createElement("div");
    textEl.className = "turn-text";
    textEl.innerHTML = renderMarkdown(text || "");
    body.appendChild(textEl);
    turn.appendChild(head);
    turn.appendChild(body);
    messages.appendChild(turn);
    scrollToBottom();
    return { root: turn, body: body, text: textEl, tools: [], plan: null, approvals: [] };
  }

  function renderUserPrompt(text) {
    addTurn("user", text);
  }

  function ensureAssistant() {
    if (!currentTurn) currentTurn = addTurn("assistant", "");
    return currentTurn;
  }

  function appendAssistant(text) {
    const t = ensureAssistant();
    t.raw = (t.raw || "") + String(text || "");
    t.text.innerHTML = renderMarkdown(t.raw);
    scrollToBottom();
  }

  function addTool(tool) {
    const t = ensureAssistant();
    const card = document.createElement("div");
    card.className = "tool";
    card.innerHTML =
      '<div class="tool-head"><span class="tool-name">' + escapeHtml(tool.name || "tool") +
      '</span><span class="tool-status">running…</span></div>' +
      '<div class="tool-summary"></div><div class="tool-refs"></div>';
    t.body.appendChild(card);
    toolCards.set(tool.id || tool.name, card);
    scrollToBottom();
  }

  function finishTool(tool, ok, summary, refs) {
    const key = tool.id || tool.name;
    const card = toolCards.get(key) || toolCards.get(tool.name);
    if (!card) {
      addTool(tool);
      return finishTool(tool, ok, summary, refs);
    }
    const status = card.querySelector(".tool-status");
    status.textContent = ok === false ? "failed" : "done";
    status.className = "tool-status " + (ok === false ? "err" : "ok");
    card.querySelector(".tool-summary").textContent = summary || "";
    const refsEl = card.querySelector(".tool-refs");
    refsEl.textContent = "";
    (refs || []).slice(0, 8).forEach(function (ref) {
      const b = document.createElement("button");
      b.className = "ref-chip";
      b.textContent = ref.path + (ref.line ? ":" + ref.line : "");
      b.title = "在编辑器中打开";
      b.addEventListener("click", function () {
        vscode.postMessage({ type: "openFile", path: ref.path, line: ref.line, column: ref.column });
      });
      refsEl.appendChild(b);
    });
    scrollToBottom();
  }

  function renderPlan(steps) {
    const t = ensureAssistant();
    if (!t.plan) {
      const box = document.createElement("div");
      box.className = "plan";
      box.innerHTML = '<div class="plan-title">Plan</div>';
      t.body.appendChild(box);
      t.plan = box;
    }
    const completed = steps.filter(function (s) { return s.status === "completed" || s.done; }).length;
    const title = t.plan.querySelector(".plan-title");
    title.textContent = "Plan · " + completed + "/" + steps.length;
    t.plan.querySelectorAll(".plan-step").forEach(function (n) { n.remove(); });
    steps.forEach(function (step) {
      const done = step.status === "completed" || step.done;
      const active = step.status === "in_progress";
      const mark = done ? "✓" : active ? "◐" : "○";
      const row = document.createElement("div");
      row.className = "plan-step" + (done ? " done" : "");
      row.innerHTML = '<span class="mark">' + mark + "</span><span>" +
        escapeHtml(step.title || step.step || step.text || "") + "</span>";
      t.plan.appendChild(row);
    });
    scrollToBottom();
  }

  function renderDiff(diff) {
    const box = document.createElement("div");
    box.className = "diff-view";
    String(diff || "").split("\n").forEach(function (line) {
      const el = document.createElement("div");
      el.className = "diff-line";
      if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) {
        el.classList.add("hunk");
      } else if (line.startsWith("+")) {
        el.classList.add("add");
      } else if (line.startsWith("-")) {
        el.classList.add("del");
      }
      el.textContent = line || " ";
      box.appendChild(el);
    });
    return box;
  }

  function addApproval(approval) {
    const t = ensureAssistant();
    const card = document.createElement("div");
    card.className = "approval";
    const head = document.createElement("div");
    head.className = "approval-head";
    head.textContent = "需要确认 · " + (approval.tool || "tool");
    const body = document.createElement("div");
    body.className = "approval-body";
    const summary = document.createElement("div");
    summary.className = "approval-summary";
    summary.textContent = approval.summary || "该工具请求执行";
    body.appendChild(summary);
    if (approval.diff) body.appendChild(renderDiff(approval.diff));
    const actions = document.createElement("div");
    actions.className = "approval-actions";

    const applyBtn = document.createElement("button");
    applyBtn.className = "primary";
    applyBtn.textContent = "Apply / Allow";
    const rejectBtn = document.createElement("button");
    rejectBtn.className = "secondary danger";
    rejectBtn.textContent = "Reject";
    const diffBtn = document.createElement("button");
    diffBtn.className = "secondary";
    diffBtn.textContent = "Open inline diff";
    diffBtn.disabled = !approval.diff && !approval.arguments;

    function settle(decision, label) {
      applyBtn.disabled = true;
      rejectBtn.disabled = true;
      diffBtn.disabled = true;
      const note = document.createElement("span");
      note.className = "muted";
      note.textContent = label;
      actions.appendChild(note);
      vscode.postMessage({ type: "approval", decision: decision });
    }

    applyBtn.addEventListener("click", function () { settle("allow", "applying…"); });
    rejectBtn.addEventListener("click", function () { settle("deny", "rejected"); });
    diffBtn.addEventListener("click", function () {
      vscode.postMessage({ type: "openDiff", approval: approval });
    });

    actions.appendChild(applyBtn);
    actions.appendChild(rejectBtn);
    actions.appendChild(diffBtn);
    body.appendChild(actions);
    card.appendChild(head);
    card.appendChild(body);
    t.body.appendChild(card);
    scrollToBottom();
  }

  function addQuestion(data) {
    const t = ensureAssistant();
    const card = document.createElement("div");
    card.className = "approval";
    card.innerHTML = '<div class="approval-head">需要补充信息</div>';
    const body = document.createElement("div");
    body.className = "approval-body";
    const q = document.createElement("div");
    q.className = "approval-summary";
    q.textContent = data.question || "";
    body.appendChild(q);
    const actions = document.createElement("div");
    actions.className = "approval-actions";
    (data.options || []).forEach(function (opt) {
      const b = document.createElement("button");
      b.className = "secondary";
      b.textContent = opt;
      b.addEventListener("click", function () {
        actions.querySelectorAll("button").forEach(function (x) { x.disabled = true; });
        vscode.postMessage({ type: "answer", answer: opt });
      });
      actions.appendChild(b);
    });
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "或输入自定义回答…";
    input.style.flex = "1";
    input.style.minWidth = "120px";
    input.style.background = "var(--vscode-input-background)";
    input.style.color = "var(--vscode-input-foreground)";
    input.style.border = "1px solid var(--vscode-input-border)";
    input.style.borderRadius = "4px";
    input.style.padding = "4px 6px";
    const submit = document.createElement("button");
    submit.className = "primary";
    submit.textContent = "回答";
    submit.addEventListener("click", function () {
      const answer = input.value.trim();
      if (!answer) return;
      actions.querySelectorAll("button").forEach(function (x) { x.disabled = true; });
      input.disabled = true;
      vscode.postMessage({ type: "answer", answer: answer });
    });
    actions.appendChild(input);
    actions.appendChild(submit);
    body.appendChild(actions);
    card.appendChild(body);
    t.body.appendChild(card);
    scrollToBottom();
  }

  function addError(text) {
    const t = ensureAssistant();
    const box = document.createElement("div");
    box.className = "error-box";
    box.textContent = text || "unknown error";
    t.body.appendChild(box);
    scrollToBottom();
  }

  function handleEvent(event) {
    if (!event || !event.type) return;
    switch (event.type) {
      case "text_delta":
        appendAssistant(event.text || "");
        break;
      case "reasoning_delta":
        break;
      case "tool_start":
        addTool(event.tool || {});
        break;
      case "tool_end":
        finishTool(event.tool || {}, event.ok, event._summary || "", event._refs || []);
        break;
      case "plan":
        renderPlan((event.data && event.data.steps) || []);
        break;
      case "approval_needed":
        addApproval(event.approval || {});
        break;
      case "approval_changed":
        if (event.data && event.data.label) hint.textContent = event.data.label;
        else if (event.data) hint.textContent = "审批：" + (event.data.approval || "");
        break;
      case "question":
        addQuestion(event.data || {});
        break;
      case "turn_error":
        addError(event.text || "turn failed");
        setBusy(false);
        break;
      case "turn_end":
        if (event.text) {
          const t = ensureAssistant();
          t.raw = event.text;
          t.text.innerHTML = renderMarkdown(t.raw);
        }
        setBusy(false);
        break;
      case "done":
        setBusy(false);
        break;
      default:
        break;
    }
  }

  function submit() {
    if (busy) return;
    const text = prompt.value.trim();
    if (!text) return;
    const ctx = context;
    renderUserPrompt(text + (ctx ? "\n\nContext: " + ctx.file + (ctx.startLine ? ":" + ctx.startLine : "") : ""));
    prompt.value = "";
    currentTurn = null;
    toolCards.clear();
    setBusy(true);
    vscode.postMessage({ type: "send", prompt: text, context: ctx });
    clearCtx();
  }

  function clearCtx() {
    context = null;
    contextStrip.hidden = true;
    contextLabel.textContent = "";
  }

  sendBtn.addEventListener("click", submit);
  prompt.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  });
  clearContext.addEventListener("click", clearCtx);
  cancelBtn.addEventListener("click", function () {
    vscode.postMessage({ type: "cancel" });
  });
  newBtn.addEventListener("click", function () {
    vscode.postMessage({ type: "newSession" });
  });

  window.addEventListener("message", function (e) {
    const msg = e.data || {};
    if (msg.type === "status") {
      setStatus(msg.status || {});
    } else if (msg.type === "event") {
      handleEvent(msg.event || {});
    } else if (msg.type === "busy") {
      setBusy(msg.value);
    } else if (msg.type === "reset") {
      messages.innerHTML = "";
      currentTurn = null;
      toolCards.clear();
      if (emptyState) {
        const clone = emptyState.cloneNode(true);
        clone.hidden = false;
        messages.appendChild(clone);
      }
      clearCtx();
      setBusy(false);
    } else if (msg.type === "prefill") {
      if (msg.context) {
        context = msg.context;
        contextLabel.textContent = msg.context.file + (msg.context.startLine ? ":" + msg.context.startLine : "") +
          (msg.context.text ? ` · ${msg.context.text.length} chars` : "");
        contextStrip.hidden = false;
      }
      if (msg.prompt) prompt.value = msg.prompt;
      prompt.focus();
    } else if (msg.type === "notice") {
      hint.textContent = msg.text || "";
    } else if (msg.type === "connection") {
      connDot.className = "dot " + (msg.ok ? "ok" : "err");
      sessionLabel.textContent = msg.text || sessionLabel.textContent;
    }
  });

  vscode.postMessage({ type: "ready" });
})();
