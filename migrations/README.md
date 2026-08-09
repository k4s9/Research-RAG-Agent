# Database migrations

Alembic owns PostgreSQL schema changes. Apply pending revisions with `alembic upgrade head`
against the configured `POSTGRES_URL`. The first revision adds the nullable, unique
`document.content_hash` used for ingestion idempotency; existing rows can be backfilled later.
