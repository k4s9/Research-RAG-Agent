"""Add the document registry fields and the ingest batch table."""

import sqlalchemy as sa
from alembic import op

revision = "20260918_02"
down_revision = "20260918_01"
branch_labels = None
depends_on = None

DOCUMENT_COLUMNS = (
    ("title", sa.Column("title", sa.String(length=512), nullable=True)),
    ("doc_type", sa.Column("doc_type", sa.String(length=20), nullable=True)),
    ("authors", sa.Column("authors", sa.JSON(), nullable=True)),
    ("year", sa.Column("year", sa.Integer(), nullable=True)),
    ("venue", sa.Column("venue", sa.String(length=255), nullable=True)),
    ("tags", sa.Column("tags", sa.JSON(), nullable=True)),
    ("summary", sa.Column("summary", sa.Text(), nullable=True)),
    ("page_count", sa.Column("page_count", sa.Integer(), nullable=True)),
    (
        "ingest_batch_id",
        sa.Column(
            "ingest_batch_id",
            sa.String(length=36),
            sa.ForeignKey("ingest_batch.id", name="fk_document_ingest_batch_id"),
            nullable=True,
        ),
    ),
    ("extra_metadata", sa.Column("extra_metadata", sa.JSON(), nullable=True)),
)

DOCUMENT_INDEXES = (
    ("ix_document_doc_type", ["doc_type"]),
    ("ix_document_year", ["year"]),
    ("ix_document_ingest_batch_id", ["ingest_batch_id"]),
    ("ix_document_status", ["status"]),
)


def _create_ingest_batch(inspector: sa.Inspector) -> None:
    if inspector.has_table("ingest_batch"):
        return
    op.create_table(
        "ingest_batch",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("project_ids", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def _extend_document(inspector: sa.Inspector) -> None:
    if not inspector.has_table("document"):
        return
    existing = {column["name"] for column in inspector.get_columns("document")}
    missing = [column for name, column in DOCUMENT_COLUMNS if name not in existing]
    if missing:
        # Batch mode is required because SQLite cannot ALTER in a foreign key.
        with op.batch_alter_table("document") as batch_op:
            for column in missing:
                batch_op.add_column(column)
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("document")}
    for name, columns in DOCUMENT_INDEXES:
        if name not in indexes:
            op.create_index(name, "document", columns)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    _create_ingest_batch(inspector)
    _extend_document(inspector)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("document"):
        indexes = {index["name"] for index in inspector.get_indexes("document")}
        for name, _columns in reversed(DOCUMENT_INDEXES):
            if name in indexes:
                op.drop_index(name, table_name="document")
        existing = {column["name"] for column in inspector.get_columns("document")}
        present = [name for name, _column in reversed(DOCUMENT_COLUMNS) if name in existing]
        if present:
            with op.batch_alter_table("document") as batch_op:
                for name in present:
                    batch_op.drop_column(name)
    if inspector.has_table("ingest_batch"):
        op.drop_table("ingest_batch")
