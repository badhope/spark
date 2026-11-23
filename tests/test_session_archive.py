from pathlib import Path
from types import SimpleNamespace

from spark.store import SessionStore
from spark.web._handlers_sessions import _handle_sessions_archive


def test_archive_hides_and_restores_without_deleting(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "archive.db")
    s1 = store.create_session(tmp_path, "mock", title="one")
    s2 = store.create_session(tmp_path, "mock", title="two")
    assert {r["id"] for r in store.list_sessions()} == {s1, s2}
    store.archive_session(s1, True)
    assert [r["id"] for r in store.list_sessions()] == [s2]
    assert s1 in [r["id"] for r in store.list_sessions(include_archived=True)]
    assert store.get_session(s1)["archived_at"] > 0
    store.archive_session(s1, False)
    assert {r["id"] for r in store.list_sessions()} == {s1, s2}


def test_archive_api_switches_away_from_active_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "archive-api.db")
    s1 = store.create_session(tmp_path, "mock", title="one")
    s2 = store.create_session(tmp_path, "mock", title="two")
    rebuilt: list[str] = []
    state = SimpleNamespace(
        store=store,
        workdir=tmp_path,
        session_id=s1,
        busy=False,
        runtime_for=lambda _sid: None,
        rebuild_loop=lambda session_id=None: rebuilt.append(str(session_id)),
        status=lambda: {"session_id": rebuilt[-1] if rebuilt else s1},
    )
    handler = SimpleNamespace(
        _is_loopback=lambda: True,
        server=SimpleNamespace(state=state),
        _json=lambda status, payload: handler.responses.append((status, payload)),
        responses=[],
    )
    _handle_sessions_archive(handler, {"session_id": s1, "archived": True})
    status, payload = handler.responses[-1]
    assert status == 200 and payload["session"]["archived_at"] > 0
    assert rebuilt == [s2]
