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
- **legacy baseline only:** current ORM still models one per-card request and shipment-wide receipt. The target retains at most one fulfillment shipment per grouped parent, but replaces shipment-wide receipt with item-level receipt and a `partially_received` shipment projection; use the P-09 target section below, not this legacy implementation description.
- archive shelf placement is restricted to active, non-empty boxes physically located in the archive;
- domain events reject UPDATE and DELETE; corrections must be appended as new events.

The PostgreSQLArchiveWorkflow provides transaction-scoped use cases for bulk requests created only by central-office users, archive 'not found' outcomes with a reason, and box placement/removal on archive shelves. PostgreSQLShipmentWorkflow.remove_card_from_box() closes the active membership row rather than deleting history, allowing the archive to take requested cards out of an old stored box and repack them into a new outbound box.

Use one Database.transaction() around each service operation. These services do not commit independently. Database triggers are defensive integrity guards; role/location authorization remains in the application service and must be retained when the FastAPI dependencies and routers are migrated.

The guard migration is intentionally irreversible in the same way as the initial schema revision. For local development, recreate the disposable PostgreSQL database if you need to reset the complete schema.
 
## Individual archive card storage (0003)

A card can return to the archive as a standalone item, not only inside a box. The card_placements table therefore records an individual card's shelf history separately from box_placements. Archive staff can place and retrieve individual cards; a card must be removed from its shelf before it can be packed into a box or included in an outbound shipment. Conversely, cards inside a box cannot also have individual shelf placements.

When the last active card is removed from a box, its usage cycle is closed. Reusing that physical box starts the next numbered cycle; previous membership rows and cycle records remain intact.


## P-09 grouped requests and single full shipment — target design, not implemented

The existing PostgreSQL ORM/guard baseline is legacy for P-09: `ArchiveRequest` currently represents one card, `Shipment` has no `partially_received` state, and `ShipmentCard` has no request-item link or per-card receipt metadata. The existing `Card.current_location_id/current_shipment_id` constraint also does not separately represent the organization retaining custody while a card is physically in transit.

The approved UX-06 target is documented in `docs/ux-audit/UX-06-TECHNICAL-DATA-MODEL.md`: 
- one grouped parent request → at most one archive → central-office fulfillment shipment;
- the archive must fully process every request item before creating the shipment; each item has a final processing outcome (`extracted`, `not_found`, or `removed_by_archive`);
- the single shipment's immutable manifest contains all and only `extracted` items; `not_found` and `removed_by_archive` are excluded;
- partial issue, multiple shipments per parent, and processing remaining items after handover are prohibited; handover transfers the full manifest in one atomic operation;
- each manifest card has its own receipt actor/time; the single shipment state includes `partially_received` while only some cards have actual CO receipt;
- physical transit and responsible custodian are separate facts. During archive → CO transit, the card points to the single shipment while `custody_location_id` remains archive; only actual item receipt transfers custody to central office;
- after handover, cancellation is prohibited. Before handover, the archive may cancel the whole request with a reason; extracted cards are returned to boxes, while `not_found` and `removed_by_archive` remain final outcomes;
- if every item is `not_found` or `removed_by_archive`, no shipment is created and the parent can complete without a shipment.

This section records the gap between current implementation and approved target; it does not claim the target is implemented. A future additive Alembic migration must preserve the existing per-card `archive_requests` table during compatibility (shadow parent table), add parent/item/single-shipment/receipt/custody relations, reconcile historical data and only cut over after read-only preflight. Do not edit historical Alembic revisions, run DDL, or enable autogenerate based only on this design. Runtime code, ORM mappings, SQL and tests remain unchanged until a separate implementation authorization.
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

The systemd backup timer uses `scripts/backup.sh`, `pg_dump` in PostgreSQL custom-archive format, and `age` encryption. It validates the plaintext dump with `pg_restore --list`, encrypts it to a `.dump.age` archive, sets mode 0600, and retains the newest 24 completed encrypted archives by default. Plaintext is held only in a mode-0600 temporary file inside the private backup directory and is removed after successful encryption; the cleanup trap also removes it on failure. A lock prevents overlapping runs. The script fails closed if an age recipient is not configured or encryption fails.

Generate an age identity on a trusted offline machine, not on the application server:

```bash
age-keygen -o medarchive-backup.agekey
age-keygen -y medarchive-backup.agekey
```

Copy only the public `age1...` recipient printed by the second command into `MEDARCHIVE_BACKUP_AGE_RECIPIENT` in `/etc/medarchive/medarchive.env`. Keep the private identity file outside the server, separate from the backups, and maintain a tested recovery copy of it. The application server needs only the public recipient to encrypt backups.

The installer creates `/etc/medarchive/medarchive.env` with mode 0640 (root:medarchive). Configure `MEDARCHIVE_DATABASE_URL`, a unique `MEDARCHIVE_SECRET_KEY`, and `MEDARCHIVE_BACKUP_AGE_RECIPIENT` there; URL-encode reserved characters in database credentials. Install the PostgreSQL client tools matching the server's major version and the `age` package. Then rerun `sudo bash scripts/install.sh`: the timer is enabled only when the database URL, secret, age recipient, and age executable are available. If any prerequisite is missing, the installer disables the backup timer.

```bash
sudo systemctl start medarchive-backup.service
sudo systemctl status medarchive-backup.service
sudo ls -l /var/lib/medarchive/backups
```

To restore, stop the application and decrypt the selected archive with the private age identity on a trusted recovery machine. Then restore the plaintext dump into a fresh or explicitly prepared PostgreSQL database. For example:

```bash
umask 077
age --decrypt --identity /secure/offline/medarchive-backup.agekey \
  --output /secure/restore/medarchive.dump \
  /var/lib/medarchive/backups/medarchive-YYYYMMDDTHHMMSSZ.dump.age

createdb medarchive_restore
pg_restore --no-owner --no-acl --exit-on-error --dbname=medarchive_restore /secure/restore/medarchive.dump
```

Validate the restored schema and application data before directing the application to it. Do not restore over a live production database without a separately reviewed maintenance and rollback plan. CI generates a temporary age identity, creates an encrypted PostgreSQL dump, decrypts it with the private identity, and restores it into a second database on every workflow run.

New backups are encrypted before they are published. The backup script refuses to run while any legacy `medarchive-*.dump` plaintext archive remains in the backup directory. Encrypt and verify those old files, then securely retire the plaintext originals before restarting the timer. Maintain an additional encrypted off-host copy; a local hourly backup alone is not a disaster-recovery strategy.

The session cookie is HTTP-only and SameSite=Lax. Set `MEDARCHIVE_COOKIE_SECURE=true` whenever the application is served over HTTPS. The same signing secret must be used by all application instances.

### Failed-login throttling

PostgreSQL authentication uses the `auth_login_throttles` table. Failed attempts are counted in PostgreSQL, so limits are shared by application workers and survive process restarts:

- 5 failed attempts for a username/client-IP pair in a 15-minute window trigger a 15-minute block for that pair.
- 20 failed attempts from one client IP in a 15-minute window trigger a 15-minute IP block.
- A successful login clears the username/IP counter. The IP-wide counter is intentionally retained.
- A block response is HTTP 429 and includes `Retry-After`.
- Throttle identifiers are HMAC-SHA256 digests keyed by `MEDARCHIVE_SECRET_KEY`; raw usernames and IP addresses are not stored in the throttle table. Expired rows are periodically pruned during failed-login processing.

The counters use atomic PostgreSQL upserts and transaction-scoped advisory locks so concurrent requests from the same IP cannot all pass the check before the failure counter is updated. The throttle table is created by Alembic revision `0011_login_throttles`.

The IP used by the limiter is the client address exposed by the ASGI server. When deploying behind a reverse proxy, configure Uvicorn to trust forwarded headers only from the exact proxy addresses; never trust arbitrary client-supplied `X-Forwarded-For` values. The legacy SQLite compatibility login does not use this PostgreSQL throttle; production must use `MEDARCHIVE_AUTH_BACKEND=postgresql`.

### Cross-origin request protection

All state-changing HTTP methods (`POST`, `PUT`, `PATCH`, and `DELETE`) are checked by application middleware. Requests marked `Sec-Fetch-Site: cross-site` are rejected, and supplied `Origin` or `Referer` origins must match the application's origin. The check applies to both PostgreSQL and legacy routes, including login and logout. Browser requests without cross-origin metadata remain compatible; non-browser API clients that omit these headers are not blocked by the origin check.

The session cookie also remains `SameSite=Lax`, and logout is POST-only. Keep the reverse proxy's host and forwarded-scheme configuration correct so the application compares the browser origin against the externally served HTTPS origin.
