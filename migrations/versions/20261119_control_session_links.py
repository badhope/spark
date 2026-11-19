"""link control tasks and artifacts to agent sessions

Revision ID: 20261119_session_links
Revises: 7dbcd0bb0272
Create Date: 2026-11-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261119_session_links"
down_revision: Union[str, Sequence[str], None] = "7dbcd0bb0272"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("session_id", sa.String(length=64), nullable=False, server_default=""),
    )
    op.create_index(op.f("ix_tasks_session_id"), "tasks", ["session_id"], unique=False)
    op.add_column(
        "artifacts",
        sa.Column("session_id", sa.String(length=64), nullable=False, server_default=""),
    )
    op.create_index(
        op.f("ix_artifacts_session_id"), "artifacts", ["session_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_artifacts_session_id"), table_name="artifacts")
    op.drop_column("artifacts", "session_id")
    op.drop_index(op.f("ix_tasks_session_id"), table_name="tasks")
    op.drop_column("tasks", "session_id")
