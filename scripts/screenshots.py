"""文档截图自动化：起真实 mock 服务（预置会话）→ headless Edge/Chrome 截图。

产出写回 ``assets/screenshots/``（README 已引用这些路径）。无新依赖：
浏览器用本机 Edge/Chrome 的 ``--headless --screenshot``，服务用项目自身的
``build_web_server`` + 预置的 SessionStore 数据。

用法::

    python scripts/screenshots.py          # 全量重拍
    python scripts/screenshots.py --check  # 只验证浏览器与服务可用（CI 用）
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "screenshots"

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]
DARWIN_CANDIDATES = [
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_browser() -> str | None:
    candidates = (
        EDGE_CANDIDATES if sys.platform == "win32" else DARWIN_CANDIDATES
    )
    for c in candidates:
        if Path(c).exists():
            return c
    found = shutil.which("chrome") or shutil.which("msedge")
    return found


def seed_demo_session(store) -> str:
    """预置一段真实的对话（mock 语义），让截图里的 UI 有内容可看。"""
    from spark.models import ChatMessage

    sid = store.create_session(ROOT, "demo-model", title="修复登录页表单校验")
    turns = [
        ("user", "读一下 src/pages/Login.tsx，告诉我表单校验为什么没生效"),
        (
            "assistant",
            "我读了 Login.tsx：`onSubmit` 里直接 `preventDefault()` 后调用了 API，"
            "没有先跑 `validate()`；而且 `emailRules` 的 `required` 写在了 `blur` "
            "触发器上，提交时不触发。\n\n修复方案：\n1. 提交前显式 await `formRef.validate()`\n"
            "2. 把 `required` 规则的 trigger 改为 `['blur', 'change']`",
        ),
        ("user", "好，按这个方案改，然后跑一遍相关测试"),
        (
            "assistant",
            "已修改 Login.tsx 两处并补了一条空邮箱提交的用例：\n"
            "- `validate()` 失败时提前 return，不再发请求\n"
            "- 新增测试 `should block submit when email empty`\n\n"
            "`npm test -- login` 4 passed（新增 1 条）。",
        ),
    ]
    for role, content in turns:
        store.append_message(sid, ChatMessage(role=role, content=content))
    return sid


def start_server() -> tuple[int, object, object]:
    sys.path.insert(0, str(ROOT / "src"))
    home = Path(tempfile.mkdtemp(prefix="spark-shots-"))
    import os

    os.environ["SPARK_HOME"] = str(home)
    from spark.config import SparkConfig
    from spark.store import SessionStore
    from spark.web.server import build_web_server

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "demo-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(home / "sessions.db")
    seed_demo_session(store)
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server, _state = build_web_server(
        workdir=ROOT, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(1.5)
    return port, server, store


def shoot(browser: str, url: str, out: Path, *, wait_ms: int = 9000) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    cmd = [
        browser,
        "--headless",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--screenshot={out}",
        "--window-size=1440,900",
        f"--virtual-time-budget={wait_ms}",
        "--no-first-run",
        "--disable-extensions",
        url,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60, text=True)
    except subprocess.TimeoutExpired:
        return False
    return out.exists() and out.stat().st_size > 10_000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验证环境可用")
    args = ap.parse_args()

    browser = find_browser()
    if not browser:
        print("未找到 Edge/Chrome，截图自动化不可用", file=sys.stderr)
        return 2
    print(f"browser: {browser}")
    port, server, store = start_server()
    url = f"http://127.0.0.1:{port}/"
    print(f"server: {url}（mock，预置演示会话）")
    if args.check:
        server.shutdown()
        return 0
    try:
        ok_all = True
        # 深链（?panel=...&tab=...）让 headless 一次运行就能拍到指定面板，
        # 不必模拟点击；这些链接对用户同样可用（见 App.tsx 的深链 effect）。
        shots = [
            ("11-chat.png", url + "?onboarding=skip", 9000),
            ("12-settings-model.png", url + "?onboarding=skip&panel=settings&tab=model", 11000),
            ("13-settings-permissions.png", url + "?onboarding=skip&panel=settings&tab=security", 11000),
            ("14-settings-wiki.png", url + "?onboarding=skip&panel=settings&tab=wiki", 13000),
            ("19-automations.png", url + "?onboarding=skip&panel=settings&tab=automations", 11000),
            ("15-command-palette.png", url + "?onboarding=skip&panel=palette", 10000),
        ]
        for entry in shots:
            name, target = entry[0], entry[1]
            wait_ms = entry[2] if len(entry) > 2 else 9000
            out = OUT_DIR / name
            ok = shoot(browser, target, out, wait_ms=wait_ms)
            print(("PASS" if ok else "FAIL"), name)
            ok_all = ok_all and ok
        # 空状态图需要另一个全新 home（无会话）；单独起服务太重，保留旧图
    finally:
        server.shutdown()
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
