"""Meeting-audio transcription, driven by a real Whisper model.

The unit-level contract (parsing, limits, error messages) is asserted against
``AudioTranscriber`` directly; the API contract is asserted against a real
``TestClient`` so upload → transcribe → minutes is one continuous path.

The transcription tests need a local model. They are skipped with an explicit
reason when neither ``SPARK_TEST_ASR_MODEL`` nor a model under
``$SPARK_HOME/models/asr`` is present, so the default suite stays offline-safe
without pretending the feature was exercised.
"""

from __future__ import annotations

import os
import shutil
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spark.control.app import create_app
from spark.control.audio import (
    MAX_AUDIO_BYTES,
    AudioTranscriber,
    Transcript,
    TranscriptSegment,
)
from spark.control.settings import ControlSettings

# Resolved at import time: ``tests/conftest.py`` repoints ``SPARK_HOME`` at a
# per-test temp dir, so asking ``default_home()`` inside a test would look for
# models in an empty directory and skip every real-model assertion.
_REAL_MODEL_ROOT = Path(
    os.environ.get("SPARK_TEST_ASR_MODEL_ROOT")
    or Path(os.environ.get("SPARK_HOME") or Path.home() / ".spark") / "models"
)

SAMPLE = Path(__file__).parent / "fixtures" / "meeting-sample.wav"


def _model_root() -> Path:
    return _REAL_MODEL_ROOT


def _model_size() -> str:
    size = os.environ.get("SPARK_TEST_ASR_MODEL", "tiny")
    if (Path(_model_root()) / "asr" / size / "model.bin").is_file():
        return size
    return ""


def _silent_wav(path: Path, seconds: float = 2.0) -> Path:
    """Silence, used to prove the "no speech" path stays explicit."""
    rate = 16000
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def _require_model() -> str:
    size = _model_size()
    if not size:
        pytest.skip(
            "no Whisper model found; set SPARK_TEST_ASR_MODEL / "
            "SPARK_TEST_ASR_MODEL_ROOT or pre-download into $SPARK_HOME/models/asr"
        )
    return size


def test_transcript_renders_timestamped_lines() -> None:
    transcript = Transcript(
        text="hello world",
        segments=[
            TranscriptSegment(start=0.0, end=3.0, text="hello"),
            TranscriptSegment(start=65.0, end=67.5, text="world"),
        ],
        language="en",
        duration=67.5,
        model="tiny",
    )
    lines = transcript.as_speaker_lines().splitlines()
    assert lines == ["[00:00] hello", "[01:05] world"]
    # No segments at all must still yield the plain text, never an empty string.
    assert Transcript(text="plain").as_speaker_lines() == "plain"


def test_transcriber_rejects_bad_input(tmp_path: Path) -> None:
    transcriber = AudioTranscriber(tmp_path / "models", "tiny")
    with pytest.raises(FileNotFoundError):
        transcriber.transcribe(tmp_path / "missing.wav")
    text_file = tmp_path / "notes.txt"
    text_file.write_text("not audio")
    with pytest.raises(ValueError, match="unsupported audio type"):
        transcriber.transcribe(text_file)
    big = tmp_path / "big.wav"
    with big.open("wb") as handle:
        handle.truncate(MAX_AUDIO_BYTES + 1)
    with pytest.raises(ValueError, match="too large"):
        transcriber.transcribe(big)


def test_local_model_dir_prefers_named_subdir(tmp_path: Path) -> None:
    root = tmp_path / "asr"
    (root / "tiny").mkdir(parents=True)
    (root / "tiny" / "model.bin").write_bytes(b"x")
    assert AudioTranscriber(root, "tiny")._local_model_dir() == root / "tiny"
    # A flat layout (files directly in the root) is accepted too.
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "model.bin").write_bytes(b"x")
    assert AudioTranscriber(flat, "small")._local_model_dir() == flat
    empty = tmp_path / "empty"
    empty.mkdir()
    assert AudioTranscriber(empty, "tiny")._local_model_dir() is None


def test_transcribes_real_audio(tmp_path: Path) -> None:
    size = _require_model()
    transcriber = AudioTranscriber(_model_root() / "asr", size)
    if not transcriber.available:
        pytest.skip("faster-whisper is not installed (pip install spark-agent[asr])")
    wav = tmp_path / "sample.wav"
    shutil.copyfile(SAMPLE, wav)
    transcript = transcriber.transcribe(wav)
    assert transcript.model == size
    assert transcript.duration > 0
    assert transcript.language, "language detection produced nothing"
    assert transcript.segments, "no speech recognised in the spoken fixture"
    # The fixture says "Good morning. We decided to ship the new release on
    # Friday. Alice will prepare the documentation." Assert on robust tokens:
    # a tiny model may mishear a function word, but not the whole sentence.
    text = transcript.text.lower()
    assert "good morning" in text
    assert "documentation" in text
    assert transcript.as_speaker_lines().startswith("[00:00]")

    # Silence must be reported, not silently turned into text.
    silent = transcriber.transcribe(_silent_wav(tmp_path / "silent.wav"))
    assert not silent.segments
    assert any("no speech" in warning for warning in silent.warnings)


def test_api_transcribe_then_minutes(tmp_path: Path) -> None:
    size = _require_model()
    if not AudioTranscriber(_model_root() / "asr", size).available:
        pytest.skip("faster-whisper is not installed")
    settings = ControlSettings(
        database_url=f"sqlite:///{(tmp_path / 'audio.db').as_posix()}",
        artifact_root=str(tmp_path / "artifacts"),
        document_root=str(tmp_path / "documents"),
        auth_mode="dev",
        allow_dev_auth=True,
        asr_model=size,
        model_root=str(_model_root()),
    )
    app = create_app(settings)
    wav = tmp_path / "meeting.wav"
    shutil.copyfile(SAMPLE, wav)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/office/audio/transcribe",
            files={"file": ("meeting.wav", wav.read_bytes(), "audio/wav")},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["filename"].startswith("audio/")
        assert body["model"] == size
        assert body["duration"] > 0
        assert body["text"] == body["text"].strip()

        # The uploaded copy stays inside the document root.
        stored = tmp_path / "documents" / body["filename"]
        assert stored.is_file()

        # Unsupported formats are rejected before any work happens.
        bad = client.post(
            "/api/v1/office/audio/transcribe",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert bad.status_code == 422

        # The transcript is directly usable as meeting input.
        minutes = client.post(
            "/api/v1/office/meetings",
            json={"title": "录音会议", "transcript": body["transcript"]},
        )
        assert minutes.status_code == 201, minutes.text
        parsed = minutes.json()["minutes"]
        assert parsed["title"] == "录音会议"
        assert "议题" in parsed["summary"]
