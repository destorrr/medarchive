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

Alembic owns schema transactions. The initial revision contains a frozen copy of the initial DDL and executes it one statement at a time. Do not edit historical Alembic revisions. The SQL file in `db/postgresql/` is the reviewable initial-schema reference; subsequent schema changes must be added as new Alembic revisions.

## Application adapter

`medarchive.adapters.postgresql.database.Database` owns an async SQLAlchemy engine, bounded connection pool, and async session factory. Use one session/transaction per application use case. Repositories must not commit; transaction ownership belongs to the service layer.

The adapter is managed by the application startup/shutdown lifecycle. Set `MEDARCHIVE_POSTGRES_ENABLED=true` to create the PostgreSQL pool and run a startup health check; `get_pg_session` provides an `AsyncSession` to PostgreSQL routes and leaves transaction ownership to the service layer. Operational and administrative routers use this adapter when `MEDARCHIVE_AUTH_BACKEND=postgresql`; legacy SQLite routers are mounted only when the SQLite backend is selected. Do not enable PostgreSQL mode in an environment without a reachable PostgreSQL database.

## Status

PostgreSQL is the target runtime for the operational and administrative application. When `MEDARCHIVE_AUTH_BACKEND=postgresql`, FastAPI mounts the PostgreSQL dashboard, packing, branch, transfer, archive, central-office, and administration routers; the production systemd unit selects this backend explicitly. SQLite remains as a legacy compatibility backend for development and the older routes, but it is not the production target and no SQLite data import is part of this migration.

## ORM and repository foundation

`medarchive.adapters.postgresql.models` now maps the target tables to SQLAlchemy 2.x declarative models, including UUID identifiers, location references, box-cycle history, archive requests, and shipment manifests. `CardRepository`, `UserRepository`, and `LocationRepository` use the caller-provided `AsyncSession`; they flush when generated values are needed but never commit.

The ORM metadata is not yet connected to Alembic autogenerate. The initial SQL revision is the schema authority until the mappings have been checked column-by-column against that frozen DDL. This avoids autogenerate proposing destructive changes because of a partial or mismatched metadata model.

The composite manifest foreign keys encode an important invariant: an object's `current_shipment_id` must refer to a manifest row for that exact card or box; a card transported inside a box must reference a `shipment_boxes` row for the same shipment. The partial unique index on active box membership prevents one card from being active in two boxes.

These relational constraints are complemented by transaction-scoped PostgreSQL workflow services and database guard triggers. The services create and pack boxes, create drafts, add standalone cards and boxes, snapshot active box contents at handover, validate archive-to-central requests, and atomically receive complete manifests. The PostgreSQL FastAPI routers use these services for the operational workflows. Integration tests cover the end-to-end branch → archive → central office → archive cycle, request fulfillment, external custody, receipt incidents, and the relevant direct-SQL integrity guards. Keep authorization in the application services and database constraints as defense in depth.


## ORM / DDL parity and PostgreSQL contract tests

The PostgreSQL ORM metadata is checked against the frozen initial DDL. The parity review corrected missing ORM mappings for allowed-value checks, the card full-text GIN index, and the partial unique index for boxes in transit. ORM check-constraint names match the PostgreSQL DDL names.

The CI suite checks the schema installed by Alembic and runs transaction-scoped PostgreSQL tests for manifest foreign keys, shipment lifecycle, handover immutability, box membership, complete receipt, archive fulfillment, and location consistency. The integration suite exercises the full logistics cycle and verifies that invalid direct SQL is rejected by database guards.

Alembic `target_metadata` remains intentionally disconnected until the complete metadata/DDL parity check is demonstrated. Do not enable autogenerate based only on these focused checks.

## Workflow guard migration (0002)

The next Alembic revision adds database-enforced workflow protections:

- shipment route, courier and issuer are immutable after handover;
- shipment box/card manifests can only be changed while the shipment is a draft;
- active box membership cannot be edited while a box is assigned to a draft/in-transit shipment or placed on an archive shelf;
- deferred constraint triggers validate that every active card in a box has the same physical location or transit shipment as its box;
- deferred shipment checks validate non-empty manifests, exact box membership snapshots, object transit locations, complete receipt, and request-backed archive-to-central fulfillment;
- archive requests are tied to one fulfillment shipment and must complete with that shipment's receipt;
- archive shelf placement is restricted to active, non-empty boxes physically located in the archive;
- domain events reject UPDATE and DELETE; corrections must be appended as new events.

The PostgreSQLArchiveWorkflow provides transaction-scoped use cases for bulk requests created only by central-office users, archive 'not found' outcomes with a reason, and box placement/removal on archive shelves. PostgreSQLShipmentWorkflow.remove_card_from_box() closes the active membership row rather than deleting history, allowing the archive to take requested cards out of an old stored box and repack them into a new outbound box.

Use one Database.transaction() around each service operation. These services do not commit independently. Database triggers are defensive integrity guards; role/location authorization remains in the application service and must be retained when the FastAPI dependencies and routers are migrated.

The guard migration is intentionally irreversible in the same way as the initial schema revision. For local development, recreate the disposable PostgreSQL database if you need to reset the complete schema.
 
## Individual archive card storage (0003)

A card can return to the archive as a standalone item, not only inside a box. The card_placements table therefore records an individual card's shelf history separately from box_placements. Archive staff can place and retrieve individual cards; a card must be removed from its shelf before it can be packed into a box or included in an outbound shipment. Conversely, cards inside a box cannot also have individual shelf placements.

When the last active card is removed from a box, its usage cycle is closed. Reusing that physical box starts the next numbered cycle; previous membership rows and cycle records remain intact.


## PostgreSQL authentication

The login form can now authenticate against PostgreSQL when explicitly selected:

```bash
export MEDARCHIVE_POSTGRES_ENABLED=true
export MEDARCHIVE_AUTH_BACKEND=postgresql
export MEDARCHIVE_DATABASE_URL='postgresql+asyncpg://...'
export MEDARCHIVE_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export MEDARCHIVE_COOKIE_SECURE=true  # required behind HTTPS in production
export MEDARCHIVE_ENVIRONMENT=production
# Strict cookie/secret validation applies only when MEDARCHIVE_ENVIRONMENT=production.
```

The default `MEDARCHIVE_AUTH_BACKEND=sqlite` remains for legacy development compatibility. The production systemd unit sets `MEDARCHIVE_AUTH_BACKEND=postgresql`, `MEDARCHIVE_POSTGRES_ENABLED=true`, and `MEDARCHIVE_ENVIRONMENT=production`. In production, startup also requires `MEDARCHIVE_COOKIE_SECURE=true` and a `MEDARCHIVE_SECRET_KEY` of at least 32 characters. Development and test environments can use PostgreSQL without production-only cookie/secret constraints.

For a completely empty PostgreSQL `users` table, create the first administrator once:

```bash
medarchive-admin create-admin --username admin --full-name "Имя Фамилия"
```

The command prompts for the password twice, stores only a bcrypt hash, and refuses to run if any user already exists. A PostgreSQL advisory transaction lock serializes concurrent bootstrap attempts. There is no public registration endpoint and no default password. Do not use the local Docker credentials in production.

## Production PostgreSQL backup and restore

The systemd backup timer uses `scripts/backup.sh` and `pg_dump` in PostgreSQL custom-archive format. It reads the same `MEDARCHIVE_DATABASE_URL` as the application, creates a mode-0600 archive, validates it with `pg_restore --list`, and retains the newest 24 completed archives by default. A lock prevents overlapping runs. A failed dump or archive validation leaves no completed backup file.

The installer creates `/etc/medarchive/medarchive.env` with mode 0640 (root:medarchive). Configure `MEDARCHIVE_DATABASE_URL` and a unique `MEDARCHIVE_SECRET_KEY` there; URL-encode reserved characters in database credentials. Install the PostgreSQL client tools matching the server's major version. Then run:

```bash
sudo systemctl start medarchive-backup.service
sudo systemctl status medarchive-backup.service
sudo ls -l /var/lib/medarchive/backups
```

To restore, stop the application and restore the selected archive into a fresh or explicitly prepared PostgreSQL database. For example, after configuring the usual libpq connection variables (`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` or `PGPASSFILE`):

```bash
createdb medarchive_restore
pg_restore --no-owner --no-acl --exit-on-error --dbname=medarchive_restore /var/lib/medarchive/backups/medarchive-YYYYMMDDTHHMMSSZ.dump
```

Validate the restored schema and application data before directing the application to it. Do not restore over a live production database without a separately reviewed maintenance and rollback plan. CI creates a real PostgreSQL dump and restores it into a second database on every workflow run.

The backup directory is private to the service account, but PostgreSQL custom archives are not encrypted by `pg_dump`. Place the directory on encrypted storage and maintain an additional encrypted off-host copy. A local hourly backup alone is not a disaster-recovery strategy.

The session cookie is HTTP-only and SameSite=Lax. Set `MEDARCHIVE_COOKIE_SECURE=true` whenever the application is served over HTTPS. The same signing secret must be used by all application instances.
