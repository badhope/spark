#!/usr/bin/env node
/**
 * i18n gate: dictionary parity, used-key existence, and hardcoded Chinese UI.
 *
 * Rules:
 * 1. `zh` and `en` must expose exactly the same keys.
 * 2. Any key passed to `t("key", …)` must exist in both dictionaries.
 * 3. `en` values must not contain CJK characters.
 * 4. JSX text containing CJK is always a failure.
 * 5. String literals containing CJK are failures unless they are:
 *    - inside the true branch of a `zh` / `lang === "zh"` conditional;
 *    - a `zh`/`en` property of a bilingual object;
 *    - paired with a sibling `*_en` field (preset/title data);
 *    - explicitly marked `i18n-exempt` (user-facing templates/content defaults).
 *
 * No baseline: new hardcoded copy fails the build; old debt is fixed in place.
 */
import fs from "node:fs"
import path from "node:path"
import ts from "typescript"

const ROOT = process.cwd()
const SRC = path.join(ROOT, "src")
const I18N = path.join(SRC, "i18n.ts")
const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/

function sourceFiles(dir) {
  const out = []
  for (const name of fs.readdirSync(dir)) {
    const file = path.join(dir, name)
    const stat = fs.statSync(file)
    if (stat.isDirectory()) out.push(...sourceFiles(file))
    else if (/\.(ts|tsx|js|jsx)$/.test(name) && file !== I18N) out.push(file)
  }
  return out
}

function parse(file) {
  const text = fs.readFileSync(file, "utf8")
  const kind = file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  return { text, sf: ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind) }
}

function propertyName(node) {
  if (!node) return ""
  if (ts.isIdentifier(node) || ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text
  return node.getText()
}

function objectProperties(obj) {
  if (!ts.isObjectLiteralExpression(obj)) return new Map()
  const map = new Map()
  for (const prop of obj.properties) {
    if (ts.isPropertyAssignment(prop)) map.set(propertyName(prop.name), prop.initializer)
  }
  return map
}

function readDictionary() {
  const { sf } = parse(I18N)
  let dict = null
  function visit(node) {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.name.text === "dict" &&
      node.initializer
    ) {
      const init = ts.isAsExpression(node.initializer) ? node.initializer.expression : node.initializer
      if (ts.isObjectLiteralExpression(init)) dict = init
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  if (!dict) throw new Error("i18n.ts: cannot find `const dict = {...}`")
  const langs = objectProperties(dict)
  const zh = objectProperties(langs.get("zh"))
  const en = objectProperties(langs.get("en"))
  if (!zh.size || !en.size) throw new Error("i18n.ts: zh/en dictionaries are empty")
  return { zh, en }
}

function textOf(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text
  if (ts.isTemplateExpression(node)) {
    return node.head.text + node.templateSpans.map((s) => s.literal.text).join("")
  }
  return ""
}

function insideTrueBranch(node, boundary) {
  let child = node
  let parent = node.parent
  while (parent && parent !== boundary) {
    if (ts.isConditionalExpression(parent) && isZhCondition(parent.condition)) {
      if (child.pos >= parent.whenTrue.pos && child.end <= parent.whenTrue.end) return true
    }
    child = parent
    parent = parent.parent
  }
  return false
}

function isZhCondition(cond) {
  const text = cond.getText().trim()
  return text === "zh" || /lang\s*===\s*["']zh["']/.test(text) || /lang\s*!==\s*["']en["']/.test(text)
}

function hasBilingualSibling(prop) {
  if (!ts.isPropertyAssignment(prop)) return false
  const parent = prop.parent
  if (!ts.isObjectLiteralExpression(parent)) return false
  const key = propertyName(prop.name)
  const siblings = parent.properties
    .filter((p) => ts.isPropertyAssignment(p))
    .map((p) => propertyName(p.name))
  if (siblings.includes(`${key}_en`)) return true
  if (["zh", "en"].includes(key)) return true
  return false
}

function statementHasExemptComment(sf, node) {
  const start = node.getFullStart()
  const text = sf.text.slice(Math.max(0, start - 400), node.getStart(sf))
  return /i18n-exempt/.test(text)
}

function lineHasExemptComment(sf, node) {
  const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf))
  const lineText = sf.text.split("\n")[line] || ""
  return /i18n-exempt/.test(lineText)
}

function checkModelErrorsCopy(sf, rel, problems) {
  let copy = null
  function visit(node) {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.name.text === "COPY" &&
      node.initializer
    ) {
      copy = node.initializer
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  if (!copy || !ts.isObjectLiteralExpression(copy)) {
    problems.push(`${rel}: cannot find COPY zh/en table`)
    return
  }
  for (const prop of copy.properties) {
    if (!ts.isPropertyAssignment(prop) || !ts.isObjectLiteralExpression(prop.initializer)) continue
    const code = propertyName(prop.name)
    const pair = objectProperties(prop.initializer)
    if (!pair.has("zh") || !pair.has("en")) {
      problems.push(`${rel}: COPY["${code}"] must have both zh and en`)
      continue
    }
    for (const field of ["title", "message", "hint"]) {
      const zhValue = pair.get("zh")
      const enValue = pair.get("en")
      if (!zhValue || !enValue) continue
      const enProps = objectProperties(enValue)
      const text = enProps.get(field)
      if (text && CJK.test(textOf(text))) {
        problems.push(`${rel}: COPY["${code}"].en.${field} contains Chinese text`)
      }
    }
  }
}

const { zh, en } = readDictionary()
const problems = []
const zhKeys = new Set(zh.keys())
const enKeys = new Set(en.keys())

for (const key of zhKeys) if (!enKeys.has(key)) problems.push(`i18n: key "${key}" exists in zh but not en`)
for (const key of enKeys) if (!zhKeys.has(key)) problems.push(`i18n: key "${key}" exists in en but not zh`)
for (const [key, value] of en) {
  if (CJK.test(textOf(value))) problems.push(`i18n: en["${key}"] contains Chinese text: ${textOf(value)}`)
}

let used = 0
const files = sourceFiles(SRC)
for (const file of files) {
  const { sf } = parse(file)
  const rel = path.relative(ROOT, file)
  if (rel.endsWith("modelErrors.ts")) {
    checkModelErrorsCopy(sf, rel, problems)
    continue
  }
  function visit(node) {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "t") {
      const arg = node.arguments[0]
      if (arg && ts.isStringLiteral(arg)) {
        used += 1
        if (!zhKeys.has(arg.text) || !enKeys.has(arg.text)) {
          const pos = sf.getLineAndCharacterOfPosition(arg.getStart(sf))
          problems.push(`${rel}:${pos.line + 1}: t("${arg.text}") has no zh/en entry`)
        }
      }
    }
    if (ts.isJsxText(node) && CJK.test(node.text)) {
      const pos = sf.getLineAndCharacterOfPosition(node.getStart(sf))
      problems.push(`${rel}:${pos.line + 1}: hardcoded Chinese JSX text: ${node.text.trim().replace(/\s+/g, " ")}`)
    }
    const isTextNode =
      ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateExpression(node)
    if (isTextNode && textOf(node) && CJK.test(textOf(node))) {
      const exempt =
        insideTrueBranch(node, sf) ||
        (ts.isPropertyAssignment(node.parent) && hasBilingualSibling(node.parent)) ||
        statementHasExemptComment(sf, node.parent) ||
        statementHasExemptComment(sf, node) ||
        lineHasExemptComment(sf, node)
      if (!exempt) {
        const pos = sf.getLineAndCharacterOfPosition(node.getStart(sf))
        problems.push(`${rel}:${pos.line + 1}: hardcoded Chinese string: ${textOf(node).trim().replace(/\s+/g, " ").slice(0, 120)}`)
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
}

if (problems.length) {
  console.error(`i18n gate failed: ${problems.length} issue(s)\n`)
  for (const problem of problems) console.error(`  - ${problem}`)
  console.error("\nFix with t(...), a zh/en branch, bilingual *_en field, or an explicit i18n-exempt comment.")
  process.exit(1)
}

console.log(`i18n gate passed: ${zhKeys.size} keys aligned, ${used} t() calls resolved, no hardcoded Chinese UI.`)
