"""Built-in catalog and downloader for small local GGUF models.

The app ships no multi-hundred-megabyte weights inside the installer.  The
settings page shows a curated, versioned catalog and downloads the chosen
GGUF into ``$SPARK_HOME/models``; ``llama_cpp`` then loads that file directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from spark.config import default_home


@dataclass(frozen=True)
class LocalModelSpec:
    id: str
    name: str
    params: str
    filename: str
    size_bytes: int
    context: int
    license: str
    languages: str
    summary: str
    strengths: tuple[str, ...]
    tool_parser: str
    chat_format: str
    urls: tuple[str, ...]
    recommended: bool = False
    note: str = ""

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "params": self.params,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "size_mb": round(self.size_bytes / (1024 * 1024)),
            "context": self.context,
            "license": self.license,
            "languages": self.languages,
            "summary": self.summary,
            "strengths": list(self.strengths),
            "tool_parser": self.tool_parser,
            "chat_format": self.chat_format,
            "recommended": self.recommended,
            "note": self.note,
        }


#: Curated as of 2026-11.  Only models with a public, direct GGUF artifact are
#: listed; a dead download link is worse than not advertising the model.
CATALOG: tuple[LocalModelSpec, ...] = (
    LocalModelSpec(
        id="minicpm5-1b",
        name="MiniCPM5-1B",
        params="1.08B",
        filename="MiniCPM5-1B-Q4_K_M.gguf",
        size_bytes=688_065_920,
        context=131_072,
        license="Apache-2.0",
        languages="中文 / English",
        summary="1B 级原生 128K 上下文，同一份权重支持 Think / No-Think，官方面向本地助手与工具调用。",
        strengths=("128K 上下文", "中文优先", "XML 工具调用", "Apache-2.0"),
        tool_parser="minicpm5",
        chat_format="default",
        urls=(
            "https://modelscope.cn/models/OpenBMB/MiniCPM5-1B-GGUF/resolve/master/MiniCPM5-1B-Q4_K_M.gguf",
            "https://huggingface.co/openbmb/MiniCPM5-1B-GGUF/resolve/main/MiniCPM5-1B-Q4_K_M.gguf?download=true",
        ),
        recommended=True,
        note="中文场景首选；默认以 16K 上下文启动，可用 SPARK_LOCAL_CTX 调高。",
    ),
    LocalModelSpec(
        id="botpsy-0-8b",
        name="BotPsy-0.8B",
        params="0.8B",
        filename="botpsy-0.8b-q4_k_m-imat.gguf",
        size_bytes=529_296_960,
        context=32_768,
        license="Apache-2.0",
        languages="English 优先 / 中文可用",
        summary="2026-11 新出的端侧 Agent 小模型，基于 Qwen3.5-0.8B；厂商称其工具调用强于 1.2B 级对手。",
        strengths=("工具调用", "多步 Agent", "0.5GB 体积", "Apache-2.0"),
        tool_parser="qwen3.5",
        chat_format="default",
        urls=(
            "https://huggingface.co/qvac/BotPsy-0.8B-GGUFs/resolve/main/botpsy-0.8b-q4_k_m-imat.gguf?download=true",
        ),
        note="英文任务更强；中文和长上下文不如 MiniCPM5-1B，作为实验档提供。",
    ),
)


def get_spec(model_id: str) -> LocalModelSpec | None:
    for spec in CATALOG:
        if spec.id == model_id:
            return spec
    return None


def models_dir() -> Path:
    override = os.environ.get("SPARK_MODEL_DIR", "").strip()
    root = Path(override).expanduser() if override else default_home() / "models"
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class DownloadJob:
    model_id: str
    status: str = "idle"  # downloading | verifying | ready | failed
    bytes_done: int = 0
    bytes_total: int = 0
    error: str = ""
    started_at: float = field(default_factory=time.time)
    speed_bps: float = 0.0


class LocalModelManager:
    """Download state is process-local; installed files are durable on disk."""

    def __init__(self) -> None:
        self._jobs: dict[str, DownloadJob] = {}
        self._lock = threading.Lock()

    def _path(self, spec: LocalModelSpec) -> Path:
        return models_dir() / spec.filename

    def installed(self, spec: LocalModelSpec) -> bool:
        path = self._path(spec)
        try:
            if path.stat().st_size != spec.size_bytes:
                return False
            with path.open("rb") as fh:
                return fh.read(4) == b"GGUF"
        except OSError:
            return False

    def snapshot(self, active_path: str | None = None) -> dict:
        models = []
        configured = active_path or self.active_path()
        configured_file = Path(configured).expanduser() if configured else None
        for spec in CATALOG:
            path = self._path(spec)
            installed = self.installed(spec)
            active = bool(
                configured_file
                and configured_file.is_file()
                and configured_file.name == spec.filename
            )
            if active:
                installed = True
            job = self._jobs.get(spec.id)
            models.append(
                {
                    **spec.public(),
                    "path": str(configured_file if active else path),
                    "installed": installed,
                    "active": active,
                    "download": (
                        {
                            "status": job.status,
                            "bytes_done": job.bytes_done,
                            "bytes_total": job.bytes_total or spec.size_bytes,
                            "percent": round(
                                100
                                * job.bytes_done
                                / max(job.bytes_total or spec.size_bytes, 1),
                                1,
                            ),
                            "speed_bps": round(job.speed_bps),
                            "error": job.error,
                        }
                        if job
                        else None
                    ),
                }
            )
        return {"models": models, "models_dir": str(models_dir())}

    def active_path(self) -> str:
        try:
            return str(json.loads((models_dir() / "active.json").read_text()).get("path") or "")
        except (OSError, ValueError):
            return ""

    def mark_active(self, path: str) -> None:
        (models_dir() / "active.json").write_text(
            json.dumps({"path": path, "at": time.time()}), encoding="utf-8"
        )

    def start_download(self, model_id: str) -> DownloadJob:
        spec = get_spec(model_id)
        if spec is None:
            raise KeyError(model_id)
        with self._lock:
            job = self._jobs.get(model_id)
            if job and job.status in {"downloading", "verifying"}:
                return job
            if self.installed(spec):
                self._jobs[model_id] = DownloadJob(model_id, "ready", spec.size_bytes, spec.size_bytes)
                return self._jobs[model_id]
            job = DownloadJob(model_id, "downloading", 0, spec.size_bytes)
            self._jobs[model_id] = job
        threading.Thread(target=self._download, args=(spec, job), daemon=True).start()
        return job

    def _download(self, spec: LocalModelSpec, job: DownloadJob) -> None:
        target = self._path(spec)
        part = target.with_suffix(target.suffix + ".part")
        last_error = ""
        for url in spec.urls:
            try:
                if part.exists() and part.stat().st_size < spec.size_bytes:
                    resume = part.stat().st_size
                else:
                    resume = 0
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Spark/0.15 local-model-downloader",
                        **({"Range": f"bytes={resume}-"} if resume else {}),
                    },
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    if resume and resp.status != 206:
                        resume = 0
                    total = int(resp.headers.get("Content-Length") or 0) + resume
                    job.bytes_total = max(total, spec.size_bytes)
                    started = time.monotonic()
                    with part.open("ab" if resume else "wb") as out:
                        job.bytes_done = resume
                        while True:
                            chunk = resp.read(1024 * 256)
                            if not chunk:
                                break
                            out.write(chunk)
                            job.bytes_done += len(chunk)
                            elapsed = max(time.monotonic() - started, 0.1)
                            job.speed_bps = (job.bytes_done - resume) / elapsed
                if part.stat().st_size != spec.size_bytes:
                    last_error = f"下载不完整：{part.stat().st_size}/{spec.size_bytes} bytes"
                    continue
                with part.open("rb") as fh:
                    if fh.read(4) != b"GGUF":
                        last_error = "下载文件不是 GGUF（可能拿到的是登录页/错误页）"
                        part.unlink(missing_ok=True)
                        continue
                job.status = "verifying"
                sha = hashlib.sha256()
                with part.open("rb") as fh:
                    for block in iter(lambda: fh.read(1024 * 1024), b""):
                        sha.update(block)
                part.replace(target)
                target.with_suffix(target.suffix + ".sha256").write_text(sha.hexdigest())
                job.status = "ready"
                job.error = ""
                return
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                last_error = f"{type(exc).__name__}: {exc}"
                continue
        job.status = "failed"
        job.error = last_error or "所有下载镜像都失败"
