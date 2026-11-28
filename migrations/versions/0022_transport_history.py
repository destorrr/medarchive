"""E4.4 preserve fulfillment links and original per-object receipt facts.

Additive guards; the legacy draft-only manifest guard remains active.
Receipt corrections must use separate historical facts, not overwrite
the original receipt. E4.5 replaces the legacy draft-only restriction.
"""
from alembic import op

revision = "0022_transport_history"
down_revision = "0021_p09_manifest_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.medarchive_guard_fulfillment_link()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, pg_temp
        AS $body$
        BEGIN
            IF OLD.request_id IS NOT NULL
               AND NEW.request_id IS DISTINCT FROM OLD.request_id THEN
                RAISE EXCEPTION 'Established fulfillment link is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.status <> 'draft'
               AND NEW.request_id IS DISTINCT FROM OLD.request_id THEN
                RAISE EXCEPTION 'Fulfillment link cannot change after draft'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $body$
    """)
    op.execute("""
        CREATE TRIGGER trg_fulfillment_link_history
        BEFORE UPDATE ON public.shipments
        FOR EACH ROW EXECUTE FUNCTION public.medarchive_guard_fulfillment_link()
    """)
    op.execute("""
        CREATE FUNCTION public.medarchive_guard_object_receipt_history()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, pg_temp
        AS $body$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.received_at IS NOT NULL THEN
                    RAISE EXCEPTION 'Original object receipt cannot be deleted'
                        USING ERRCODE = '23514';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.received_at IS NOT NULL AND NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'Original object receipt is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $body$
    """)
    for table in ("shipment_boxes", "shipment_cards"):
        op.execute(f"""
            CREATE TRIGGER trg_object_receipt_history
            BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.medarchive_guard_object_receipt_history()
        """)
    for function in ("medarchive_guard_fulfillment_link", "medarchive_guard_object_receipt_history"):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC")


def downgrade() -> None:
    for table in ("shipment_boxes", "shipment_cards"):
        op.execute(f"DROP TRIGGER trg_object_receipt_history ON public.{table}")
    op.execute("DROP TRIGGER trg_fulfillment_link_history ON public.shipments")
    op.execute("DROP FUNCTION public.medarchive_guard_object_receipt_history()")
    op.execute("DROP FUNCTION public.medarchive_guard_fulfillment_link()")
