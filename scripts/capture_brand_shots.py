"""Capture the brand-refresh screenshots used by both READMEs.

Unlike ``scripts/screenshots.py`` this script drives Chromium through
Playwright, so it can set the theme and language before the app boots and can
capture at a retina scale. It starts the real Web server with the ``mock``
provider and seeds a realistic multi-turn session; no model key is required.

    python scripts/capture_brand_shots.py

Outputs:
  assets/screenshots/40-brand-empty-dark.png
  assets/screenshots/41-brand-chat-dark.png
  assets/screenshots/42-brand-settings-dark.png
  assets/screenshots/43-brand-chat-light.png
  assets/screenshots/45-brand-mobile-home.png

Each PNG is mirrored to WebP for the READMEs; PNG stays as the capture master.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "screenshots"
DEMO_ROOT = Path(os.environ.get("SPARK_BRAND_DEMO_ROOT", "/workspace"))
DEMO_HOME = DEMO_ROOT / ".spark-demo"
DEMO_WORKDIR = DEMO_ROOT / "spark"
sys.path.insert(0, str(ROOT / "src"))


def seed(store) -> str:
    from spark.models import ChatMessage

    # A little history makes the sidebar feel lived-in without pretending to be
    # a benchmark. The last session becomes the selected one after reload.
    store.create_session(DEMO_WORKDIR, "demo-model", title="给 parser 加一层缓存")
    sid = store.create_session(DEMO_WORKDIR, "demo-model", title="修复登录页表单校验")
    turns = [
        ("user", "读一下 src/pages/Login.tsx，告诉我表单校验为什么没生效"),
        (
            "assistant",
            "我读了 `Login.tsx`，问题出在提交路径上。\n\n"
            "`onSubmit` 先 `preventDefault()`，然后直接调用 `login()`，"
            "`validate()` 从未 await；`emailRules` 的 `required` 又只挂在 `blur` 上，"
            "提交时不会触发。\n\n"
            "修复分两步：\n\n"
            "1. 提交前显式 `await formRef.value.validate()`，失败立即 return；\n"
            "2. 把 `required` 的 trigger 改为 `['blur', 'change']`。\n\n"
            "我先改代码，再补一条空邮箱提交的回归测试。",
        ),
        ("user", "好，按这个方案改，然后跑相关测试"),
        (
            "assistant",
            "已改完 `Login.tsx`：\n\n"
            "```ts\nconst valid = await formRef.value.validate()\nif (!valid) return\nawait login(form)\n```\n\n"
            "补了 `should block submit when email empty`，`npm test -- login` 4 passed（新增 1 条）。\n\n"
            "变更落在当前任务分支，未提交；每一步都在执行图和回滚点里。",
        ),
    ]
    for role, content in turns:
        store.append_message(sid, ChatMessage(role=role, content=content))
    return sid


def start_server(seed_session: bool) -> tuple[int, object, object, object]:
    DEMO_HOME.mkdir(parents=True, exist_ok=True)
    DEMO_WORKDIR.mkdir(parents=True, exist_ok=True)
    home = DEMO_HOME / ("seeded" if seed_session else "empty")
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True, exist_ok=True)
    os.environ["SPARK_HOME"] = str(home)
    from spark.config import SparkConfig
    from spark.store import SessionStore
    from spark.web.server import build_web_server

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "demo-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(home / "sessions.db")
    if seed_session:
        seed(store)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server, state = build_web_server(workdir=DEMO_WORKDIR, cfg=cfg, store=store, host="127.0.0.1", port=port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(1.5)
    return port, server, store, state


def wait_text(page, needle: str, timeout: float = 30.0) -> None:
    """Wait for rendered text without JS eval (the app ships a strict CSP)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if needle in page.locator("body").inner_text():
            return
        page.wait_for_timeout(400)
    raise TimeoutError(f"text not found: {needle}")


def shot(page, name: str, out_width: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / f".{name}.2x.png"
    page.screenshot(path=str(tmp), full_page=False)
    img = Image.open(tmp).convert("RGB")
    if img.width != out_width:
        ratio = out_width / img.width
        img = img.resize((out_width, round(img.height * ratio)), Image.LANCZOS)
    out = OUT / name
    img.save(out, optimize=True)
    tmp.unlink(missing_ok=True)
    print(f"  {name}  {img.width}×{img.height}  {out.stat().st_size // 1024} KB")


def main() -> int:
    import json

    from playwright.sync_api import sync_playwright

    browser = None
    server = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path="/usr/local/bin/chromium")

            # ---- empty state, on a clean store ---------------------------------
            port, server, _store, _state = start_server(seed_session=False)
            empty_url = f"http://127.0.0.1:{port}/"
            print(f"empty server: {empty_url}")
            ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
            ctx.add_init_script(
                "localStorage.setItem('spark-theme','dark');"
                "localStorage.setItem('spark-palette','ember');"
                "localStorage.setItem('spark-lang','zh');"
            )
            page = ctx.new_page()
            page.goto(empty_url + "?onboarding=skip", wait_until="load")
            page.wait_for_selector("textarea", timeout=30_000)
            page.get_by_text("本地自托管的 AI 编程智能体").first.wait_for(timeout=30_000)
            page.wait_for_timeout(800)
            shot(page, "40-brand-empty-dark.png", 1800)
            ctx.close()
            server.shutdown()
            server = None

            # ---- seeded store: active session must exist before the first paint
            port, server, store, _state = start_server(seed_session=True)
            url = f"http://127.0.0.1:{port}/"
            print(f"seeded server: {url}")
            latest = next(s["id"] for s in store.list_sessions() if s["title"] == "修复登录页表单校验")
            ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
            ctx.add_init_script(
                "localStorage.setItem('spark-theme','dark');"
                "localStorage.setItem('spark-palette','ember');"
                "localStorage.setItem('spark-lang','zh');"
            )
            ctx.request.post(
                url + "api/sessions/switch",
                data=json.dumps({"session_id": latest}),
                headers={"Content-Type": "application/json"},
            )
            print(f"active session: {latest}")
            page = ctx.new_page()
            page.goto(url + "?onboarding=skip", wait_until="load")
            page.wait_for_selector("textarea", timeout=30_000)
            wait_text(page, "表单校验为什么没生效", 30)
            page.wait_for_timeout(1000)
            shot(page, "41-brand-chat-dark.png", 1800)

            page.goto(url + "?onboarding=skip&panel=settings&tab=model", wait_until="load")
            page.wait_for_selector("textarea", timeout=30_000)
            wait_text(page, "模型", 30)
            page.wait_for_timeout(2500)
            shot(page, "42-brand-settings-dark.png", 1800)
            ctx.close()

            # Light theme: same product, same session, for the day/night contrast.
            ctx2 = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
            ctx2.add_init_script(
                "localStorage.setItem('spark-theme','light');"
                "localStorage.setItem('spark-palette','ember');"
                "localStorage.setItem('spark-lang','zh');"
            )
            page2 = ctx2.new_page()
            page2.goto(url + "?onboarding=skip", wait_until="load")
            page2.wait_for_selector("textarea", timeout=30_000)
            wait_text(page2, "表单校验为什么没生效", 30)
            page2.wait_for_timeout(1000)
            shot(page2, "43-brand-chat-light.png", 1800)
            ctx2.close()

            # Mobile home at a real 390×844 viewport, not a squeezed desktop.
            ctx3 = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3)
            ctx3.add_init_script(
                "localStorage.setItem('spark-theme','light');"
                "localStorage.setItem('spark-palette','ember');"
                "localStorage.setItem('spark-lang','zh');"
            )
            page3 = ctx3.new_page()
            page3.goto(url + "?onboarding=skip", wait_until="load")
            page3.wait_for_timeout(10000)
            shot(page3, "45-brand-mobile-home.png", 780)
            ctx3.close()
    finally:
        if server is not None:
            server.shutdown()

    for png in OUT.glob("*brand*.png"):
        Image.open(png).convert("RGB").save(
            png.with_suffix(".webp"), "WEBP", quality=90, method=6
        )

    print("done")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
