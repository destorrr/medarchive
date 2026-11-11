# PostgreSQL development environment

## Purpose

The target application uses PostgreSQL only. SQLite remains temporarily while existing repositories and services are replaced; it is not a supported target database and no SQLite data is imported.

## Start PostgreSQL

```bash
docker compose up -d postgres
```

Local development connection string:

```text
postgresql+asyncpg://medarchive:medarchive@localhost:5432/medarchive
```

Override it with `MEDARCHIVE_DATABASE_URL` elsewhere. These credentials are for local development only and must never be reused in production.

## Install and create the schema

```bash
pip install -e '.[dev]'
alembic upgrade head
```

Alembic owns schema transactions. The initial revision executes `db/postgresql/001_initial_schema.sql` one statement at a time. Keep the SQL source and revision in sync; subsequent schema changes must be added as new Alembic revisions.

## Application adapter

`medarchive.adapters.postgresql.database.Database` owns an async SQLAlchemy engine, bounded connection pool, and async session factory. Use one session/transaction per application use case. Repositories must not commit; transaction ownership belongs to the service layer.

The adapter is deliberately not wired into FastAPI dependencies yet: current routers, repositories, and services still issue SQLite SQL and use integer identifiers. Wiring the new engine before replacing those components would make the app fail at runtime.

## Status

This is the first infrastructure step, not a completed application migration. Before switching the app, add PostgreSQL integration tests for the schema and transaction invariants, replace SQLite repositories and integer domain IDs, and run the full router/service suite against PostgreSQL.
