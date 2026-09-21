"""Make legacy conversation histories readable through the session API.

Existing message indexes are stable cursors. When a session already contains
indexed messages, prepend the legacy rows using indexes below the existing
minimum rather than renumbering cursors that clients may already hold.
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "20260919_01"
down_revision = "20260918_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    conversation = sa.table(
        "conversation",
        sa.column("id", sa.String),
        sa.column("session_id", sa.String),
        sa.column("content", sa.Text),
        sa.column("timestamp", sa.DateTime),
        sa.column("turn_index", sa.Integer),
    )
    sessions = sa.table(
        "chat_session",
        sa.column("id", sa.String),
        sa.column("title", sa.String),
        sa.column("project_ids", sa.JSON),
        sa.column("status", sa.String),
        sa.column("summary_upto_turn", sa.Integer),
        sa.column("last_active_at", sa.DateTime),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    projects = sa.table(
        "project_conversation",
        sa.column("conversation_id", sa.String),
        sa.column("project_id", sa.String),
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session_ids = connection.execute(sa.select(conversation.c.session_id).distinct()).scalars()
    for session_id in list(session_ids):
        messages = (
            connection.execute(
                sa.select(conversation)
                .where(conversation.c.session_id == session_id)
                .order_by(conversation.c.timestamp.asc().nullsfirst(), conversation.c.id.asc()),
            )
            .mappings()
            .all()
        )
        exists = connection.execute(
            sa.select(sessions.c.id).where(sessions.c.id == session_id),
        ).first()
        if exists is None:
            project_ids = list(
                connection.execute(
                    sa.select(projects.c.project_id)
                    .join(conversation, projects.c.conversation_id == conversation.c.id)
                    .where(conversation.c.session_id == session_id)
                    .distinct()
                    .order_by(projects.c.project_id),
                ).scalars(),
            )
            title = " ".join(messages[0]["content"].split()) or "历史会话"
            timestamps = [row["timestamp"] for row in messages if row["timestamp"] is not None]
            connection.execute(
                sessions.insert().values(
                    id=session_id,
                    title=title[:59] + "…" if len(title) > 60 else title,
                    project_ids=project_ids,
                    status="active",
                    summary_upto_turn=0,
                    created_at=min(timestamps) if timestamps else now,
                    last_active_at=max(timestamps) if timestamps else now,
                    updated_at=now,
                ),
            )
        unindexed = [row for row in messages if row["turn_index"] is None]
        existing_indexes = [row["turn_index"] for row in messages if row["turn_index"] is not None]
        first_index = min(existing_indexes) - len(unindexed) if existing_indexes else 0
        for index, message in enumerate(unindexed, start=first_index):
            connection.execute(
                conversation.update()
                .where(conversation.c.id == message["id"])
                .values(turn_index=index),
            )


def downgrade() -> None:
    """Keep recovered history and stable cursors; the repair only changes data."""
