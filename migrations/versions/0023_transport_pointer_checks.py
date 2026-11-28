"""Target transport pointer checks for the coupled E4.5 switch.

Cards retain their physical transit pointer while boxed; membership rather
than a redundant shipment_cards row proves ordinary manifest membership.
The existing box/card location consistency guard must remain active.
"""
from alembic import op

revision = "0023_transport_pointer_checks"
down_revision = "0022_transport_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.medarchive_target_transport_object(
            p_kind text, p_object uuid, p_shipment uuid, p_received boolean
        ) RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER
        SET search_path = pg_catalog, pg_temp
        AS $body$
        SELECT EXISTS (
            SELECT 1 FROM public.shipments s
            JOIN public.locations src ON src.id = s.from_location_id
            JOIN public.locations dst ON dst.id = s.to_location_id
            WHERE s.id = p_shipment AND s.handed_over_at IS NOT NULL
              AND (
                (p_kind = 'boxes' AND src.kind IN ('branch','central_office')
                 AND dst.kind = 'archive' AND EXISTS (
                    SELECT 1 FROM public.shipment_boxes sb
                    WHERE sb.shipment_id = s.id AND sb.box_id = p_object
                      AND (sb.received_at IS NOT NULL) = p_received
                 ))
                OR (p_kind = 'cards' AND src.kind IN ('branch','central_office')
                    AND dst.kind = 'archive' AND EXISTS (
                    SELECT 1 FROM public.box_card_memberships m
                    JOIN public.box_cycles cy ON cy.id = m.box_cycle_id
                    JOIN public.shipment_boxes sb ON sb.box_id = cy.box_id
                    WHERE m.card_id = p_object AND m.removed_at IS NULL
                      AND cy.ended_at IS NULL AND sb.shipment_id = s.id
                      AND (sb.received_at IS NOT NULL) = p_received
                 ))
                OR (p_kind = 'cards' AND src.kind = 'archive'
                    AND dst.kind = 'central_office' AND EXISTS (
                    SELECT 1 FROM public.shipment_cards sc
                    JOIN public.archive_request_items i ON i.id = sc.request_item_id
                    WHERE sc.shipment_id = s.id AND sc.card_id = p_object
                      AND sc.box_id IS NULL AND i.card_id = p_object
                      AND i.request_id = s.request_id
                      AND (sc.received_at IS NOT NULL) = p_received
                 ))
              )
        )
        $body$
    """)
    op.execute("""
        CREATE FUNCTION public.medarchive_guard_target_transport_pointer()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $body$
        DECLARE
            active_shipment uuid;
            active_status text;
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.current_shipment_id IS NOT NULL
               AND OLD.current_shipment_id IS DISTINCT FROM NEW.current_shipment_id
               AND NOT public.medarchive_target_transport_object(
                   TG_TABLE_NAME, OLD.id, OLD.current_shipment_id, true
               ) THEN
                RAISE EXCEPTION 'Object requires its own receipt before leaving transit'
                    USING ERRCODE = '23514';
            END IF;
            IF TG_TABLE_NAME = 'cards' THEN
                SELECT current_shipment_id INTO active_shipment
                FROM public.cards WHERE id = NEW.id;
            ELSE
                SELECT current_shipment_id INTO active_shipment
                FROM public.boxes WHERE id = NEW.id;
            END IF;
            IF active_shipment IS NOT NULL THEN
                SELECT status INTO active_status FROM public.shipments
                WHERE id = active_shipment FOR UPDATE;
                IF active_status NOT IN ('in_transit','partially_received','exception')
                   OR NOT public.medarchive_target_transport_object(
                       TG_TABLE_NAME, NEW.id, active_shipment, false
                   ) THEN
                    RAISE EXCEPTION 'Transit pointer requires an outstanding manifest object'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NULL;
        END;
        $body$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.medarchive_target_transport_object(text,uuid,uuid,boolean) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.medarchive_guard_target_transport_pointer() FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION public.medarchive_guard_target_transport_pointer()")
    op.execute("DROP FUNCTION public.medarchive_target_transport_object(text,uuid,uuid,boolean)")
