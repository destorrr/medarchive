-- Run by a provisioning administrator, outside Alembic, in the intended database.
-- Passwords are provisioned separately; no embedded credentials.
BEGIN;
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'medarchive_owner') THEN
    CREATE ROLE medarchive_owner NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'medarchive_migrator') THEN
    CREATE ROLE medarchive_migrator LOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'medarchive_runtime') THEN
    CREATE ROLE medarchive_runtime LOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'medarchive_backup') THEN
    CREATE ROLE medarchive_backup LOGIN NOINHERIT;
  END IF;
  IF EXISTS (
    SELECT FROM pg_roles WHERE rolname IN ('medarchive_owner','medarchive_migrator','medarchive_runtime','medarchive_backup')
    AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
      OR rolcanlogin <> (rolname <> 'medarchive_owner') OR (rolname <> 'medarchive_owner' AND rolinherit))
  ) THEN
    RAISE EXCEPTION 'Unsafe existing MedArchive principal attributes';
  END IF;
  IF EXISTS (
    SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
    JOIN pg_roles g ON g.oid=m.roleid
    WHERE r.rolname IN ('medarchive_owner','medarchive_migrator','medarchive_runtime','medarchive_backup')
    AND NOT (r.rolname='medarchive_migrator' AND g.rolname='medarchive_owner' AND NOT m.admin_option AND NOT m.inherit_option AND m.set_option)
  ) THEN
    RAISE EXCEPTION 'Unexpected existing MedArchive role membership';
  END IF;
END $$;
GRANT medarchive_owner TO medarchive_migrator WITH INHERIT FALSE, SET TRUE, ADMIN FALSE;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
COMMIT;
-- Object ownership, application grants and backup read scope are separate steps.
-- Do not revoke existing application DML until all writers have been migrated.
