# Database migrations

Alembic owns schema changes. Apply pending revisions with `python scripts/init_db.py` or
`alembic upgrade head` against the configured `POSTGRES_URL`. The base revision creates an empty
database schema without deleting existing tables. The following revision adds the nullable, unique
`document.content_hash` to legacy schemas when needed.

Revision `20260925_01` widens `agent_run.status` from 20 to 32 characters so PostgreSQL can
save `insufficient_evidence`. Existing deployments must apply this migration; `create_all`
does not alter existing columns. Downgrade refuses to truncate already stored longer statuses.
