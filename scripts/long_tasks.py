"""Long-running task harness for real-model validation.

Each task is decomposed into resumable steps so a million-word novel or a
multi-file web app can be produced without losing state on a timeout.  Progress
is persisted as JSON, so a re-run continues where the previous one stopped
instead of restarting (and re-paying for) everything.

Usage::

    python scripts/long_tasks.py novel  --out D:/spark-workspace/long/novel --words 1000000
    python scripts/long_tasks.py web    --out D:/spark-workspace/long/web
    python scripts/long_tasks.py word   --out D:/spark-workspace/long/word
    python scripts/long_tasks.py paper  --out D:/spark-workspace/long/paper --chars 3000
    python scripts/long_tasks.py program --out D:/spark-workspace/long/program

The harness talks to the model through Spark's own provider layer, so it
exercises the same code path the agent uses (streaming, retries, usage).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from spark.config import SparkConfig, load_config, require_api_key
from spark.models import ChatMessage
from spark.providers.factory import create_provider


class Journal:
    """Append-only progress log so a crashed run can be resumed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"steps": {}, "started": time.time()}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

    def done(self, key: str) -> bool:
        return key in self.data["steps"]

    def mark(self, key: str, value: Any = True) -> None:
        self.data["steps"][key] = value
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _is_degenerate(text: str, expected_chars: int | None = None) -> str:
    """Detect two failure modes of the endpoint's no-thinking mode.

    1. Runaway length: a 1500-char chapter that comes back 480k chars long.
    2. Repetition: the same sentence re-emitted dozens of times (loops).
    Both are junk the user must never see; rejecting beats saving.
    """
    if expected_chars and len(text) > max(expected_chars * 3, 20000):
        return f"length {len(text)} >> expected {expected_chars}"
    lines = [line.strip()[:60] for line in text.splitlines() if line.strip()]
    if len(lines) >= 12:
        from collections import Counter

        repeated = sum(count for _, count in Counter(lines).most_common(1))
        if repeated / len(lines) > 0.15 and repeated > 4:
            return f"repetition ratio {repeated}/{len(lines)}"
    return ""


async def ask(
    provider: Any,
    prompt: str,
    *,
    system: str = "",
    retries: int = 4,
    max_chars: int | None = None,
) -> str:
    """One model turn, collected from the streaming provider.

    Reasoning models sometimes spend the whole budget on hidden reasoning and
    return empty text; retry a couple of times instead of writing an empty
    chapter/file into the deliverable.
    """
    messages: list[ChatMessage] = []
    if system:
        messages.append(ChatMessage(role="system", content=system))
    messages.append(ChatMessage(role="user", content=prompt))
    last_error = ""
    for attempt in range(retries + 1):
        parts: list[str] = []
        started = time.time()
        usage: dict[str, int] | None = None
        try:
            async for delta in provider.stream(messages, tools=[]):
                if delta.type == "text" and delta.text:
                    parts.append(delta.text)
                if delta.type == "end":
                    usage = delta.usage
        except Exception as exc:  # noqa: BLE001 - transient endpoint errors
            last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            print(f"[retry {attempt + 1}] {last_error}")
            await asyncio.sleep(3 * (attempt + 1))
            continue
        text = "".join(parts).strip()
        if text:
            degenerate = _is_degenerate(text, max_chars)
            if not degenerate:
                print(f"[ok] {len(text)} chars in {round(time.time() - started, 1)}s usage={usage}")
                return text
            last_error = f"degenerate output ({degenerate})"
            print(f"[retry {attempt + 1}] {last_error}")
            await asyncio.sleep(3 * (attempt + 1))
            continue
        last_error = f"empty answer on attempt {attempt + 1}/{retries + 1} usage={usage}"
        print(f"[retry {attempt + 1}] {last_error}")
        await asyncio.sleep(3 * (attempt + 1))
    raise RuntimeError(f"model returned no text ({last_error})")


def _provider(cfg: SparkConfig):
    require_api_key(cfg)
    return create_provider(cfg)


def _novel_tail(book: Path, chars: int = 1200) -> str:
    return book.read_text(encoding="utf-8")[-chars:] if book.exists() else ""


def _append_chapter(book: Path, number: int, text: str) -> None:
    """Write a chapter exactly once.

    Two resumed runs used to append the same chapter twice, producing a file
    with 59 headings for 40 chapters. Chapters are now keyed by number: an
    existing heading is replaced, a new one is inserted in order.
    """
    import re

    heading = re.compile(rf"^## 第 {number} 章\s*$", re.MULTILINE)
    body = text.strip()
    if book.exists():
        existing = book.read_text(encoding="utf-8")
        match = heading.search(existing)
        if match:
            next_heading = re.compile(r"^## 第 \d+ 章\s*$", re.MULTILINE).search(
                existing, match.end()
            )
            end = next_heading.start() if next_heading else len(existing)
            existing = existing[: match.start()] + f"## 第 {number} 章\n\n{body}\n\n" + existing[end:]
            book.write_text(existing.rstrip() + "\n", encoding="utf-8")
            return
    with book.open("a", encoding="utf-8") as handle:
        handle.write(f"\n\n## 第 {number} 章\n\n{body}\n")


# ---------------------------------------------------------------------------
# 1. Million-word novel
# ---------------------------------------------------------------------------

NOVEL_BIBLE = """《长夜灯塔》类型：近未来科幻悬疑。
主角：林澈，28 岁，海岸监测站工程师，擅长修复旧设备，害怕深海。
搭档：苏禾，31 岁，气象数据研究员，理性、话少、记性极好。
反派：周慕，45 岁，能源公司项目负责人，相信“可控的灾难”能换来秩序。
核心设定：近海出现会改变无线电信号的“静默区”，每次扩张都伴随一次失踪。
主题：人如何与无法理解的事物共处。
"""


async def task_novel(args: argparse.Namespace, cfg: SparkConfig) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    journal = Journal(out / "progress.json")
    provider = _provider(cfg)

    target_words = args.words
    chapters = max(1, target_words // args.chapter_words)
    (out / "bible.md").write_text(NOVEL_BIBLE, encoding="utf-8")

    if not journal.done("outline"):
        outline = await ask(
            provider,
            f"根据以下设定，写一份 {chapters} 章的小说大纲。每章一行，格式：章号｜标题｜一句话剧情。\n\n{NOVEL_BIBLE}",
            system="你是长篇小说的结构编辑，输出紧凑、可直接执行的大纲，不要客套。",
        )
        (out / "outline.md").write_text(outline, encoding="utf-8")
        journal.mark("outline", {"chapters": chapters})
        print(f"[novel] outline: {len(outline)} chars")

    outline_text = (out / "outline.md").read_text(encoding="utf-8")
    lines = [line for line in outline_text.splitlines() if "｜" in line or "|" in line]
    if not lines:
        lines = outline_text.splitlines()[:chapters]

    book = out / "novel.md"
    written = 0
    for index in range(chapters):
        key = f"chapter-{index:04d}"
        if journal.done(key):
            continue
        beat = lines[index % len(lines)]
        previous_tail = _novel_tail(book)
        prompt = (
            f"小说设定：\n{NOVEL_BIBLE}\n\n本章大纲：{beat}\n\n"
            f"上一章结尾（用于衔接，可忽略）：\n{previous_tail or '（这是第一章）'}\n\n"
            f"请写第 {index + 1} 章正文，约 {args.chapter_words} 字。"
            "要求：有场景、有对话、有推进；结尾留钩子；只输出正文，不要标题以外的东西。"
        )
        text = await ask(
            provider,
            prompt,
            system="你是小说作者，文风克制、画面感强。",
            max_chars=args.chapter_words * 3,
        )
        _append_chapter(book, index + 1, text)
        written += len(text)
        journal.mark(key, {"chars": len(text), "words_estimate": len(text)})
        if index % 5 == 0:
            print(f"[novel] chapter {index + 1}/{chapters} ({len(text)} chars)")

    total = len(book.read_text(encoding="utf-8")) if book.exists() else 0
    print(f"[novel] done: {total} chars in {book}")
    if total < target_words * 0.6:
        print(f"[novel] NOTE: below target ({target_words}); re-run to continue")


# ---------------------------------------------------------------------------
# 2. Web application
# ---------------------------------------------------------------------------

WEB_FILES = {
    "index.html": "单页应用外壳，引入 style.css 与 app.js，包含输入框、列表、统计栏",
    "style.css": "响应式样式，浅色主题，卡片与按钮，窄屏单列",
    "app.js": "原生 JS：增删改查、localStorage 持久化、筛选与统计、无框架",
    "README.md": "运行方式与功能说明",
}


async def task_web(args: argparse.Namespace, cfg: SparkConfig) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    journal = Journal(out / "progress.json")
    provider = _provider(cfg)
    spec = (
        "做一个「个人书签管理器」纯静态网页：可添加书签（标题+网址+标签）、"
        "按标签筛选、搜索、删除、统计总数；数据存 localStorage；"
        "不使用任何构建工具或第三方 CDN，双击 index.html 即可运行。"
    )
    (out / "spec.md").write_text(spec, encoding="utf-8")
    for name, role in WEB_FILES.items():
        key = f"file-{name}"
        if journal.done(key) and (out / name).exists():
            continue
        text = await ask(
            provider,
            f"项目需求：{spec}\n\n只输出文件 {name} 的完整内容。职责：{role}。"
            "不要 Markdown 代码块围栏，不要解释。",
            system="你是资深前端工程师，代码可直接运行，无外部依赖。",
        )
        (out / name).write_text(_strip_fence(text), encoding="utf-8")
        journal.mark(key)
        print(f"[web] wrote {name}")

    # Real browser check: a page that renders but throws on click is not done.
    for attempt in range(3):
        problems, detail = await asyncio.to_thread(_web_smoke, out)
        if not problems:
            print("[web] browser smoke passes")
            return
        print(f"[web] repair {attempt + 1}: {problems}")
        files = {name: (out / name).read_text(encoding="utf-8") for name in ("index.html", "app.js")}
        fixed = await ask(
            provider,
            "网页在真实浏览器里报错/不工作，请修复。当前文件：\n\n"
            f"index.html:\n{files['index.html']}\n\napp.js:\n{files['app.js']}\n\n"
            f"浏览器错误：\n{detail[:2000]}\n\n"
            "只输出 app.js 的完整修复版本，不要解释。",
            system="你是资深前端工程师，必须消除运行时报错，事件绑定要指向真实存在的元素。",
        )
        (out / "app.js").write_text(_strip_fence(fixed), encoding="utf-8")
        journal.mark(f"web-repair-{attempt}")


_EDGE_PATHS = [
    Path(r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path(r"C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
]


def _launch(playwright):
    """Prefer the system Edge; fall back to a bundled Chromium."""
    if any(path.exists() for path in _EDGE_PATHS):
        return playwright.chromium.launch(channel="msedge", headless=True)
    return playwright.chromium.launch(headless=True)


def _web_smoke(out: Path) -> tuple[list[str], str]:
    """Open the built page in a real browser, click the primary action."""
    import threading

    from playwright.sync_api import sync_playwright

    port = 18000 + (abs(hash(str(out))) % 500)
    import functools
    import http.server
    import socketserver

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    problems: list[str] = []
    detail: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            page = browser.new_context(viewport={"width": 1280, "height": 860}).new_page()
            page.on("pageerror", lambda exc: detail.append(f"pageerror: {exc}"))
            page.on("console", lambda msg: detail.append(f"{msg.type}: {msg.text}") if msg.type == "error" else None)
            page.goto(f"http://127.0.0.1:{port}/index.html", wait_until="domcontentloaded")
            page.wait_for_timeout(600)
            for idx, placeholder in enumerate(["书签标题", "Title"]):
                field = page.locator(f'input[placeholder*="{placeholder}"]').first
                if field.count():
                    field.fill("Spark")
                    break
            url_field = page.locator('input[placeholder*="网址"], input[placeholder*="URL"]').first
            if url_field.count():
                url_field.fill("https://github.com/badhope/spark")
            add = page.get_by_role("button").filter(has_text="添加").first
            if not add.count():
                problems.append("no add button")
            else:
                add.click()
                page.wait_for_timeout(400)
                body = page.inner_text("body")
                if "Spark" not in body:
                    problems.append("added bookmark not rendered")
                if "总数：0" in body or "total: 0" in body.lower():
                    problems.append("counter did not update")
            errors = [d for d in detail if "pageerror" in d]
            if errors:
                problems.append(f"{len(errors)} JS error(s)")
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    return problems, "\n".join(detail)


# ---------------------------------------------------------------------------
# 3. Word document processing
# ---------------------------------------------------------------------------

async def task_word(args: argparse.Namespace, cfg: SparkConfig) -> None:
    from docx import Document

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    journal = Journal(out / "progress.json")
    provider = _provider(cfg)

    source = out / "source.docx"
    if not source.exists():
        doc = Document()
        doc.add_heading("季度运营说明", level=1)
        doc.add_paragraph("本季度收入 1280 万元，同比增长 18%。客户续约率 91%。")
        doc.add_paragraph("问题：交付周期偏长，平均 21 天；客服响应中位数 4.2 小时。")
        table = doc.add_table(rows=3, cols=2)
        table.cell(0, 0).text = "指标"
        table.cell(0, 1).text = "数值"
        table.cell(1, 0).text = "收入"
        table.cell(1, 1).text = "1280 万"
        table.cell(2, 0).text = "续约率"
        table.cell(2, 1).text = "91%"
        doc.save(source)

    if not journal.done("summary"):
        text = "\n".join(p.text for p in Document(str(source)).paragraphs)
        summary = await ask(
            provider,
            f"阅读以下文档内容，输出一段 150 字以内的中文摘要，并列出 3 条改进建议。\n\n{text}",
            system="你是运营顾问，结论明确、不空话。",
        )
        journal.mark("summary", {"chars": len(summary)})
        (out / "summary.md").write_text(summary, encoding="utf-8")
        print(f"[word] summary {len(summary)} chars")

    if not journal.done("rewrite"):
        doc = Document(str(source))
        summary = (out / "summary.md").read_text(encoding="utf-8")
        doc.add_heading("摘要与建议", level=1)
        for line in summary.splitlines():
            if line.strip():
                doc.add_paragraph(line.strip())
        doc.save(out / "updated.docx")
        journal.mark("rewrite")

    verify = Document(str(out / "updated.docx"))
    headings = [p.text for p in verify.paragraphs if p.style.name.startswith("Heading")]
    print(f"[word] updated.docx headings: {headings}")


# ---------------------------------------------------------------------------
# 4. Essay / paper
# ---------------------------------------------------------------------------

async def task_paper(args: argparse.Namespace, cfg: SparkConfig) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    journal = Journal(out / "progress.json")
    provider = _provider(cfg)
    sections = ["摘要", "引言", "相关工作", "方法", "实验结果", "讨论", "结论"]
    topic = args.topic

    parts: list[str] = []
    for name in sections:
        key = f"section-{name}"
        path = out / f"{name}.md"
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
            journal.mark(key)
            continue
        prompt = (
            f"写论文《{topic}》的「{name}」部分，约 {args.chars // len(sections)} 字。"
            "学术语体，论点与证据分开，给出可核对的推理；不要虚构具体引用编号。"
        )
        text = await ask(provider, prompt, system="你是严谨的中文学术写作助手。")
        path.write_text(text, encoding="utf-8")
        journal.mark(key, {"chars": len(text)})
        parts.append(text)
        print(f"[paper] {name}: {len(text)} chars")

    full = "\n\n".join(f"## {name}\n\n{text}" for name, text in zip(sections, parts))
    (out / "paper.md").write_text(f"# {topic}\n\n{full}", encoding="utf-8")
    print(f"[paper] total {len(full)} chars")


# ---------------------------------------------------------------------------
# 5. Small program
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    from spark.proc import run_bounded

    result = run_bounded(cmd, cwd=cwd, timeout=90)
    return result.returncode, (result.stdout + result.stderr).strip()


async def task_program(args: argparse.Namespace, cfg: SparkConfig) -> None:
    """Generate a CLI, then repair it against real runtime errors.

    One-shot generation produced code that did not run and tests that imported a
    module which never existed.  The fix is the loop a human would use: run it,
    feed the error back, ask for the corrected file, run again.
    """
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    journal = Journal(out / "progress.json")
    provider = _provider(cfg)
    python = sys.executable
    spec = (
        "写一个 Python CLI 工具 expense.py：读取 CSV（日期,类别,金额,备注），"
        "支持 --month 过滤、--category 过滤、按类别汇总、输出表格；"
        "无第三方依赖；包含 --help。"
    )
    sample = out / "sample.csv"
    if not sample.exists():
        sample.write_text(
            "日期,类别,金额,备注\n"
            "2026-01-05,餐饮,58,午餐\n"
            "2026-01-06,交通,12,地铁\n"
            "2026-02-01,餐饮,88,聚餐\n",
            encoding="utf-8",
        )

    if not journal.done("code"):
        code = _strip_fence(
            await ask(
                provider,
                f"{spec}\n\n只输出 expense.py 的完整代码，不要解释。",
                system="你是资深 Python 工程师，代码可运行、有基本错误处理。",
            )
        )
        (out / "expense.py").write_text(code, encoding="utf-8")
        journal.mark("code", {"chars": len(code)})
        print(f"[program] expense.py {len(code)} chars")

    # Repair against the real CLI contract instead of guessing it.
    checks = [
        ([python, "expense.py", "--help"], "expense.py --help"),
        ([python, "expense.py", "sample.csv"], "expense.py sample.csv"),
        ([python, "expense.py", "sample.csv", "--category", "餐饮"], "expense.py --category"),
        ([python, "expense.py", "sample.csv", "--month", "2026-02"], "expense.py --month"),
    ]
    for attempt in range(3):
        failures = []
        for cmd, label in checks:
            code, output = _run(cmd, out)
            if code != 0:
                failures.append(f"$ {label}\n{output[:1200]}")
        if not failures:
            print("[program] all CLI checks pass")
            break
        print(f"[program] repair attempt {attempt + 1}: {len(failures)} failing check(s)")
        current = (out / "expense.py").read_text(encoding="utf-8")
        fixed = _strip_fence(
            await ask(
                provider,
                f"以下程序在运行时报错。请修复并输出完整文件。\n\n程序：\n{current}\n\n"
                f"错误：\n" + "\n\n".join(failures) + "\n\n"
                f"契约：\n{spec}\n\n只输出修复后的 expense.py 完整代码。",
                system="你是资深 Python 工程师，先修 bug，不要改需求。",
            )
        )
        (out / "expense.py").write_text(fixed, encoding="utf-8")
        journal.mark(f"repair-{attempt}", {"chars": len(fixed)})

    if not journal.done("tests"):
        tests = _strip_fence(
            await ask(
                provider,
                f"为以下程序写 pytest 测试（至少 3 个用例，覆盖正常与异常）：\n\n"
                f"{(out / 'expense.py').read_text(encoding='utf-8')}\n\n"
                "测试必须 import expense（文件名 expense.py），只输出 test_expense.py。",
                system="你是测试工程师，断言明确。",
            )
        )
        (out / "test_expense.py").write_text(tests, encoding="utf-8")
        journal.mark("tests", {"chars": len(tests)})
        print(f"[program] test_expense.py {len(tests)} chars")

    for attempt in range(3):
        code, output = _run([python, "-m", "pytest", "-q"], out)
        if code == 0:
            print("[program] pytest passes")
            break
        print(f"[program] test repair {attempt + 1}: {output[-600:]}")
        tests = (out / "test_expense.py").read_text(encoding="utf-8")
        program = (out / "expense.py").read_text(encoding="utf-8")
        fixed = _strip_fence(
            await ask(
                provider,
                f"测试失败。程序：\n{program}\n\n测试：\n{tests}\n\n失败输出：\n{output[-2000:]}\n\n"
                "修正测试（若程序有 bug 也可同时修正程序）。只输出 test_expense.py 完整代码。",
                system="你是测试工程师，先让测试真实反映行为。",
            )
        )
        (out / "test_expense.py").write_text(fixed, encoding="utf-8")
        journal.mark(f"test-repair-{attempt}", {"chars": len(fixed)})


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip() + "\n"


TASKS = {
    "novel": task_novel,
    "web": task_web,
    "word": task_word,
    "paper": task_paper,
    "program": task_program,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Spark long-task harness")
    parser.add_argument("task", choices=sorted(TASKS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--words", type=int, default=1_000_000)
    parser.add_argument("--chapter-words", type=int, default=1200)
    parser.add_argument("--chars", type=int, default=3000)
    parser.add_argument("--topic", default="本地优先 AI 编程智能体的权限模型设计")
    args = parser.parse_args(argv)

    cfg = load_config(workdir=Path.cwd())
    asyncio.run(TASKS[args.task](args, cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
