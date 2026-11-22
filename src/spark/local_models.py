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
    languages_en: str = ""
    summary_en: str = ""
    strengths_en: tuple[str, ...] = ()
    note_en: str = ""

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
            "languages_en": self.languages_en,
            "summary": self.summary,
            "summary_en": self.summary_en,
            "strengths": list(self.strengths),
            "strengths_en": list(self.strengths_en),
            "tool_parser": self.tool_parser,
            "chat_format": self.chat_format,
            "recommended": self.recommended,
            "note": self.note,
            "note_en": self.note_en,
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
        languages_en="Chinese / English",
        summary_en="1B-class native 128K context; one weight set supports Think / No-Think, built for local assistants and tool calling.",
        strengths_en=("128K context", "Chinese-first", "XML tool calls", "Apache-2.0"),
        note_en="Best for Chinese tasks; starts at 16K context, raise with SPARK_LOCAL_CTX.",
    ),
    LocalModelSpec(
        id="minicpm5-2b",
        name="MiniCPM5-2B",
        params="2B",
        filename="MiniCPM5-2B-Q4_K_M.gguf",
        size_bytes=1_561_318_368,
        context=131_072,
        license="Apache-2.0",
        languages="中文 / English",
        summary="MiniCPM5 同系列 2B 升级档，官方定位本地助手、Coding Agent 与工具调用；称同尺寸 SOTA，综合可对标部分 4B。",
        strengths=("2B 级 SOTA", "128K 上下文", "Coding / Agent", "Apache-2.0"),
        tool_parser="minicpm5",
        chat_format="default",
        urls=(
            "https://modelscope.cn/models/OpenBMB/MiniCPM5-2B-GGUF/resolve/master/MiniCPM5-2B-Q4_K_M.gguf",
            "https://hf-mirror.com/openbmb/MiniCPM5-2B-GGUF/resolve/main/MiniCPM5-2B-Q4_K_M.gguf?download=true",
        ),
        recommended=True,
        note="1B 与 4B 之间的平衡档；官方建议 llama.cpp 用 Q8，本目录选 Q4_K_M 控制体积。",
        languages_en="Chinese / English",
        summary_en="2B upgrade in the MiniCPM5 family, positioned for local assistants, coding agents and tool use; claims SOTA at this size and competes with some 4B models.",
        strengths_en=("2B-class SOTA", "128K context", "Coding / Agent", "Apache-2.0"),
        note_en="Balanced tier between 1B and 4B; the vendor suggests Q8 for llama.cpp, this catalog uses Q4_K_M to limit size.",
    ),
    LocalModelSpec(
        id="qwen3.5-2b",
        name="Qwen3.5-2B",
        params="1.89B",
        filename="Qwen3.5-2B-Q4_K_M.gguf",
        size_bytes=1_280_835_840,
        context=262_144,
        license="Apache-2.0",
        languages="中文 / English / 多语种",
        summary="Qwen3.5 官方 2B 档，原生 256K 上下文，支持 thinking 与 qwen3_coder 工具调用；比 0.8B 档更适合多步任务。",
        strengths=("256K 上下文", "工具调用", "中文", "Apache-2.0"),
        tool_parser="qwen3.5",
        chat_format="default",
        urls=(
            "https://modelscope.cn/models/unsloth/Qwen3.5-2B-GGUF/resolve/master/Qwen3.5-2B-Q4_K_M.gguf",
            "https://hf-mirror.com/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf?download=true",
        ),
        note="默认非 thinking；复杂 Agent 任务建议 4B 档，0.8B 档已因工具调用失败不收录。",
        languages_en="Chinese / English / multilingual",
        summary_en="Official Qwen3.5 2B tier with native 256K context, thinking and qwen3_coder tool calls; a better fit for multi-step work than the 0.8B tier.",
        strengths_en=("256K context", "Tool calls", "Chinese", "Apache-2.0"),
        note_en="Non-thinking by default; for complex agent work prefer the 4B tier. The 0.8B tier is excluded after failing tool-call tests.",
    ),
    LocalModelSpec(
        id="qwen3.5-4b",
        name="Qwen3.5-4B",
        params="4.21B",
        filename="Qwen3.5-4B-Q4_K_M.gguf",
        size_bytes=2_740_937_888,
        context=262_144,
        license="Apache-2.0",
        languages="中文 / English / 多语种",
        summary="4B 级稳定档：官方 256K 上下文、原生工具调用与 thinking，适合长时间本地 Coding Agent；体积约 2.6GB。",
        strengths=("4B 稳定档", "256K 上下文", "Coding Agent", "Apache-2.0"),
        tool_parser="qwen3.5",
        chat_format="default",
        urls=(
            "https://modelscope.cn/models/unsloth/Qwen3.5-4B-GGUF/resolve/master/Qwen3.5-4B-Q4_K_M.gguf",
            "https://hf-mirror.com/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf?download=true",
        ),
        note="本地复杂多轮与工具链的稳定下限；内存建议 8GB 以上。",
        languages_en="Chinese / English / multilingual",
        summary_en="The 4B stability tier: native 256K context, tool calls and thinking, suited to long local coding-agent sessions; about 2.6GB on disk.",
        strengths_en=("4B stability tier", "256K context", "Coding Agent", "Apache-2.0"),
        note_en="Practical lower bound for complex local multi-turn and tool chains; 8GB+ RAM recommended.",
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
        languages_en="English-first / Chinese usable",
        summary_en="A late-2025 on-device agent model based on Qwen3.5-0.8B; the vendor claims stronger tool use than 1.2B-class rivals.",
        strengths_en=("Tool calls", "Multi-step Agent", "0.5GB", "Apache-2.0"),
        note_en="Stronger on English tasks; weaker Chinese and long context than MiniCPM5-1B, offered as an experimental tier.",
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
