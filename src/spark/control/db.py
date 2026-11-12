"""SQLAlchemy engine/session helpers for the control plane."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from spark.control.models import Base


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite:///"):
            raw = url.removeprefix("sqlite:///")
            if raw and raw != ":memory:":
                Path(raw).expanduser().parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {"pool_pre_ping": True, "future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        self.engine: Engine = create_engine(url, **kwargs)
        self.session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, autoflush=False
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()
