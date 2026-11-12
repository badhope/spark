"""内置终端（PTY）测试：多 tab 管理、读写回显、目录绑定、退出标记。

Windows 使用 pywinpty（ConPTY），Linux/macOS 使用 ptyprocess；对应后端缺失
时整套测试自动跳过。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.importorskip("winpty")
else:
    pytest.importorskip("ptyprocess")

PtyManager = pytest.importorskip("spark.pty").PtyManager


def _collect(sess, loop, seconds=2.5):
    """起 reader，向 PTY 写入命令，返回期间所有输出文本。"""
    q: asyncio.Queue[str] = asyncio.Queue()
    sess.start_reader(loop, q)
    out: list[str] = []
    newline = "\r\n" if sys.platform == "win32" else "\n"
    pwd_cmd = "cd" if sys.platform == "win32" else "pwd"

    async def drain(sec):
        while True:
            try:
                out.append(await asyncio.wait_for(q.get(), timeout=sec))
            except TimeoutError:
                break

    async def main():
        await asyncio.sleep(1.0)
        sess.write(f"echo SPARK_PTY_ECHO{newline}")
        await asyncio.sleep(1.0)
        sess.write(f"{pwd_cmd}{newline}")
        await asyncio.sleep(0.8)
        sess.write(f"exit{newline}")
        await asyncio.sleep(0.8)
        await drain(0.4)
        return "".join(out)

    return loop.run_until_complete(main())


def test_pty_read_write_and_cwd():
    tmp = Path(tempfile.mkdtemp())
    m = PtyManager()
    sess = m.get_or_create("s1", "tab_a", tmp)
    try:
        assert sess.alive
        loop = asyncio.new_event_loop()
        try:
            text = _collect(sess, loop)
            assert "SPARK_PTY_ECHO" in text          # 命令回显
            assert str(tmp) in text                   # pwd = 工作目录
            assert "\x00__SPARK_PTY_EXIT__\x00" in text  # 退出标记
        finally:
            loop.close()
    finally:
        m.close_all()


def test_pty_multi_tab_isolated():
    tmp1 = Path(tempfile.mkdtemp())
    tmp2 = Path(tempfile.mkdtemp())
    m = PtyManager()
    s1 = m.get_or_create("s1", "t1", tmp1)
    s2 = m.get_or_create("s1", "t2", tmp2)
    try:
        assert s1.tab_id == "t1" and s2.tab_id == "t2"
        assert s1.cwd == tmp1.resolve() and s2.cwd == tmp2.resolve()
        # 同 (sid, tab) 复用
        assert m.get_or_create("s1", "t1", tmp1) is s1
    finally:
        m.close_all()


def test_pty_session_isolated_by_sid():
    """同一 tab_id 在不同会话下互不复用（切会话必须换 cwd，不能留在旧目录）。"""
    tmp1 = Path(tempfile.mkdtemp())
    tmp2 = Path(tempfile.mkdtemp())
    m = PtyManager()
    s1 = m.get_or_create("sA", "tab1", tmp1)
    s2 = m.get_or_create("sB", "tab1", tmp2)
    try:
        assert s1 is not s2
        assert s1.cwd == tmp1.resolve()
        assert s2.cwd == tmp2.resolve()  # 新会话拿到自己的目录
        # 切回 sA 仍是原来的 shell（持久）
        assert m.get_or_create("sA", "tab1", tmp1) is s1
    finally:
        m.close_all()


def test_pty_write_to_closed_is_noop():
    m = PtyManager()
    _sess = m.get_or_create("sx", "gone", Path(tempfile.mkdtemp()))
    m.close_all()
    assert m.write("sx", "gone", "ls\n") is False  # 已关闭 → no-op 而非抛错
