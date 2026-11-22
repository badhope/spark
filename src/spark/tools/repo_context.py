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


def _commands_for(root: Path, manifests: set[str]) -> dict[str, list[str]]:
    commands: dict[str, list[str]] = {"test": [], "build": [], "lint": [], "dev": []}

    # --- Python ---
    if manifests & {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"}:
        if (root / "uv.lock").exists():
            commands["test"].append("uv run pytest")
        elif (root / "poetry.lock").exists():
            commands["test"].append("poetry run pytest")
        else:
            commands["test"].append("python -m pytest")
        if (root / "ruff.toml").exists() or (root / ".ruff.toml").exists() or "ruff" in (
            (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
            if (root / "pyproject.toml").exists()
            else ""
        ):
            commands["lint"].append("ruff check .")
        if (root / "mypy.ini").exists() or (root / "pyproject.toml").exists():
            commands["lint"].append("python -m mypy .")
        if (root / "manage.py").exists():
            commands["dev"].append("python manage.py runserver")

    # --- Node ---
    pkg = _read_json(root / "package.json") if (root / "package.json").exists() else None
    if pkg:
        pm = _node_package_manager(root)
        raw_scripts = pkg.get("scripts")
        scripts: dict[str, Any] = raw_scripts if isinstance(raw_scripts, dict) else {}
        for label in ("test", "build", "lint", "dev", "start"):
            if label in scripts:
                target = "dev" if label in {"dev", "start"} else label
                commands[target].append(f"{pm} run {label}")
        if not commands["test"]:
            commands["test"].append(f"{pm} test")

    # --- Go / Rust / JVM ---
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

    # --- Makefile targets (only a few well-known ones) ---
    makefile = root / "Makefile"
    if makefile.exists():
        try:
            text = makefile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for target in ("test", "build", "lint", "dev"):
            if any(line.startswith(target + ":") for line in text.splitlines()):
                commands[target].append(f"make {target}")

    return {k: v for k, v in commands.items() if v}


def repo_context(sandbox: WorkdirSandbox, args: RepoContextArgs) -> ToolResult:
    try:
        root = sandbox.resolve(args.path)
    except (SandboxPolicyError, Exception) as exc:  # noqa: BLE001 - policy errors vary
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not root.exists() or not root.is_dir():
        return ToolResult(ok=False, payload={"error": f"Directory not found: {args.path}"})

    max_files = max(100, min(int(args.max_files or 4000), 20000))
    started = time.monotonic()
    lang_counts: dict[str, int] = {}
    ext_counts: dict[str, int] = {}
    top_level: list[str] = []
    entrypoints: list[str] = []
    manifests: set[str] = set()
    scanned = 0
    truncated = False

    try:
        top_entries = sorted(os.listdir(root))
    except OSError as exc:
        return ToolResult(ok=False, payload={"error": f"cannot list directory: {exc}"})
    for name in top_entries:
        child = root / name
        if name.startswith("."):
            continue
        if child.is_dir():
            top_level.append(name + "/")
        else:
            top_level.append(name)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        # Prune before descending so huge vendor trees never enter the walk.
        kept: list[str] = []
        for d in dirnames:
            if d.startswith(".") or d in _SKIP_DIRS:
                continue
            child = Path(dirpath) / d
            try:
                if not sandbox.is_path_allowed(child):
                    continue
            except Exception:  # noqa: BLE001 - a policy object without the helper
                pass
            kept.append(d)
        dirnames[:] = kept

        if time.monotonic() - started > 6.0:
            truncated = True
            break

        for filename in filenames:
            if scanned >= max_files:
                truncated = True
                break
            path = Path(dirpath) / filename
            if filename.startswith("."):
                if filename not in _MANIFESTS:
                    continue
            try:
                if not sandbox.is_path_allowed(path):
                    continue
            except Exception:  # noqa: BLE001
                pass
            scanned += 1
            ext = path.suffix.lower()
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
            lang = _LANG_BY_EXT.get(ext)
            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
            if filename in _MANIFESTS:
                manifests.add(filename)
            if filename in _ENTRY_NAMES and len(entrypoints) < 30:
                entrypoints.append(os.path.normpath(os.path.join(rel_dir, filename)))
        if truncated and scanned >= max_files:
            break

    languages = sorted(lang_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    package_manager = ""
    if manifests & {"package.json"}:
        package_manager = _node_package_manager(root)
    elif "uv.lock" in manifests:
        package_manager = "uv"
    elif "poetry.lock" in manifests:
        package_manager = "poetry"
    elif "Pipfile" in manifests:
        package_manager = "pipenv"
    elif manifests & {"pyproject.toml", "requirements.txt"}:
        package_manager = "pip"
    elif "go.mod" in manifests:
        package_manager = "go modules"
    elif "Cargo.toml" in manifests:
        package_manager = "cargo"
    elif "pom.xml" in manifests:
        package_manager = "maven"
    elif manifests & {"build.gradle", "build.gradle.kts"}:
        package_manager = "gradle"

    commands = _commands_for(root, manifests)
    branch = _run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    status = _run_git(root, "status", "--porcelain")
    dirty_all = [line for line in status.splitlines() if line.strip()]
    dirty = dirty_all[:30]
    dirty_count = len(dirty_all)
    recent = _run_git(root, "log", "-5", "--pretty=format:%h %s")

    data: dict[str, Any] = {
        "root": args.path,
        "languages": [{"name": n, "files": c} for n, c in languages],
        "package_manager": package_manager,
        "manifests": sorted(manifests),
        "commands": commands,
        "top_level": top_level[:40],
        "entrypoints": entrypoints,
        "git": {
            "branch": branch,
            "dirty_files": dirty,
            "dirty_count": dirty_count,
            "recent_commits": [line for line in recent.splitlines() if line.strip()],
        },
        "scanned_files": scanned,
        "truncated": truncated,
    }

    lines: list[str] = [f"# 项目上下文: {args.path}", ""]
    if languages:
        lines.append("- 语言: " + ", ".join(f"{n} ({c})" for n, c in languages))
    lines.append(f"- 包管理器: {package_manager or '未识别'}")
    if data["manifests"]:
        lines.append("- 关键清单: " + ", ".join(data["manifests"][:12]))
    if commands:
        lines.append("")
        lines.append("## 可用命令")
        for label, values in commands.items():
            lines.append(f"- {label}: " + "; ".join(f"`{v}`" for v in values))
    if top_level:
        lines.append("")
        lines.append("## 顶层结构")
        lines.append("  ".join(top_level[:30]))
    if entrypoints:
        lines.append("")
        lines.append("## 入口候选")
        for p in entrypoints[:15]:
            lines.append(f"- {p}")
    if branch or dirty:
        lines.append("")
        lines.append("## Git")
        lines.append(f"- 分支: {branch or '未知'}")
        if dirty:
            lines.append(f"- 未提交: {dirty_count} 项（前 10）")
            for row in dirty[:10]:
                lines.append(f"  {row}")
        else:
            lines.append("- 工作区干净")
    recent_commits = data["git"]["recent_commits"]
    if recent_commits:
        lines.append("- 最近提交:")
        for row in recent_commits:
            lines.append(f"  {row}")
    if truncated:
        lines.append("")
        lines.append(f"> 扫描达到上限（{scanned} 个文件），结论是抽样而非全量。")

    return ToolResult(
        ok=True,
        payload={
            "content": "\n".join(lines),
            "data": data,
            "scanned_files": scanned,
            "truncated": truncated,
        },
    )
