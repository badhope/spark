"""自更新：查询最新 Release、校验 SHA256、原子替换当前安装。

设计取舍（与 opencode ``auto/notify`` 三态对齐）：

* 默认 **notify**：`spark --version` / `spark doctor` 只提示有新版本，不自动改盘；
* ``spark update`` 显式执行：下载 → 校验 → 替换；
* ``[updater] mode = "off"`` 可彻底关掉版本检查（离线/内网环境）。

安全边界：

* 只接受 https 的 GitHub/GitCode release 资产地址；
* 没有 ``SHA256SUMS`` 或哈希不匹配一律中止（不装未校验的二进制）；
* 替换前把当前可执行文件改名为 ``*.old``，失败可回滚；
* 从不自动提权（不 sudo、不 UAC 弹窗），需要写系统目录时明确报错。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from spark import __version__

RELEASE_API = "https://api.github.com/repos/badhope/spark/releases/latest"
CANARY_API = "https://api.github.com/repos/badhope/spark/releases?per_page=10"
DEFAULT_TIMEOUT = 20.0


class UpdateError(Exception):
    """更新过程中的可预期失败（网络/校验/权限）。"""


@dataclass
class ReleaseInfo:
    version: str
    tag: str
    notes: str
    assets: list[dict]


def _platform_asset_name() -> str:
    if sys.platform == "win32":
        return "spark-windows-x86_64.zip"
    if sys.platform == "darwin":
        return "spark-macos-arm64.tar.gz"
    return "spark-linux-x86_64.tar.gz"


def parse_version(text: str) -> tuple[int, ...]:
    """把 ``v0.9.12`` / ``0.9.12-beta.1`` 解析成可比较的元组。

    预发布后缀整体丢弃（``0.9.12-beta.1`` == ``0.9.12``）：GitHub Release 的
    tag 命名可能带后缀，把它当额外版本段会让 ``0.9.12`` < ``0.9.12-beta.1``。
    """
    cleaned = text.strip().lstrip("vV")
    for sep in ("-", "+"):
        if sep in cleaned:
            cleaned = cleaned.split(sep, 1)[0]
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def _get_json_any(url: str, timeout: float = DEFAULT_TIMEOUT) -> object:
    if not url.startswith("https://"):
        raise UpdateError(f"refusing non-https URL: {url}")
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https enforced above
            return json.loads(resp.read().decode("utf-8"))
    except OSError as exc:
        raise UpdateError(f"无法访问发布源：{exc}") from exc
    except ValueError as exc:
        raise UpdateError("发布源返回的不是合法 JSON") from exc


def _get_json(url: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    data = _get_json_any(url, timeout)
    if not isinstance(data, dict):
        raise UpdateError("发布源返回格式异常")
    return data


def _release_from_payload(data: dict) -> ReleaseInfo:
    if not isinstance(data, dict):
        raise UpdateError("发布源返回格式异常")
    tag = str(data.get("tag_name") or "")
    version = tag.lstrip("vV")
    assets = [
        {
            "name": str(a.get("name") or ""),
            "url": str(a.get("browser_download_url") or ""),
            "size": int(a.get("size") or 0),
        }
        for a in data.get("assets") or []
        if isinstance(a, dict)
    ]
    if not version:
        raise UpdateError("发布信息缺少 tag_name")
    return ReleaseInfo(version=version, tag=tag, notes=str(data.get("body") or ""), assets=assets)


def fetch_latest(api_url: str = RELEASE_API, channel: str = "stable") -> ReleaseInfo:
    """Fetch the newest release for ``stable`` or ``canary``.

    ``stable`` uses GitHub's ``releases/latest``, which excludes prereleases.
    ``canary`` lists releases and takes the first non-draft entry (usually the
    newest prerelease), so opt-in users can dogfood a tagged build.
    """
    if channel == "canary":
        data = _get_json_any(CANARY_API if api_url == RELEASE_API else api_url)
        if not isinstance(data, list):
            raise UpdateError("canary 发布源应返回 release 列表")
        for item in data:
            if isinstance(item, dict) and not item.get("draft"):
                return _release_from_payload(item)
        raise UpdateError("canary 发布源没有可用 release")
    return _release_from_payload(_get_json(api_url))


def select_asset(release: ReleaseInfo, name: str | None = None) -> dict:
    wanted = name or _platform_asset_name()
    for asset in release.assets:
        if asset["name"] == wanted:
            return asset
    available = ", ".join(a["name"] for a in release.assets) or "（无资产）"
    raise UpdateError(f"没有适用于本平台的产物 {wanted}；可用：{available}")


def parse_sha256sums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            out[parts[-1].lstrip("*")] = parts[0].lower()
    return out


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path, timeout: float = 120.0) -> Path:
    if not url.startswith("https://"):
        raise UpdateError(f"refusing non-https download: {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, target.open("wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out)
    except OSError as exc:
        raise UpdateError(f"下载失败：{exc}") from exc
    return target


def verify(target: Path, sums: dict[str, str], asset_name: str) -> None:
    expected = sums.get(asset_name)
    if not expected:
        raise UpdateError(f"SHA256SUMS 里没有 {asset_name}，拒绝安装未校验的产物")
    actual = sha256_file(target)
    if actual != expected:
        raise UpdateError(f"SHA256 不匹配：期望 {expected[:12]}…，实际 {actual[:12]}…")


def current_executable() -> Path:
    """当前 spark 可执行文件（打包版 = exe；pip 安装 = 控制台脚本）。"""
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    if argv0 and argv0.exists() and argv0.name.lower().startswith("spark"):
        try:
            return argv0.resolve()
        except OSError:
            pass
    return Path(sys.executable).resolve()


def _safe_extract(archive: Path, dest: Path) -> None:
    """解压 release 产物，拒绝逃逸出 *dest* 的成员。

    只校验 SHA256 不够：若发布资产本身被替换（或镜像回源被投毒），
    ``extractall`` 会把 ``../../.ssh/authorized_keys`` 直接写出去。
    逐成员校验解析后仍在 dest 内，并拒绝符号链接/硬链接/设备文件。
    """
    import tarfile
    import zipfile

    base = dest.resolve()

    def inside(name: str) -> bool:
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts:
            return False
        try:
            (dest / candidate).resolve().relative_to(base)
        except (ValueError, OSError):
            return False
        return True

    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if not infos:
                raise UpdateError("产物是空压缩包")
            for info in infos:
                if not inside(info.filename):
                    raise UpdateError(f"压缩包包含路径逃逸：{info.filename}")
            zf.extractall(dest)  # nosec B202 - every member name validated above
        return

    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            if member.issym() or member.islnk() or member.isdev():
                raise UpdateError(f"压缩包包含不允许的链接/设备：{member.name}")
            if not inside(member.name):
                raise UpdateError(f"压缩包包含路径逃逸：{member.name}")
        # 成员已逐个校验（无逃逸/无链接/无设备），filter="data" 再兜一层
        tf.extractall(dest, filter="data")  # nosec B202 - validated above


def install_archive(archive: Path, exe: Path) -> Path:
    """解压新版本并原子替换 *exe*；返回备份路径（调用方负责清理或回滚）。"""
    workdir = Path(tempfile.mkdtemp(prefix="spark-update-"))
    _safe_extract(archive, workdir)

    candidates = list(workdir.rglob(exe.name)) or list(workdir.rglob("spark*"))
    candidates = [c for c in candidates if c.is_file() and not c.name.endswith((".md", ".txt"))]
    if not candidates:
        raise UpdateError("压缩包里找不到可执行文件")
    new_exe = max(candidates, key=lambda p: p.stat().st_size)
    backup = exe.with_name(exe.name + ".old")
    try:
        if backup.exists():
            backup.unlink()
        if exe.exists():
            os.replace(exe, backup)
        shutil.copy2(new_exe, exe)
        if sys.platform != "win32":
            exe.chmod(0o755)
    except OSError as exc:
        if backup.exists() and not exe.exists():
            os.replace(backup, exe)
        raise UpdateError(
            f"替换可执行文件失败（{exc}）。若安装在系统目录，请用管理员权限或改用用户目录安装"
        ) from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return backup


def release_page_url(tag: str) -> str:
    return f"https://github.com/badhope/spark/releases/tag/{tag}"


def check_cached(home: Path, ttl_sec: float = 86_400.0) -> str | None:
    """返回可提示的新版本号（无更新/失败/被关闭返回 None）。

    只在 ``doctor`` 这类交互命令里调用；``spark --version`` 保持纯本地，
    冷启动不能被网络拖慢（F-006 已把启动耗时压到 <2s，不能自己破功）。
    结果按 24h 缓存到 ``$SPARK_HOME/update-check.json``，离线环境静默失败。
    """
    import time

    cache = home / "update-check.json"
    try:
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            if time.time() - float(data.get("checked_at") or 0) < ttl_sec:
                latest = str(data.get("latest") or "")
                return latest if latest and is_newer(latest) else None
    except (OSError, ValueError, TypeError):
        pass
    try:
        release = fetch_latest()
    except UpdateError:
        return None
    try:
        home.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({"checked_at": time.time(), "latest": release.version}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return release.version if is_newer(release.version) else None
