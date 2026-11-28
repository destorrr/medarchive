"""E4.4: protect additive P09 item identity and historical facts.

Compatible with legacy writers: they do not update archive_request_items.
The target parent lifecycle and receipt transitions are activated together
in E4.5; this revision does not change the legacy shipment constraints.
"""
from alembic import op

revision = "0017_request_item_history"
down_revision = "0016_target_validators"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.medarchive_guard_request_item_history()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $body$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Request item history cannot be deleted'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.request_id IS DISTINCT FROM OLD.request_id
               OR NEW.card_id IS DISTINCT FROM OLD.card_id
               OR NEW.added_at IS DISTINCT FROM OLD.added_at
               OR NEW.added_by IS DISTINCT FROM OLD.added_by
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'Request item identity and addition history are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.status = 'removed_by_central_office' AND NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'Removed draft item history is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $body$
    """)
    op.execute("""
        CREATE TRIGGER trg_request_item_history
        BEFORE UPDATE OR DELETE ON public.archive_request_items
        FOR EACH ROW EXECUTE FUNCTION public.medarchive_guard_request_item_history()
    """)
    op.execute("REVOKE ALL ON FUNCTION public.medarchive_guard_request_item_history() FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_request_item_history ON public.archive_request_items")
    op.execute("DROP FUNCTION public.medarchive_guard_request_item_history()")
