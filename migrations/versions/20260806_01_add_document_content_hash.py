"""Add ingestion content hash."""

from alembic import op
import sqlalchemy as sa

revision = "20260806_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_document_content_hash", "document", ["content_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_document_content_hash", table_name="document")
    op.drop_column("document", "content_hash")
