"""Add chat session, agent run and agent step tables."""

import sqlalchemy as sa
from alembic import op

revision = "20260918_01"
down_revision = "20260806_01"
branch_labels = None
depends_on = None

CONVERSATION_COLUMNS = (
    ("turn_index", sa.Column("turn_index", sa.Integer(), nullable=True)),
    (
        "run_id",
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("agent_run.id", name="fk_conversation_run_id_agent_run"),
            nullable=True,
        ),
    ),
    ("token_count", sa.Column("token_count", sa.Integer(), nullable=True)),
    ("message_metadata", sa.Column("message_metadata", sa.JSON(), nullable=True)),
)


def _create_chat_session(inspector: sa.Inspector) -> None:
    if inspector.has_table("chat_session"):
        return
    op.create_table(
        "chat_session",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("project_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rolling_summary", sa.Text(), nullable=True),
        sa.Column("summary_upto_turn", sa.Integer(), nullable=False),
        sa.Column("last_active_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_chat_session_last_active_at", "chat_session", ["last_active_at"])


def _create_agent_run(inspector: sa.Inspector) -> None:
    if inspector.has_table("agent_run"):
        return
    op.create_table(
        "agent_run",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("chat_session.id"),
            nullable=False,
        ),
        sa.Column("run_type", sa.String(length=20), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("state", sa.JSON(), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_agent_run_session_id", "agent_run", ["session_id"])


def _create_agent_step(inspector: sa.Inspector) -> None:
    if inspector.has_table("agent_step"):
        return
    op.create_table(
        "agent_step",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("agent_run.id"), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(length=20), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=True),
        sa.Column("arguments", sa.JSON(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_ref", sa.String(length=512), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_agent_step_run_id", "agent_step", ["run_id"])


def _extend_conversation(inspector: sa.Inspector) -> None:
    if not inspector.has_table("conversation"):
        return
    existing = {column["name"] for column in inspector.get_columns("conversation")}
    missing = [column for name, column in CONVERSATION_COLUMNS if name not in existing]
    if missing:
        # SQLite cannot ALTER in a foreign key constraint, so batch mode is
        # required for the run_id column; other dialects emit plain ALTERs.
        with op.batch_alter_table("conversation") as batch_op:
            for column in missing:
                batch_op.add_column(column)
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("conversation")}
    if "ix_conversation_session_id_turn_index" not in indexes:
        op.create_index(
            "ix_conversation_session_id_turn_index",
            "conversation",
            ["session_id", "turn_index"],
        )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    _create_chat_session(inspector)
    _create_agent_run(inspector)
    _create_agent_step(inspector)
    _extend_conversation(inspector)


def _drop_conversation_extension(inspector: sa.Inspector) -> None:
    if not inspector.has_table("conversation"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("conversation")}
    if "ix_conversation_session_id_turn_index" in indexes:
        op.drop_index("ix_conversation_session_id_turn_index", table_name="conversation")
    existing = {column["name"] for column in inspector.get_columns("conversation")}
    present = [name for name, _column in reversed(CONVERSATION_COLUMNS) if name in existing]
    if present:
        with op.batch_alter_table("conversation") as batch_op:
            for name in present:
                batch_op.drop_column(name)


def _drop_table(inspector: sa.Inspector, table: str, index: str | None = None) -> None:
    if not inspector.has_table(table):
        return
    if index is not None:
        indexes = {item["name"] for item in inspector.get_indexes(table)}
        if index in indexes:
            op.drop_index(index, table_name=table)
    op.drop_table(table)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    _drop_conversation_extension(inspector)
    _drop_table(inspector, "agent_step", "ix_agent_step_run_id")
    _drop_table(inspector, "agent_run", "ix_agent_run_session_id")
    _drop_table(inspector, "chat_session", "ix_chat_session_last_active_at")
