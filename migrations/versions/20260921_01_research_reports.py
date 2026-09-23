"""Run ownership and immutable, version-linked research reports."""

import sqlalchemy as sa
from alembic import op

revision = "20260921_01"
down_revision = "20260919_01"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "active_run_id" not in {c["name"] for c in inspector.get_columns("chat_session")}:
        op.add_column("chat_session", sa.Column("active_run_id", sa.String(36), nullable=True))
    if not inspector.has_table("report_artifact"):
        op.create_table(
            "report_artifact",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "run_id", sa.String(36), sa.ForeignKey("agent_run.id"), nullable=False, unique=True
            ),
            sa.Column(
                "session_id", sa.String(36), sa.ForeignKey("chat_session.id"), nullable=False
            ),
            sa.Column(
                "parent_report_id",
                sa.String(36),
                sa.ForeignKey("report_artifact.id"),
                nullable=True,
            ),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("markdown", sa.Text(), nullable=False),
            sa.Column("structured", sa.JSON(), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("project_ids", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_report_artifact_session_id", "report_artifact", ["session_id"])


def downgrade():
    op.drop_table("report_artifact")
    with op.batch_alter_table("chat_session") as batch:
        batch.drop_column("active_run_id")
