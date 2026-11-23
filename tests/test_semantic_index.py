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
