# UX-09.5B — SQLite dependency inventory
2026-11-26. Static design inventory. Source tree, tests, scripts, CI and selected file contents inspected. No runtime or production data access.
| Area | Verified dependency | Target disposition |
|---|---|---|
| pyproject.toml | aiosqlite | remove after import/test migration |
| config.py | auth_backend defaults sqlite; postgres_enabled false; conditional production security | mandatory PostgreSQL URL, unconditional production security checks; reject obsolete conflicting settings during transition |
| main.py | conditional startup and router registration | one PG startup, fail closed, no fallback; preserve health/bootstrap/shutdown |
| dependencies.py | imports both adapters, constructs SQLiteDatabase even in PG mode; legacy principal/role guards | PG only, remove legacy dependency graph |
| routers/auth.py | SQLite Database import and backend branch | PG auth/throttle/session only; preserve security checks |
| adapters/sqlite | database.py, migrations.py, repositories.py | retire entire adapter after data decision |
| legacy routers | admin/archive/dashboard/packing/transfer | replace with PG equivalents; verify route parity against approved target, not obsolete workflows |
| legacy services | auth_service, backup_service, box_service, incident_service, movement_service, receive_service, request_service, return_service, search_service, shipment_service | inspect imports individually; auth helpers may be shared; do not blindly delete |
| domain/models.py, statuses.py, repositories/base.py | legacy candidates | inspect actual consumers before removal |
| templates | legacy packing/placement/receive/request/transfer/admin backup etc.; shared base/login/dashboard/search/history | remove only proven unused; preserve shared assets and approved UX |
| backup_service.py | reads/overwrites SQLite .db | remove legacy web backup/restore surface; PG encrypted backup retained |
| scripts/cleanup_stale_box_cards.py | sqlite3, MEDARCHIVE_DB_PATH, .db backup and mutation | retire; no automatic PG translation of old repair |
| scripts/install.sh, medarchive.service | explicit two backend flags | remove obsolete flags after config migration |
| scripts/backup.sh, backup service/timer | already pg_dump/pg_restore + age; 24 copies configured | retain and test restore; backup rotation not audit retention |
| cli.py | already PG create-admin | retain; align target security |
| docker-compose.yml | PG16 | retain |
| tests/conftest.py | tempfile .db and SQLite Database | replace fixtures with isolated PG, no production URL |
| tests/adapters/sqlite, legacy router/service/smoke tests | legacy candidates by tree | map behavioral coverage to target PG tests before removal; inspect imports and shared helpers |
| .github/workflows/postgresql.yml | PG16 schema and encrypted backup restore checks | preserve, extend PG-only full suite and negative fallback tests |
| README.md, docs/development/postgresql.md | README legacy compatibility and flags; development guide requires full review | update active instructions; preserve historical audit docs |
| db/postgresql/001_initial_schema.sql | PG bootstrap artifact | reconcile with Alembic; not SQLite |
## Removal order
Inventory transitive imports and template references; confirm whether any real SQLite data exists and authorized migration/retention plan (no assumption); implement PG parity for approved workflows; replace fixtures and preserve behavioral coverage; switch startup/auth/config; remove dead modules/routes/templates/scripts/dependency; update install/docs/CI; verify clean installation, upgrade, backup restore and no runtime SQLite import/fallback. Historical docs may mention SQLite; static checks scope executable code and active instructions, not history. Offline legacy data conversion if needed is isolated migration tooling, not supported runtime backend. No deletion of user database files or backups. 
## Gates
Full content scan of remaining modules/tests/docs, dependency graph and runtime import checks pending 5G; actual deployment env and SQLite data presence unknown; no tests run. Code/migrations/HTML unchanged. Next 5C target data model.
