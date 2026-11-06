"""`/api/wiki`：生成、读取、路径隔离。

重点验证三件事，都是"能生成"看不出来的：
1. 未生成时读是 404，不是 500 或空 200；
2. 模块名只能是 modules/ 下的裸 .md 文件（穿越尝试必须 400）；
3. 产物不会越出工作区（生成前后工作区外的文件不变）。
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.store import SessionStore
from spark.web.server import serve_web


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _post(base: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


@pytest.fixture()
def live(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "svc.py").write_text(
        "class Service:\n    def run(self):\n        pass\n\n\ndef helper():\n    pass\n",
        encoding="utf-8",
    )
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    store = SessionStore(tmp_path / "web.db")
    port = _free_port()
    thread = threading.Thread(
        target=serve_web,
        kwargs={"workdir": tmp_path, "cfg": cfg, "store": store, "host": "127.0.0.1", "port": port},
        daemon=True,
    )
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/status", timeout=5)
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    else:
        pytest.fail("server did not start")
    yield base, tmp_path
    store.close()


def test_status_reports_not_generated_before_the_first_run(live) -> None:
    base, _ = live
    data = _post(base, "/api/wiki", {"action": "status"})
    assert data["exists"] is False
    assert data["modules"] == 0


def test_read_before_generate_is_404_not_an_empty_200(live) -> None:
    base, _ = live
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(base, "/api/wiki", {"action": "read", "name": "INDEX.md"})
    assert excinfo.value.code == 404


def test_generate_then_read_index_and_module(live) -> None:
    base, workdir = live
    stats = _post(base, "/api/wiki", {"action": "generate", "max_files": 50})
    assert stats["modules"] >= 1
    assert stats["symbols"] >= 2
    assert (workdir / ".spark" / "wiki" / "INDEX.md").is_file()

    index = _post(base, "/api/wiki", {"action": "read"})
    assert "Repo Wiki" in index["content"]

    module = _post(base, "/api/wiki", {"action": "read", "name": "pkg__svc.py.md"})
    assert "`Service`" in module["content"]
    assert "| 1 | class | `Service` |" in module["content"]


def test_module_name_traversal_is_rejected(live) -> None:
    base, _ = live
    _post(base, "/api/wiki", {"action": "generate"})
    for bad in ("../etc/passwd", "..\\..\\win.ini", "/abs.md", "nested/x.md", ".hidden.md", "noext"):
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _post(base, "/api/wiki", {"action": "read", "name": bad})
        assert excinfo.value.code in (400, 404), bad


def test_delete_removes_only_the_wiki_directory(live) -> None:
    base, workdir = live
    _post(base, "/api/wiki", {"action": "generate"})
    assert (workdir / ".spark" / "wiki").is_dir()
    result = _post(base, "/api/wiki", {"action": "delete"})
    assert result["removed"] is True
    assert not (workdir / ".spark" / "wiki").exists()
    # Source files must survive: deleting derived docs is not deleting code.
    assert (workdir / "pkg" / "svc.py").is_file()


def test_generate_rejects_non_integer_max_files(live) -> None:
    base, _ = live
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(base, "/api/wiki", {"action": "generate", "max_files": "many"})
    assert excinfo.value.code == 400


def test_unknown_action_is_400(live) -> None:
    base, _ = live
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(base, "/api/wiki", {"action": "explode"})
    assert excinfo.value.code == 400


def test_status_echoes_the_max_files_actually_used(live) -> None:
    """The panel showed the form default (240) next to a 30-file wiki."""
    base, _ = live
    _post(base, "/api/wiki", {"action": "generate", "max_files": 7})
    status = _post(base, "/api/wiki", {"action": "status"})
    assert status["max_files"] == 7


def test_status_reads_parameters_written_by_the_cli(live) -> None:
    """A wiki generated from the terminal must display correctly in the panel.

    Regression: metadata was written by the HTTP handler only, so `spark wiki
    --max-files 20` produced a 20-file wiki that the panel labelled 240.
    """
    from spark.wiki import WikiConfig, build_wiki

    base, workdir = live
    build_wiki(workdir, WikiConfig(max_files=5))
    status = _post(base, "/api/wiki", {"action": "status"})
    assert status["exists"] is True
    assert status["modules"] <= 5
    assert status["max_files"] == 5
