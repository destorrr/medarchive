"""E4.4 integrate target checks at the deferred request boundary.

Legacy parents remain subject to existing constraints. The target status
gate is removed/replaced as part of the atomic E4.5 schema switch.
"""
from alembic import op

revision = "0019_request_guards"
down_revision = "0018_request_reservation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.medarchive_guard_target_request_composition()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $body$
        DECLARE
            parent_status text;
        BEGIN
            SELECT status INTO parent_status FROM public.archive_requests
            WHERE id = NEW.request_id FOR UPDATE;
            IF parent_status IN ('submitted', 'processing', 'completed', 'cancelled') THEN
                IF TG_OP = 'INSERT' THEN
                    RAISE EXCEPTION 'Submitted request composition is frozen'
                        USING ERRCODE = '23514';
                ELSIF NEW.status = 'removed_by_central_office'
                      AND OLD.status <> NEW.status THEN
                    RAISE EXCEPTION 'Only draft items can be removed by central office'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $body$
    """)
    op.execute("""
        CREATE TRIGGER trg_target_request_composition
        BEFORE INSERT OR UPDATE ON public.archive_request_items
        FOR EACH ROW EXECUTE FUNCTION public.medarchive_guard_target_request_composition()
    """)
    op.execute("""
        CREATE FUNCTION public.medarchive_check_target_request_deferred()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $body$
        DECLARE
            parent_id uuid;
            parent_status text;
        BEGIN
            IF TG_TABLE_NAME = 'archive_requests' THEN
                parent_id := NEW.id;
            ELSE
                parent_id := NEW.request_id;
            END IF;
            SELECT status INTO parent_status FROM public.archive_requests
            WHERE id = parent_id FOR UPDATE;
            -- Legacy terminal rows have no target item composition. Target
            -- terminal rows are identified by their existing child records.
            IF parent_status IN ('draft', 'submitted', 'processing')
               OR (parent_status IN ('completed', 'cancelled') AND EXISTS (
                   SELECT 1 FROM public.archive_request_items
                   WHERE request_id = parent_id
               )) THEN
                PERFORM public.medarchive_validate_request_reservation(parent_id);
            END IF;
            RETURN NULL;
        END;
        $body$
    """)
    for table in ("archive_requests", "archive_request_items"):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER trg_target_request_consistency
            AFTER INSERT OR UPDATE ON public.{table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.medarchive_check_target_request_deferred()
        """)
    for function in ("medarchive_guard_target_request_composition", "medarchive_check_target_request_deferred"):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC")


def downgrade() -> None:
    for table in ("archive_requests", "archive_request_items"):
        op.execute(f"DROP TRIGGER trg_target_request_consistency ON public.{table}")
    op.execute("DROP TRIGGER trg_target_request_composition ON public.archive_request_items")
    op.execute("DROP FUNCTION public.medarchive_check_target_request_deferred()")
    op.execute("DROP FUNCTION public.medarchive_guard_target_request_composition()")
