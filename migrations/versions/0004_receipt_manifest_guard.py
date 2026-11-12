"""Validate the full shipment manifest at receipt even within a single transaction."""

from alembic import op

revision = "0004_receipt_manifest_guard"
down_revision = "0003_card_placements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_assert_received_manifest()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        sid uuid;
        shipment_status text;
        target_id uuid;
        source_kind text;
        target_kind text;
    BEGIN
        IF TG_TABLE_NAME = 'shipments' THEN
            sid := NEW.id;
        ELSIF TG_TABLE_NAME IN ('shipment_boxes', 'shipment_cards') THEN
            sid := CASE WHEN TG_OP = 'DELETE' THEN OLD.shipment_id ELSE NEW.shipment_id END;
        ELSIF TG_TABLE_NAME = 'boxes' THEN
            sid := COALESCE(NEW.current_shipment_id, OLD.current_shipment_id);
        ELSE
            sid := COALESCE(NEW.current_shipment_id, OLD.current_shipment_id);
        END IF;
        IF sid IS NULL THEN RETURN NULL; END IF;

        SELECT s.status, s.to_location_id, src.kind, dst.kind
          INTO shipment_status, target_id, source_kind, target_kind
          FROM shipments s
          JOIN locations src ON src.id = s.from_location_id
          JOIN locations dst ON dst.id = s.to_location_id
         WHERE s.id = sid;
        IF shipment_status IS DISTINCT FROM 'received' THEN
            RETURN NULL;
        END IF;

        IF NOT EXISTS (SELECT 1 FROM shipment_boxes WHERE shipment_id = sid)
           AND NOT EXISTS (SELECT 1 FROM shipment_cards WHERE shipment_id = sid) THEN
            RAISE EXCEPTION 'Нельзя принять пустое отправление'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM shipment_boxes sb
            WHERE sb.shipment_id = sid
              AND NOT EXISTS (
                SELECT 1 FROM box_cycles bc
                JOIN box_card_memberships m ON m.box_cycle_id = bc.id
                WHERE bc.box_id = sb.box_id AND bc.ended_at IS NULL AND m.removed_at IS NULL
              )
        ) THEN
            RAISE EXCEPTION 'Нельзя принять пустую коробку'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM shipment_boxes sb
            JOIN box_cycles bc ON bc.box_id = sb.box_id AND bc.ended_at IS NULL
            JOIN box_card_memberships m ON m.box_cycle_id = bc.id AND m.removed_at IS NULL
            LEFT JOIN shipment_cards sc
              ON sc.shipment_id = sid AND sc.card_id = m.card_id AND sc.box_id = sb.box_id
            WHERE sb.shipment_id = sid AND sc.card_id IS NULL
        ) THEN
            RAISE EXCEPTION 'В манифесте отсутствует карта из принятой коробки'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM shipment_cards sc
            WHERE sc.shipment_id = sid AND (
                (sc.box_id IS NULL AND EXISTS (
                    SELECT 1 FROM box_card_memberships m
                    JOIN box_cycles bc ON bc.id = m.box_cycle_id
                    WHERE m.card_id = sc.card_id AND m.removed_at IS NULL AND bc.ended_at IS NULL
                ))
                OR
                (sc.box_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM box_card_memberships m
                    JOIN box_cycles bc ON bc.id = m.box_cycle_id
                    WHERE m.card_id = sc.card_id AND m.removed_at IS NULL
                      AND bc.ended_at IS NULL AND bc.box_id = sc.box_id
                ))
            )
        ) THEN
            RAISE EXCEPTION 'Состав манифеста не совпадает с фактическим составом коробок'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM shipment_boxes sb JOIN boxes b ON b.id = sb.box_id
            WHERE sb.shipment_id = sid
              AND (b.current_location_id IS DISTINCT FROM target_id OR b.current_shipment_id IS NOT NULL)
        ) OR EXISTS (
            SELECT 1 FROM shipment_cards sc JOIN cards c ON c.id = sc.card_id
            WHERE sc.shipment_id = sid
              AND (c.current_location_id IS DISTINCT FROM target_id OR c.current_shipment_id IS NOT NULL)
        ) THEN
            RAISE EXCEPTION 'Весь состав отправления должен находиться в локации получателя'
                USING ERRCODE = '23514';
        END IF;
        IF source_kind = 'archive' AND target_kind = 'central_office' AND EXISTS (
            SELECT 1 FROM shipment_cards sc
            LEFT JOIN archive_requests ar
              ON ar.card_id = sc.card_id
             AND ar.fulfillment_shipment_id = sid
             AND ar.status = 'completed'
            WHERE sc.shipment_id = sid AND ar.id IS NULL
        ) THEN
            RAISE EXCEPTION 'Заявка ЦО должна завершиться вместе с приёмкой отправления'
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$;
    """)
    for table, trigger_name in (
        ("shipments", "trg_shipments_received_manifest"),
        ("shipment_boxes", "trg_shipment_boxes_received_manifest"),
        ("shipment_cards", "trg_shipment_cards_received_manifest"),
        ("cards", "trg_cards_received_manifest"),
        ("boxes", "trg_boxes_received_manifest"),
    ):
        event = "AFTER INSERT OR UPDATE" if table == "shipments" else (
            "AFTER INSERT OR UPDATE OR DELETE" if table in ("shipment_boxes", "shipment_cards")
            else "AFTER UPDATE"
        )
        op.execute(f"""
        CREATE CONSTRAINT TRIGGER {trigger_name}
        {event} ON {table}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION medarchive_assert_received_manifest()
        """)


def downgrade() -> None:
    raise RuntimeError("Receipt manifest guard migration is intentionally irreversible")
