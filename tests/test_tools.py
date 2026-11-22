from pathlib import Path

from spark.config import SparkConfig
from spark.sandbox import WorkdirSandbox
from spark.tools.fs import (
    ApplyPatchArgs,
    ListDirArgs,
    ReadFileArgs,
    WriteFileArgs,
    apply_patch,
    list_dir,
    read_file,
    write_file,
)
from spark.tools.shell import RunShellArgs, run_shell


def test_read_write_list(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    result = write_file(box, WriteFileArgs(path="a.txt", content="hello\nworld\n"))
    assert result.ok
    listed = list_dir(box, ListDirArgs(path="."))
    assert listed.ok
    names = [e["name"] for e in listed.payload["entries"]]
    assert "a.txt" in names
    read = read_file(box, ReadFileArgs(path="a.txt", offset=2, limit=1))
    assert read.payload["content"] == "world"


def test_path_traversal_rejected(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    result = read_file(box, ReadFileArgs(path="../outside.txt"))
    assert result.ok is False
    assert "escapes" in result.payload["error"]


def test_apply_patch_unique(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    write_file(box, WriteFileArgs(path="b.txt", content="foo bar foo"))
    ok = apply_patch(box, ApplyPatchArgs(path="b.txt", old_text="bar", new_text="baz"))
    assert ok.ok
    assert (tmp_path / "b.txt").read_text() == "foo baz foo"


def test_apply_patch_zero_and_many(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    write_file(box, WriteFileArgs(path="c.txt", content="aa aa"))
    zero = apply_patch(box, ApplyPatchArgs(path="c.txt", old_text="zz", new_text="yy"))
    assert zero.ok is False
    many = apply_patch(box, ApplyPatchArgs(path="c.txt", old_text="aa", new_text="bb"))
    assert many.ok is False
    assert (tmp_path / "c.txt").read_text() == "aa aa"


def test_list_dir_hides_protected_entries(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("remote=secret")
    (tmp_path / ".spark").mkdir()
    (tmp_path / ".spark" / "settings.yaml").write_text("token=secret")
    (tmp_path / ".env").write_text("API_KEY=secret")
    (tmp_path / "visible.txt").write_text("ok")

    cfg = SparkConfig()
    cfg.agent.protected_paths = [".git", ".spark", ".env"]
    box = WorkdirSandbox(tmp_path, cfg)
    listed = list_dir(box, ListDirArgs(path="."))

    assert listed.ok
    assert [e["name"] for e in listed.payload["entries"]] == ["visible.txt"]


def test_shell_timeout(tmp_path: Path) -> None:
    import os
    import sys

    box = WorkdirSandbox(tmp_path)
    if os.name == "nt":
        command = f'"{sys.executable}" -c "import time; time.sleep(2)"'
    else:
        # POSIX：裸 "python3" 走 PATH 解析；系统解释器常在 /usr/bin，
        # 绝对路径会触发 workspace 沙箱的保护路径拦截（设计行为）。
        command = 'python3 -c "import time; time.sleep(2)"'
    result = run_shell(
        box, RunShellArgs(command=command), timeout_sec=1, max_output_chars=100
    )
    assert result.ok is False
    assert result.payload["error"] == "timeout"
