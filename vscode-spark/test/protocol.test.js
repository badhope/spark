"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../src/protocol");

test("parseSseText handles CRLF, ids, comments and partial tails", () => {
  const first = "id: 1\r\ndata: {\"type\":\"text_delta\",\"text\":\"你好\"}\r\n\r\nid: 2\r\ndata: {\"type\":\"turn_end\"}";
  const parsed = P.parseSseText(first);
  assert.equal(parsed.events.length, 1);
  assert.equal(parsed.events[0].id, "1");
  assert.equal(parsed.events[0].payload.text, "你好");
  assert.match(parsed.rest, /turn_end/);

  const next = P.parseSseText(parsed.rest + "\n\n");
  assert.equal(next.events.length, 1);
  assert.equal(next.events[0].payload.type, "turn_end");
});

test("parseSseText skips keepalive comments and malformed JSON", () => {
  const parsed = P.parseSseText(": ping\n\ndata: not-json\n\ndata: {\"type\":\"done\"}\n\n");
  assert.equal(parsed.events.length, 1);
  assert.equal(parsed.events[0].payload.type, "done");
});

test("parseSseText joins multi-line data frames", () => {
  const parsed = P.parseSseText('data: {"type":"text_delta",\ndata: "text":"ok"}\n\n');
  assert.equal(parsed.events[0].payload.text, "ok");
});

test("buildSelectionPrompt includes path, range and fenced code", () => {
  const prompt = P.buildSelectionPrompt("修复这个 bug", {
    file: "src/a.py",
    startLine: 10,
    endLine: 12,
    language: "python",
    text: "def f():\n    return 1",
  });
  assert.match(prompt, /^修复这个 bug/);
  assert.match(prompt, /src\/a\.py:10-12/);
  assert.match(prompt, /```python/);
  assert.match(prompt, /def f\(\)/);
});

test("applyUnifiedDiff applies one hunk and preserves surrounding lines", () => {
  const original = "a\nb\nc\nd\n";
  const diff = ["--- a/file.txt", "+++ b/file.txt", "@@ -2,3 +2,3 @@", " b", "-c", "+C", " d", ""].join("\n");
  assert.equal(P.applyUnifiedDiff(original, diff), "a\nb\nC\nd\n");
});

test("applyUnifiedDiff supports insertions without old line range", () => {
  const original = "a\nb\n";
  const diff = "@@ -1,1 +1,2 @@\n a\n+new\n";
  assert.equal(P.applyUnifiedDiff(original, diff), "a\nnew\nb\n");
});

test("applyUnifiedDiff rejects malformed/out-of-order hunks", () => {
  assert.equal(P.applyUnifiedDiff("a\n", "not a diff"), null);
  assert.equal(P.applyUnifiedDiff("a\n", "@@ -99,1 +99,1 @@\n-x\n+y\n"), null);
});

test("applySearchReplace replaces unique blocks", () => {
  const out = P.applySearchReplace("x = 1\ny = 2\n", "<<<<<<< SEARCH\nx = 1\n=======\nx = 10\n>>>>>>> REPLACE\n");
  assert.equal(out, "x = 10\ny = 2\n");
});

test("proposedContentFor understands write_file and apply_patch", () => {
  assert.equal(
    P.proposedContentFor("write_file", { content: "hello\n" }, "", null),
    "hello\n"
  );
  assert.equal(
    P.proposedContentFor("apply_patch", { old_text: "old", new_text: "new" }, "old value", null),
    "new value"
  );
  assert.equal(
    P.proposedContentFor("apply_patch", { old_text: "missing", new_text: "new" }, "old", null),
    null
  );
});

test("proposedContentFor falls back to unified diff", () => {
  const original = "a\nb\n";
  const diff = "@@ -1,2 +1,2 @@\n a\n-b\n+c\n";
  assert.equal(P.proposedContentFor("apply_edit", {}, original, diff), "a\nc\n");
});

test("extractFileReferences walks diagnostics and path:line strings", () => {
  const refs = P.extractFileReferences({
    diagnostics: [{ file: "src/a.py", line: 3, character: 2, severity: "error", message: "x" }],
    output: "FAILED tests/test_x.py:42: assertion",
  });
  assert.equal(refs.length, 2);
  assert.ok(refs.some((r) => r.path === "src/a.py" && r.line === 3 && r.column === 2));
  assert.ok(refs.some((r) => r.path === "tests/test_x.py" && r.line === 42 && r.column === 0));
});

test("extractFileReferences accepts grep-style string arrays", () => {
  const refs = P.extractFileReferences({ matches: ["src/a.py:3: hit", "lib/b.ts:9"] });
  assert.deepEqual(refs, [
    { path: "src/a.py", line: 3, column: 0 },
    { path: "lib/b.ts", line: 9, column: 0 },
  ]);
});

test("diagnosticsFromResult normalizes severities", () => {
  const out = P.diagnosticsFromResult({
    diagnostics: [
      { path: "a.py", line: 1, character: 1, message: "boom", severity: "ERROR" },
      { path: "", line: 2, message: "ignored" },
    ],
  });
  assert.equal(out.length, 1);
  assert.equal(out[0].severity, "error");
});

test("summarizeToolResult handles suite/test/diagnostic shapes", () => {
  assert.equal(P.summarizeToolResult("diagnostics", { diagnostics: [1, 2] }, true), "2 diagnostics");
  assert.equal(P.summarizeToolResult("grep", { total: 5 }, true), "5 items");
  assert.equal(P.summarizeToolResult("run_tests", { summary: "FAILED 1 passed 9" }, true), "FAILED 1 passed 9");
  assert.equal(P.summarizeToolResult("run_shell", { output: "ok" }, true), "ok");
  assert.equal(P.summarizeToolResult("x", { ok: false }, false), "failed");
});
