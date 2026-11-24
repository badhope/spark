from pathlib import Path

from spark.semantic_index import CodeIndex, build_context_block


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_semantic_index_finds_concept_not_literal(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "repo"
    root.mkdir()
    _write(
        root,
        "auth/session.py",
        '''
class SessionGuard:
    """Reject credentials whose expiry timestamp is already in the past."""

    def check(self, token):
        if token.expires_at < now():
            raise ExpiredError("token lifetime ended")
        return True
''',
    )
    _write(
        root,
        "billing/invoice.py",
        '''
def total(items):
    return sum(item.price for item in items)
''',
    )
    index = CodeIndex(root, home)
    status = index.refresh()
    assert status.files == 2
    assert status.chunks >= 2

    hits = index.search("reject expired token")
    assert hits
    assert hits[0]["path"] == "auth/session.py"
    assert hits[0]["symbol"] == "SessionGuard"
    assert "semantic_code_index" in build_context_block(hits)

    # A literal grep term still ranks the right file.
    hits = index.search("total price")
    assert hits[0]["path"] == "billing/invoice.py"


def test_index_is_reused_until_sources_change(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "a.py", "def alpha():\n    return 1\n")
    index = CodeIndex(root, home)
    first = index.refresh()
    second = index.refresh()
    assert first.chunks == second.chunks
    assert second.built_at == first.built_at

    _write(root, "b.py", "def beta():\n    return 2\n")
    third = index.refresh()
    assert third.files == 2
    assert third.built_at > first.built_at


def test_chunks_split_on_top_level_symbols_not_indented_methods(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(
        root,
        "guard.py",
        """class SessionGuard:
    def check(self, token):
        return token.expires_at >= now()

def standalone(value):
    return value + 1
""",
    )
    index = CodeIndex(root, tmp_path / "home")
    index.refresh()
    chunks = index._load()["chunks"]
    guard = [c for c in chunks if c["path"] == "guard.py" and c["symbol"] == "SessionGuard"]
    assert guard
    assert "def check" in guard[0]["text"]
    assert [c["symbol"] for c in chunks if c["path"] == "guard.py"] == [
        "SessionGuard",
        "standalone",
    ]


def test_incremental_refresh_reuses_unchanged_chunks_and_vectors(tmp_path: Path) -> None:
    class FakeEmbedder:
        model = "fake-v1"

        def __init__(self) -> None:
            self.texts: list[str] = []

        def __call__(self, texts: list[str]) -> list[list[float]]:
            self.texts.extend(texts)
            return [[1.0, 0.0] for _ in texts]

    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "a.py", "def alpha():\n    return 1\n")
    embedder = FakeEmbedder()
    index = CodeIndex(root, tmp_path / "home", embedder=embedder, embedding_model="fake-v1")
    index.refresh(force=True)
    first_texts = list(embedder.texts)
    assert any("alpha" in text for text in first_texts)

    _write(root, "b.py", "def beta():\n    return 2\n")
    status = index.refresh()
    assert status.files == 2
    new_texts = embedder.texts[len(first_texts) :]
    assert new_texts
    assert all("alpha" not in text for text in new_texts)
    assert any("beta" in text for text in new_texts)

    data = index._load()
    assert set(data["file_hashes"]) == {"a.py", "b.py"}
    assert len(index._load_vectors()) == len(data["chunks"])


def test_method_query_ranks_owner_class_chunk(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(
        root,
        "guard.py",
        """class SessionGuard:
    def check(self, token):
        if token.expires_at < now():
            raise ExpiredError("expired")
        return True
""",
    )
    index = CodeIndex(root, tmp_path / "home")
    index.refresh()
    hits = index.search("check expired token")
    assert hits
    assert hits[0]["symbol"] == "SessionGuard"
