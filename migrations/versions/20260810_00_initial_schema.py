"""Create the initial application schema."""

from alembic import op

from src.db.models import Base

revision = "20260810_00"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
