"""Contract tests for the swappable ports in :mod:`spark.adapters`."""
from __future__ import annotations

from pathlib import Path

import pytest

from spark.adapters import (
    ArtifactStore,
    DocumentIngestor,
    PolicyEngine,
    SearchProvider,
    SecretProvider,
)
from spark.adapters.legacy import (
    BingSearchProvider,
    EnvSecretProvider,
    LegacyPolicyEngine,
    LocalArtifactStore,
    TextDocumentIngestor,
)
from spark.models import ToolCall


def test_bing_search_provider_satisfies_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "spark.adapters.legacy.bing_search",
        lambda query, max_results: [
            {"title": "T", "url": "https://example.com", "snippet": "S"}
        ],
    )
    provider = BingSearchProvider()
    assert isinstance(provider, SearchProvider)
    results = provider.search("q", max_results=1)
    assert results[0].title == "T"
    assert results[0].url == "https://example.com"
    assert results[0].snippet == "S"


def test_text_document_ingestor_satisfies_port(tmp_path: Path) -> None:
    path = tmp_path / "guide.md"
    path.write_text("第一段\n\n第二段", encoding="utf-8")
    ingestor = TextDocumentIngestor()
    assert isinstance(ingestor, DocumentIngestor)
    doc = ingestor.ingest(path)
    assert doc.name == "guide.md"
    assert "第一段" in doc.text
    assert doc.chunks


def test_local_artifact_store_round_trip(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    assert isinstance(store, ArtifactStore)
    key = store.put("reports/a.txt", b"hello")
    assert key == "reports/a.txt"
    assert store.get(key) == b"hello"
    assert store.list("reports/") == ["reports/a.txt"]
    assert store.delete(key) is True
    assert store.delete(key) is False


@pytest.mark.parametrize("bad", ["../escape.txt", "/absolute.txt", "a/../../escape.txt"])
def test_local_artifact_store_rejects_path_escape(tmp_path: Path, bad: str) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError):
        store.put(bad, b"x")


def test_legacy_policy_engine_satisfies_port() -> None:
    engine = LegacyPolicyEngine()
    assert isinstance(engine, PolicyEngine)
    decision = engine.decide(
        "suggest",
        ToolCall(id="1", name="read_file", arguments={"path": "a.txt"}),
        allow_always=set(),
        readonly_mcp=set(),
    )
    assert decision == "allow"


def test_env_secret_provider_satisfies_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPARK_TEST_SECRET", "value")
    provider = EnvSecretProvider("SPARK_TEST_")
    assert isinstance(provider, SecretProvider)
    assert provider.get("SECRET") == "value"
    assert provider.get("MISSING") is None
