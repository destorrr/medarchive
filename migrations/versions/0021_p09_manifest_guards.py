"""E4.4 connect P09 manifest and receipt checks to affected relations.

The transitional gate recognizes target parents only. Legacy shipment
guards remain installed until E4.5; ordinary shipments and terminal
historical corrections need the coupled switch.
"""
from alembic import op

revision = "0021_p09_manifest_guards"
down_revision = "0020_request_guard_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.medarchive_check_p09_manifest_deferred()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $body$
        DECLARE
            shipment_key uuid;
            parent_key uuid;
            parent_status text;
            shipment_status text;
        BEGIN
            IF TG_TABLE_NAME = 'archive_requests' THEN
                parent_key := NEW.id;
            ELSIF TG_TABLE_NAME = 'archive_request_items' THEN
                parent_key := NEW.request_id;
            ELSIF TG_TABLE_NAME = 'shipments' THEN
                shipment_key := NEW.id;
            ELSE
                IF TG_OP = 'DELETE' THEN
                    shipment_key := OLD.shipment_id;
                ELSE
                    shipment_key := NEW.shipment_id;
                END IF;
            END IF;
            IF parent_key IS NOT NULL THEN
                SELECT id INTO shipment_key FROM public.shipments
                WHERE request_id = parent_key;
            ELSIF shipment_key IS NOT NULL THEN
                SELECT request_id INTO parent_key FROM public.shipments
                WHERE id = shipment_key;
            END IF;
            IF parent_key IS NULL OR shipment_key IS NULL THEN
                RETURN NULL;
            END IF;
            SELECT status INTO parent_status FROM public.archive_requests
            WHERE id = parent_key FOR UPDATE;
            IF parent_status NOT IN ('processing', 'completed', 'cancelled')
               OR NOT EXISTS (SELECT 1 FROM public.archive_request_items
                              WHERE request_id = parent_key) THEN
                RETURN NULL;
            END IF;
            SELECT status INTO shipment_status FROM public.shipments
            WHERE id = shipment_key FOR UPDATE;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            IF shipment_status = 'draft' THEN
                IF parent_status <> 'processing' THEN
                    RAISE EXCEPTION 'P09 draft requires processing parent'
                        USING ERRCODE = '23514';
                END IF;
                PERFORM public.medarchive_validate_target_manifest(shipment_key);
            END IF;
            PERFORM public.medarchive_validate_target_receipts(shipment_key);
            IF (shipment_status = 'received' AND parent_status <> 'completed')
               OR (shipment_status IN ('in_transit', 'partially_received')
                   AND parent_status <> 'processing')
               OR (shipment_status = 'cancelled' AND parent_status <> 'cancelled')
               OR EXISTS (
                   SELECT 1 FROM public.archive_request_items i
                   WHERE i.request_id = parent_key
                     AND i.status IN ('issued_pending_receipt', 'completed')
                     AND NOT EXISTS (
                         SELECT 1 FROM public.shipment_cards sc
                         WHERE sc.shipment_id = shipment_key
                           AND sc.request_item_id = i.id AND sc.card_id = i.card_id
                     )
               ) THEN
                RAISE EXCEPTION 'P09 fulfillment conflicts with parent or items'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $body$
    """)
    for table in ("archive_requests", "archive_request_items", "shipments",
                  "shipment_cards", "shipment_boxes"):
        events = "INSERT OR UPDATE OR DELETE" if table in (
            "shipment_cards", "shipment_boxes") else "INSERT OR UPDATE"
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER trg_p09_manifest_consistency
            AFTER {events} ON public.{table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.medarchive_check_p09_manifest_deferred()
        """)
    op.execute("REVOKE ALL ON FUNCTION public.medarchive_check_p09_manifest_deferred() FROM PUBLIC")


def downgrade() -> None:
    for table in ("archive_requests", "archive_request_items", "shipments",
                  "shipment_cards", "shipment_boxes"):
        op.execute(f"DROP TRIGGER trg_p09_manifest_consistency ON public.{table}")
    op.execute("DROP FUNCTION public.medarchive_check_p09_manifest_deferred()")
