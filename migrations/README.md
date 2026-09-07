# Database migrations

Alembic owns schema changes. Apply pending revisions with `python scripts/init_db.py` or
`alembic upgrade head` against the configured `POSTGRES_URL`. The base revision creates an empty
database schema without deleting existing tables. The following revision adds the nullable, unique
`document.content_hash` to legacy schemas when needed.
