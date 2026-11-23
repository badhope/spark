"use strict";

// Pure helpers for inline edit / NES. Kept outside extension.js so they can be
// tested with node without a VS Code host.

function contextAround(text, offset, maxChars) {
  const limit = Math.max(200, Number(maxChars) || 3000);
  const at = Math.max(0, Math.min(Number(offset) || 0, text.length));
  return {
    prefix: text.slice(Math.max(0, at - limit), at),
    suffix: text.slice(at, Math.min(text.length, at + limit)),
  };
}

function buildInlineRequest(opts) {
  const mode = opts.mode === "nes" ? "nes" : "edit";
  const body = {
    mode,
    path: opts.path || "",
    language: opts.language || "",
  };
  if (mode === "nes") {
    body.prefix = opts.prefix || "";
    body.suffix = opts.suffix || "";
  } else {
    body.selected = opts.selected || "";
    body.instruction = opts.instruction || "";
  }
  return body;
}

function normalizeSuggestedText(text) {
  const raw = String(text == null ? "" : text).replace(/\r\n/g, "\n");
  const fence = raw.match(/^```[^\n]*\n([\s\S]*?)\n```$/);
  return (fence ? fence[1] : raw).replace(/\s+$/, "");
}

module.exports = { contextAround, buildInlineRequest, normalizeSuggestedText };
