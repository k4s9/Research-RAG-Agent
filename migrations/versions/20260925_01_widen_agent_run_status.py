"""Allow every runtime status to persist, including insufficient_evidence."""

import sqlalchemy as sa
from alembic import op

revision = "20260925_01"
down_revision = "20260921_01"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("agent_run") as batch:
        batch.alter_column(
            "status", existing_type=sa.String(20), type_=sa.String(32), existing_nullable=False,
        )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM agent_run WHERE length(status) > 20")):
        raise RuntimeError("Cannot shorten agent_run.status while longer statuses are stored")
    with op.batch_alter_table("agent_run") as batch:
        batch.alter_column(
            "status", existing_type=sa.String(32), type_=sa.String(20), existing_nullable=False,
        )
