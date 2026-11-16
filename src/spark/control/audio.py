"""Speech-to-text for meeting audio, using a local Whisper model.

Design constraints that shaped this module:

* **Local only.** No cloud ASR: meeting audio is sensitive. faster-whisper runs
  the CTranslate2 model on CPU and never opens a network connection at
  inference time; the model is fetched once into ``$SPARK_HOME/models``.
* **Graceful degradation.** If the optional dependency or the model is missing,
  the caller gets a clear ``RuntimeError`` with the install hint and the rest of
  the office pipeline keeps working (documents, uploads, minutes).
* **Bounded work.** Long recordings are expensive: duration, file size and the
  number of processed seconds are capped, and a segment cap stops a runaway.

The model download honours ``HF_ENDPOINT`` so an air-gapped or China-hosted
deployment can point at a mirror (``https://hf-mirror.com``).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4", ".webm"}
MAX_AUDIO_BYTES = 200 * 1024 * 1024
MAX_AUDIO_SECONDS = 3 * 60 * 60
MAX_SEGMENTS = 4000
DEFAULT_MODEL = "small"
_ENGINE_LOCK = threading.Lock()
_ENGINES: dict[str, Any] = {}


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 2), "end": round(self.end, 2), "text": self.text}


@dataclass
class Transcript:
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0
    model: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "segments": [s.to_dict() for s in self.segments],
            "language": self.language,
            "duration": round(self.duration, 2),
            "model": self.model,
            "warnings": list(self.warnings),
        }

    def as_speaker_lines(self) -> str:
        """Timestamped lines, the shape the minutes parser expects.

        ``[00:01:23] 内容`` keeps the original ordering and makes the citation
        line numbers in the generated minutes meaningful to a human reviewer.
        """
        lines: list[str] = []
        for seg in self.segments:
            minutes, seconds = divmod(int(seg.start), 60)
            hours, minutes = divmod(minutes, 60)
            stamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
            lines.append(f"[{stamp}] {seg.text}".strip())
        return "\n".join(lines) if lines else self.text


class AudioTranscriber:
    """Lazy singleton around faster-whisper."""

    def __init__(self, model_root: Path, model_size: str = "") -> None:
        self.model_root = Path(model_root)
        self.model_size = model_size or os.environ.get("SPARK_ASR_MODEL", DEFAULT_MODEL)

    def _local_model_dir(self) -> Path | None:
        """A pre-downloaded model directory, if one exists.

        faster-whisper accepts a filesystem path, which is the only reliable
        option offline: passing a model name makes it call Hugging Face Hub even
        when the weights are already on disk under a flat layout.
        """
        candidate = self.model_root / self.model_size
        if candidate.is_dir() and (candidate / "model.bin").is_file():
            return candidate
        if (self.model_root / "model.bin").is_file():
            return self.model_root
        return None

    @property
    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except Exception:  # noqa: BLE001 - optional dependency
            return False
        return True

    def _engine(self) -> Any:
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "audio transcription requires faster-whisper "
                "(install spark-agent[office])"
            ) from exc
        key = self.model_size
        with _ENGINE_LOCK:
            if key in _ENGINES:
                return _ENGINES[key]
            self.model_root.mkdir(parents=True, exist_ok=True)
            local = self._local_model_dir()
            try:
                engine = WhisperModel(
                    str(local) if local is not None else self.model_size,
                    device="cpu",
                    compute_type="int8",
                    download_root=str(self.model_root),
                )
            except Exception as exc:  # noqa: BLE001 - missing model / no network
                raise RuntimeError(
                    f"could not load the Whisper model '{self.model_size}': {exc}. "
                    f"Pre-download it into {self.model_root} or set HF_ENDPOINT "
                    "to a reachable mirror (e.g. https://hf-mirror.com)."
                ) from exc
            _ENGINES[key] = engine
            return engine

    def transcribe(self, path: Path, *, language: str | None = None) -> Transcript:
        if not path.is_file():
            raise FileNotFoundError(f"audio file not found: {path.name}")
        suffix = path.suffix.lower()
        if suffix not in _AUDIO_SUFFIXES:
            raise ValueError(f"unsupported audio type: {suffix or 'unknown'}")
        size = path.stat().st_size
        if size > MAX_AUDIO_BYTES:
            raise ValueError(
                f"audio file is too large ({size} bytes, limit {MAX_AUDIO_BYTES})"
            )

        engine = self._engine()
        warnings: list[str] = []
        segments_iter, info = engine.transcribe(
            str(path),
            language=language or None,
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
        )
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        if duration > MAX_AUDIO_SECONDS:
            raise ValueError(
                f"audio is too long ({duration / 3600:.1f}h, limit "
                f"{MAX_AUDIO_SECONDS / 3600:.0f}h)"
            )

        segments: list[TranscriptSegment] = []
        for segment in segments_iter:
            if len(segments) >= MAX_SEGMENTS:
                warnings.append(f"only the first {MAX_SEGMENTS} segments were kept")
                break
            text = (segment.text or "").strip()
            if text:
                segments.append(
                    TranscriptSegment(
                        start=float(segment.start or 0.0),
                        end=float(segment.end or 0.0),
                        text=text,
                    )
                )
        if not segments:
            warnings.append("no speech detected in the audio")

        return Transcript(
            text=" ".join(s.text for s in segments).strip(),
            segments=segments,
            language=str(getattr(info, "language", "") or ""),
            duration=duration,
            model=self.model_size,
            warnings=warnings,
        )
