// Pure helpers shared by the extension host and node:test.
// No `vscode` import here: this file must stay runnable under plain Node.

"use strict";

/**
 * Incrementally parse SSE text. Returns complete JSON events plus the
 * still-unterminated tail (caller should feed it back on the next chunk).
 */
function parseSseText(text) {
  const normalized = String(text || "").replace(/\r\n/g, "\n");
  const events = [];
  let rest = normalized;
  while (true) {
    const idx = rest.indexOf("\n\n");
    if (idx === -1) break;
    const block = rest.slice(0, idx);
    rest = rest.slice(idx + 2);
    const event = parseSseBlock(block);
    if (event) events.push(event);
  }
  return { events, rest };
}

function parseSseBlock(block) {
  let id = null;
  const dataLines = [];
  for (const rawLine of block.split("\n")) {
    const line = rawLine.trimEnd();
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("id:")) {
      id = line.slice(3).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }
  if (!dataLines.length) return null;
  const raw = dataLines.join("\n");
  try {
    const payload = JSON.parse(raw);
    if (payload && typeof payload === "object") {
      return { id, payload };
    }
  } catch {
    return null;
  }
  return null;
}

/** Build the prompt sent to /api/chat/stream from editor context. */
function buildSelectionPrompt(question, context) {
  const q = String(question || "").trim();
  const text = String((context && context.text) || "");
  if (!text) return q;
  const file = String((context && context.file) || "");
  const start = Number((context && context.startLine) || 0);
  const end = Number((context && context.endLine) || 0);
  const language = String((context && context.language) || "");
  const where = file
    ? `${file}${start ? `:${start}${end && end !== start ? `-${end}` : ""}` : ""}`
    : "(unsaved editor)";
  return [
    q,
    "",
    `Context — ${where}`,
    "```" + language,
    text,
    "```",
  ].join("\n");
}

/** Apply a single-file unified diff in memory. Returns null if malformed. */
function applyUnifiedDiff(original, diff) {
  const src = String(original == null ? "" : original);
  const lines = String(diff || "").replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  // Skip file headers / metadata until the first hunk.
  while (i < lines.length && !lines[i].startsWith("@@")) i++;
  if (i >= lines.length) return null;
  const srcLines = src.split("\n");
  const out = [];
  let cursor = 0;

  while (i < lines.length) {
    const header = lines[i++];
    if (!header.startsWith("@@")) break;
    const m = /^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@/.exec(header);
    if (!m) return null;
    const oldStart = Math.max(0, Number(m[1]) - 1);
    const oldCount = m[2] === undefined ? 1 : Number(m[2]);
    if (oldStart < cursor || oldStart > srcLines.length) return null;
    while (cursor < oldStart && cursor < srcLines.length) out.push(srcLines[cursor++]);
    let oldSeen = 0;
    let newSeen = 0;
    while (i < lines.length && !lines[i].startsWith("@@")) {
      const line = lines[i];
      if (line === "\\ No newline at end of file") {
        i++;
        continue;
      }
      if (!line.length) {
        // Unified diff represents an empty context line as a single space;
        // a truly empty line in the payload is tolerated as context.
        if (oldSeen < oldCount) {
          out.push(srcLines[cursor++] ?? "");
          oldSeen++;
          newSeen++;
        }
        i++;
        continue;
      }
      const tag = line[0];
      const body = line.slice(1);
      if (tag === " ") {
        if (cursor >= srcLines.length) return null;
        out.push(srcLines[cursor++]);
        oldSeen++;
        newSeen++;
      } else if (tag === "-") {
        if (cursor >= srcLines.length) return null;
        cursor++;
        oldSeen++;
      } else if (tag === "+") {
        out.push(body);
        newSeen++;
      } else if (tag === "\\") {
        // metadata, ignore
      } else {
        // Diff text that has no hunk prefix is not an applyable patch.
        return null;
      }
      i++;
    }
    if (oldSeen < oldCount) {
      while (oldSeen < oldCount && cursor < srcLines.length) {
        out.push(srcLines[cursor++]);
        oldSeen++;
        newSeen++;
      }
    }
  }
  while (cursor < srcLines.length) out.push(srcLines[cursor++]);
  return out.join("\n");
}

/** Reconstruct the file body a write/apply tool is about to produce. */
function proposedContentFor(toolName, args, original, diff) {
  const a = args && typeof args === "object" ? args : {};
  if (typeof a.content === "string") {
    if (toolName === "apply_edit") {
      return applySearchReplace(original, a.content);
    }
    return a.content;
  }
  if (toolName === "apply_patch") {
    const oldText = a.old_text != null ? String(a.old_text) : a.oldText;
    const newText = a.new_text != null ? String(a.new_text) : a.newText;
    if (typeof oldText === "string" && typeof newText === "string") {
      const at = String(original).indexOf(oldText);
      if (at === -1) return null;
      return String(original).slice(0, at) + newText + String(original).slice(at + oldText.length);
    }
  }
  if (typeof diff === "string" && diff.trim()) {
    return applyUnifiedDiff(original, diff);
  }
  return null;
}

/** aider-style SEARCH/REPLACE blocks, or whole-file content. */
function applySearchReplace(original, content) {
  const src = String(original == null ? "" : original);
  const text = String(content || "");
  if (!text.includes("<<<<<<< SEARCH")) return text;
  const re = /<<<<<<< SEARCH\n([\s\S]*?)\n?=======\n([\s\S]*?)\n?>>>>>>> REPLACE/g;
  let out = src;
  let matched = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    const oldText = m[1];
    const newText = m[2];
    const at = out.indexOf(oldText);
    if (at === -1) return null;
    out = out.slice(0, at) + newText + out.slice(at + oldText.length);
    matched++;
  }
  return matched ? out : null;
}

/** Walk a tool result and collect file/line anchors for editor jumps. */
function extractFileReferences(value, out) {
  const refs = out || [];
  const seen = new Set();
  const push = (path, line, column) => {
    if (!path || typeof path !== "string") return;
    const key = `${path}:${line || 0}:${column || 0}`;
    if (seen.has(key)) return;
    seen.add(key);
    refs.push({ path, line: Number(line) || 0, column: Number(column) || 0 });
  };
  const walk = (node, depth) => {
    if (!node || depth > 6) return;
    if (Array.isArray(node)) {
      for (const item of node) walk(item, depth + 1);
      return;
    }
    if (typeof node === "string") {
      const re = /([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+):(\d+)(?::(\d+))?/g;
      let m;
      while ((m = re.exec(node)) !== null) push(m[1], m[2], m[3]);
      return;
    }
    if (typeof node !== "object") return;
    const path = node.path || node.file || node.filename;
    if (typeof path === "string" && (node.line || node.character || node.column || node.severity)) {
      push(path, node.line, node.character || node.column);
    }
    // `path:line` strings inside test output / grep matches.
    for (const key of ["text", "output", "log", "message", "detail"]) {
      if (typeof node[key] !== "string") continue;
      const re = /([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+):(\d+)(?::(\d+))?/g;
      let m;
      while ((m = re.exec(node[key])) !== null) push(m[1], m[2], m[3]);
    }
    for (const [k, v] of Object.entries(node)) {
      if (k === "content" || k === "patch" || k === "diff") continue;
      if (v && typeof v === "object") walk(v, depth + 1);
    }
  };
  walk(value, 0);
  return refs;
}

/** Short, safe text shown on a tool card. */
function summarizeToolResult(name, result, ok) {
  if (result == null) return ok === false ? "failed" : "done";
  if (typeof result === "string") return result.slice(0, 240);
  if (typeof result !== "object") return String(result).slice(0, 240);
  const r = result;
  if (Array.isArray(r.diagnostics)) return `${r.diagnostics.length} diagnostics`;
  if (typeof r.total === "number") return `${r.total} items`;
  if (typeof r.summary === "string") return r.summary.slice(0, 240);
  if (typeof r.output === "string") return r.output.slice(0, 240);
  if (typeof r.error === "string") return r.error.slice(0, 240);
  if (r.ok === false) return "failed";
  return ok === false ? "failed" : "done";
}

function diagnosticsFromResult(result) {
  if (!result || typeof result !== "object") return [];
  const source = Array.isArray(result.diagnostics)
    ? result.diagnostics
    : Array.isArray(result.issues)
      ? result.issues
      : [];
  return source
    .map((d) => ({
      path: d.path || d.file || d.filename,
      line: Number(d.line) || 0,
      column: Number(d.character || d.column || d.col) || 0,
      message: String(d.message || d.text || "diagnostic"),
      severity: String(d.severity || "warning").toLowerCase(),
    }))
    .filter((d) => d.path && d.line > 0);
}

module.exports = {
  parseSseText,
  buildSelectionPrompt,
  applyUnifiedDiff,
  applySearchReplace,
  proposedContentFor,
  extractFileReferences,
  summarizeToolResult,
  diagnosticsFromResult,
};
