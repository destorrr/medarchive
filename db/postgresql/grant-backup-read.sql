-- Execute as the object owner in the intended MedArchive database.
-- Existing public application schema only; no cluster-wide read role.
BEGIN;
DO $backup$
BEGIN
  IF current_user <> 'medarchive_owner' THEN
    RAISE EXCEPTION 'Backup grants require medarchive_owner';
  END IF;
END $backup$;
GRANT USAGE ON SCHEMA public TO medarchive_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO medarchive_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO medarchive_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE medarchive_owner IN SCHEMA public
  GRANT SELECT ON TABLES TO medarchive_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE medarchive_owner IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO medarchive_backup;
COMMIT;
