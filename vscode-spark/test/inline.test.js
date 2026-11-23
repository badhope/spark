"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { contextAround, buildInlineRequest, normalizeSuggestedText } = require("../src/inline");

test("contextAround keeps bounded prefix and suffix", () => {
  const text = "a".repeat(5000) + "CARET" + "b".repeat(5000);
  const at = text.indexOf("CARET");
  const ctx = contextAround(text, at, 1000);
  assert.equal(ctx.prefix.length, 1000);
  assert.equal(ctx.suffix.length, 1000);
  assert.ok(ctx.prefix.endsWith("a"));
});

test("buildInlineRequest distinguishes edit and NES", () => {
  const edit = buildInlineRequest({ mode: "edit", path: "a.py", selected: "x", instruction: "add one" });
  assert.equal(edit.mode, "edit");
  assert.equal(edit.selected, "x");
  assert.equal(edit.prefix, undefined);

  const nes = buildInlineRequest({ mode: "nes", path: "a.py", prefix: "x =", suffix: "1" });
  assert.equal(nes.mode, "nes");
  assert.equal(nes.prefix, "x =");
  assert.equal(nes.suffix, "1");
  assert.equal(nes.selected, undefined);
});

test("normalizeSuggestedText unwraps a fence and trailing blank lines", () => {
  assert.equal(normalizeSuggestedText("```python\nreturn 1\n```"), "return 1");
  assert.equal(normalizeSuggestedText("return 1\n\n"), "return 1");
});
