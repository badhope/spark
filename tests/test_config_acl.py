"""配置文件权限收紧：POSIX chmod 600；Windows 用 ACL 只保留当前用户。

Windows 上 os.chmod(0o600) 只拨只读位、不限制其他用户读取——令牌与 API Key
存于 config.toml，必须用 icacls 收紧 ACL 才算真正保护。

判定标准（安全目标导向）：不同 Windows 环境在移除继承后残留的系统条目数不同
（本机 vs GitHub runner 实测 2 vs 4 条：SYSTEM / Administrators / OWNER RIGHTS 等），
只要一般用户组读不到即达标——精确计数会把 runner 环境误判为不安全。
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

#: 一般用户组（任一普通用户可读）——收紧后必须全部不在 ACL 中
_WIDE_GROUPS = (
    "BUILTIN\\Users",
    "Everyone",
    "NT AUTHORITY\\Authenticated Users",
    "NT AUTHORITY\\INTERACTIVE",
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL 行为")
def test_save_config_hardens_acl(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    from spark.config import SparkConfig, default_config_path, save_config

    cfg = SparkConfig()
    cfg.provider.api_key = "sk-test-1234567890"
    save_config(cfg)
    f = default_config_path()
    out = subprocess.run(
        ["icacls", str(f)], capture_output=True, text=True,
        encoding="mbcs", errors="replace", timeout=30
    ).stdout or ""
    # 安全目标：宽泛用户组必须全部不可见
    for group in _WIDE_GROUPS:
        assert group not in out, f"{group} 仍可访问：{out!r}"
    # 继承已移除（无 (I) 标记），且当前用户仍是授权主体
    aces = [ln for ln in out.splitlines() if ":(" in ln]
    assert aces, f"icacls 未返回任何 ACE：{out!r}"
    assert not any("(I)" in ln for ln in aces), f"继承未移除：{out!r}"
    who = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        encoding="mbcs",
        errors="replace",
        timeout=30,
    ).stdout or ""
    m = re.search(r"S-1-\d[\d-]*\d", who)
    assert m, f"whoami 未返回 SID：{who!r}"
    assert m.group(0) in out or _user_name() in aces[0]


def _user_name() -> str:
    import getpass

    return getpass.getuser()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL 行为")
def test_doctor_perm_check_on_windows(tmp_path, monkeypatch) -> None:
    """doctor 的权限自检在 Windows 上应反映 ACL 状态（收紧后为 True）。"""
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    from spark.cli import _perm_ok
    from spark.config import SparkConfig, save_config

    save_config(SparkConfig())
    assert _perm_ok() is True
