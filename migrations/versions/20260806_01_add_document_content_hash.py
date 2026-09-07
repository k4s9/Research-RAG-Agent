"""Add ingestion content hash."""

import sqlalchemy as sa
from alembic import op

revision = "20260806_01"
down_revision = "20260810_00"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("document")}
    if "content_hash" not in columns:
        op.add_column("document", sa.Column("content_hash", sa.String(length=64), nullable=True))
    indexes = {index["name"] for index in inspector.get_indexes("document")}
    if "ix_document_content_hash" not in indexes:
        op.create_index("ix_document_content_hash", "document", ["content_hash"], unique=True)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("document")}
    if "ix_document_content_hash" in indexes:
        op.drop_index("ix_document_content_hash", table_name="document")
    columns = {column["name"] for column in inspector.get_columns("document")}
    if "content_hash" in columns:
        op.drop_column("document", "content_hash")
