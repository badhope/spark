"""One-shot repository orientation for the model.

Reading a new repository usually costs a dozen ``list_dir`` / ``read_file``
round trips before the model knows where anything is.  This tool returns the
small, stable facts that matter first: languages, package manager, the commands
the project actually exposes, important manifests, top-level layout and git
state.  It is deliberately read-only and bounded so it can be a default-safe
tool.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from spark.models import ToolResult
from spark.sandbox import SandboxPolicyError, WorkdirSandbox


class RepoContextArgs(BaseModel):
    path: str = "."
    max_files: int = 4000


_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".spark",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "bower_components",
    "dist",
    "build",
    "out",
    "target",
    "coverage",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "vendor",
    ".idea",
    ".vscode",
}

_LANG_BY_EXT = {
    ".py": "Python",
    ".pyi": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".c": "C",
    ".h": "C/C++",
    ".cc": "C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "CSS",
    ".md": "Markdown",
}

_MANIFESTS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "tsconfig.json",
    "vite.config.ts",
    "vite.config.js",
    "next.config.js",
    "next.config.mjs",
    "nuxt.config.ts",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "Makefile",
    "CMakeLists.txt",
    "composer.json",
    "Gemfile",
    "mix.exs",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
)

_ENTRY_NAMES = {
    "main.py",
    "app.py",
    "cli.py",
    "server.py",
    "manage.py",
    "wsgi.py",
    "asgi.py",
    "index.ts",
    "index.tsx",
    "main.ts",
    "main.tsx",
    "app.ts",
    "server.ts",
    "main.go",
    "main.rs",
    "lib.rs",
    "Main.java",
    "Program.cs",
}


def _run_git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _read_toml(path: Path) -> dict[str, Any] | None:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python < 3.11
        return None
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return None


def _node_package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "bun.lockb").exists():
        return "bun"
    return "npm"


def _python_commands(root: Path, manifests: set[str], commands: dict[str, list[str]]) -> None:
    if not manifests & {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"}:
        return
    if (root / "uv.lock").exists():
        commands["test"].append("uv run pytest")
    elif (root / "poetry.lock").exists():
        commands["test"].append("poetry run pytest")
    else:
        commands["test"].append("python -m pytest")
    pyproject = root / "pyproject.toml"
    pyproject_text = (
        pyproject.read_text(encoding="utf-8", errors="replace")
        if pyproject.exists()
        else ""
    )
    if (root / "ruff.toml").exists() or (root / ".ruff.toml").exists() or "ruff" in pyproject_text:
        commands["lint"].append("ruff check .")
    if (root / "mypy.ini").exists() or pyproject.exists():
        commands["lint"].append("python -m mypy .")
    if (root / "manage.py").exists():
        commands["dev"].append("python manage.py runserver")


def _node_commands(root: Path, commands: dict[str, list[str]]) -> None:
    pkg = _read_json(root / "package.json") if (root / "package.json").exists() else None
    if not pkg:
        return
    pm = _node_package_manager(root)
    raw_scripts = pkg.get("scripts")
    scripts: dict[str, Any] = raw_scripts if isinstance(raw_scripts, dict) else {}
    for label in ("test", "build", "lint", "dev", "start"):
        if label in scripts:
            target = "dev" if label in {"dev", "start"} else label
            commands[target].append(f"{pm} run {label}")
    if not commands["test"]:
        commands["test"].append(f"{pm} test")


def _native_commands(root: Path, manifests: set[str], commands: dict[str, list[str]]) -> None:
    if "go.mod" in manifests:
        commands["test"].append("go test ./...")
        commands["build"].append("go build ./...")
    if "Cargo.toml" in manifests:
        commands["test"].append("cargo test")
        commands["build"].append("cargo build")
    if "pom.xml" in manifests:
        commands["test"].append("mvn test")
        commands["build"].append("mvn package")
    if manifests & {"build.gradle", "build.gradle.kts"}:
        gradle = "./gradlew" if (root / "gradlew").exists() else "gradle"
        commands["test"].append(f"{gradle} test")
        commands["build"].append(f"{gradle} build")


def _make_commands(root: Path, commands: dict[str, list[str]]) -> None:
    makefile = root / "Makefile"
    if not makefile.exists():
        return
    try:
        text = makefile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    lines = text.splitlines()
    for target in ("test", "build", "lint", "dev"):
        if any(line.startswith(target + ":") for line in lines):
            commands[target].append(f"make {target}")


def _commands_for(root: Path, manifests: set[str]) -> dict[str, list[str]]:
    commands: dict[str, list[str]] = {"test": [], "build": [], "lint": [], "dev": []}
    _python_commands(root, manifests, commands)
    _node_commands(root, commands)
    _native_commands(root, manifests, commands)
    _make_commands(root, commands)
    return {k: v for k, v in commands.items() if v}

@dataclass(frozen=True)
class _ScanResult:
    top_level: list[str]
    entrypoints: list[str]
    manifests: set[str]
    languages: list[tuple[str, int]]
    scanned: int
    truncated: bool


def _top_level_entries(root: Path) -> list[str]:
    try:
        top_entries = sorted(os.listdir(root))
    except OSError as exc:
        raise OSError(f"cannot list directory: {exc}") from exc
    entries: list[str] = []
    for name in top_entries:
        child = root / name
        if name.startswith("."):
            continue
        entries.append(name + "/" if child.is_dir() else name)
    return entries


def _scan_repository(root: Path, sandbox: WorkdirSandbox, max_files: int) -> _ScanResult:
    started = time.monotonic()
    lang_counts: dict[str, int] = {}
    top_level = _top_level_entries(root)
    entrypoints: list[str] = []
    manifests: set[str] = set()
    scanned = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        # Prune before descending so huge vendor trees never enter the walk.
        kept: list[str] = []
        for directory in dirnames:
            if directory.startswith(".") or directory in _SKIP_DIRS:
                continue
            child = Path(dirpath) / directory
            try:
                if not sandbox.is_path_allowed(child):
                    continue
            except Exception:  # noqa: BLE001 - a policy object without the helper
                pass
            kept.append(directory)
        dirnames[:] = kept

        if time.monotonic() - started > 6.0:
            truncated = True
            break

        for filename in filenames:
            if scanned >= max_files:
                truncated = True
                break
            path = Path(dirpath) / filename
            if filename.startswith(".") and filename not in _MANIFESTS:
                continue
            try:
                if not sandbox.is_path_allowed(path):
                    continue
            except Exception:  # noqa: BLE001
                pass
            scanned += 1
            ext = path.suffix.lower()
            lang = _LANG_BY_EXT.get(ext)
            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
            if filename in _MANIFESTS:
                manifests.add(filename)
            if filename in _ENTRY_NAMES and len(entrypoints) < 30:
                entrypoints.append((Path(rel_dir) / filename).as_posix())
        if truncated and scanned >= max_files:
            break

    languages = sorted(lang_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    return _ScanResult(
        top_level=top_level,
        entrypoints=entrypoints,
        manifests=manifests,
        languages=languages,
        scanned=scanned,
        truncated=truncated,
    )


def _package_manager(root: Path, manifests: set[str]) -> str:
    if "package.json" in manifests:
        return _node_package_manager(root)
    if "uv.lock" in manifests:
        return "uv"
    if "poetry.lock" in manifests:
        return "poetry"
    if "Pipfile" in manifests:
        return "pipenv"
    if manifests & {"pyproject.toml", "requirements.txt"}:
        return "pip"
    if "go.mod" in manifests:
        return "go modules"
    if "Cargo.toml" in manifests:
        return "cargo"
    if "pom.xml" in manifests:
        return "maven"
    if manifests & {"build.gradle", "build.gradle.kts"}:
        return "gradle"
    return ""


def _git_snapshot(root: Path) -> dict[str, Any]:
    branch = _run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    status = _run_git(root, "status", "--porcelain")
    dirty_all = [line for line in status.splitlines() if line.strip()]
    recent = _run_git(root, "log", "-5", "--pretty=format:%h %s")
    return {
        "branch": branch,
        "dirty_files": dirty_all[:30],
        "dirty_count": len(dirty_all),
        "recent_commits": [line for line in recent.splitlines() if line.strip()],
    }


def _render_repo_context(path_label: str, data: dict[str, Any]) -> str:
    languages = [(row["name"], row["files"]) for row in data["languages"]]
    lines: list[str] = [f"# 项目上下文: {path_label}", ""]
    if languages:
        lines.append("- 语言: " + ", ".join(f"{name} ({count})" for name, count in languages))
    lines.append(f"- 包管理器: {data['package_manager'] or '未识别'}")
    if data["manifests"]:
        lines.append("- 关键清单: " + ", ".join(data["manifests"][:12]))
    if data["commands"]:
        lines.append("")
        lines.append("## 可用命令")
        for label, values in data["commands"].items():
            lines.append(f"- {label}: " + "; ".join(f"`{value}`" for value in values))
    if data["top_level"]:
        lines.append("")
        lines.append("## 顶层结构")
        lines.append("  ".join(data["top_level"][:30]))
    if data["entrypoints"]:
        lines.append("")
        lines.append("## 入口候选")
        for entrypoint in data["entrypoints"][:15]:
            lines.append(f"- {entrypoint}")
    git = data["git"]
    if git["branch"] or git["dirty_files"]:
        lines.append("")
        lines.append("## Git")
        lines.append(f"- 分支: {git['branch'] or '未知'}")
        if git["dirty_files"]:
            lines.append(f"- 未提交: {git['dirty_count']} 项（前 10）")
            lines.extend(f"  {row}" for row in git["dirty_files"][:10])
        else:
            lines.append("- 工作区干净")
    if git["recent_commits"]:
        lines.append("- 最近提交:")
        lines.extend(f"  {row}" for row in git["recent_commits"])
    if data["truncated"]:
        lines.append("")
        lines.append(f"> 扫描达到上限（{data['scanned_files']} 个文件），结论是抽样而非全量。")
    return "\n".join(lines)


def repo_context(sandbox: WorkdirSandbox, args: RepoContextArgs) -> ToolResult:
    try:
        root = sandbox.resolve(args.path)
    except (SandboxPolicyError, Exception) as exc:  # noqa: BLE001 - policy errors vary
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not root.exists() or not root.is_dir():
        return ToolResult(ok=False, payload={"error": f"Directory not found: {args.path}"})

    max_files = max(100, min(int(args.max_files or 4000), 20000))
    try:
        scan = _scan_repository(root, sandbox, max_files)
    except OSError as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})

    package_manager = _package_manager(root, scan.manifests)
    commands = _commands_for(root, scan.manifests)
    data: dict[str, Any] = {
        "root": args.path,
        "languages": [{"name": name, "files": count} for name, count in scan.languages],
        "package_manager": package_manager,
        "manifests": sorted(scan.manifests),
        "commands": commands,
        "top_level": scan.top_level[:40],
        "entrypoints": scan.entrypoints,
        "git": _git_snapshot(root),
        "scanned_files": scan.scanned,
        "truncated": scan.truncated,
    }
    content = _render_repo_context(args.path, data)
    return ToolResult(
        ok=True,
        payload={
            "content": content,
            "data": data,
            "scanned_files": scan.scanned,
            "truncated": scan.truncated,
        },
    )
