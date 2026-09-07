"""Apply database migrations without deleting existing data."""

from alembic import command
from alembic.config import Config


def init_db() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    print("数据库迁移完成")


if __name__ == "__main__":
    init_db()
