# Migrations

Alembic migrations own the production schema. Run with:

```bash
DATABASE_URL=postgresql+psycopg://user:pass@host/db alembic upgrade head
```

`PostgresTenancyStore` also creates its tables lazily when absent,
which exists for development and tests only; production must use
these migrations.

## Row-Level Security evaluation (W3)

RLS is worth having as a second barrier behind the application
level membership checks, and the policies are provided in
`rls_policies.sql`. They are opt-in: the application currently
opens a connection per operation and does not yet set the
`app.workspace_id` and `app.user_id` transaction settings the
policies read. Enabling RLS before that plumbing exists would
deny every query. Activation steps and the full reasoning are in
the SQL file's header.
