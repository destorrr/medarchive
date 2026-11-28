"""Candidate E4 per-object receipt and transport pointer validation.

Installed by revision 0016; activation requires the coupled migration. The caller must lock the
shipment, parent (P09), manifest and affected objects. No legacy receipt
timestamps are substituted for missing per-object facts.
"""

TARGET_RECEIPT_SQL = """
CREATE FUNCTION public.medarchive_validate_target_receipts(p_shipment_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $body$
DECLARE
    source_kind text;
    target_kind text;
    ship record;
    total_count bigint;
    receipt_count bigint;
BEGIN
    SELECT s.*, src.kind AS source_kind, dst.kind AS target_kind
      INTO ship
    FROM public.shipments s
    JOIN public.locations src ON src.id = s.from_location_id
    JOIN public.locations dst ON dst.id = s.to_location_id
    WHERE s.id = p_shipment_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Target shipment does not exist' USING ERRCODE = '23514';
    END IF;
    source_kind := ship.source_kind;
    target_kind := ship.target_kind;

    IF source_kind IN ('branch', 'central_office') AND target_kind = 'archive' THEN
        IF ship.request_id IS NOT NULL OR EXISTS (
            SELECT 1 FROM public.shipment_cards WHERE shipment_id = p_shipment_id
        ) THEN
            RAISE EXCEPTION 'Invalid ordinary manifest' USING ERRCODE = '23514';
        END IF;
        SELECT count(*), count(received_at) INTO total_count, receipt_count
        FROM public.shipment_boxes WHERE shipment_id = p_shipment_id;
        IF EXISTS (
            SELECT 1 FROM public.shipment_boxes sb
            JOIN public.boxes b ON b.id = sb.box_id
            WHERE sb.shipment_id = p_shipment_id AND (
                (sb.received_at IS NULL) <> (sb.received_by IS NULL)
                OR (sb.received_at IS NOT NULL AND
                    (ship.handed_over_at IS NULL OR sb.received_at < ship.handed_over_at))
                OR (ship.handed_over_at IS NOT NULL AND sb.received_at IS NULL
                    AND (b.current_shipment_id IS DISTINCT FROM p_shipment_id
                         OR b.current_location_id IS NOT NULL))
            )
        ) THEN
            RAISE EXCEPTION 'Invalid box receipt or transit pointer' USING ERRCODE = '23514';
        END IF;
        -- Received boxes may subsequently move; do not infer their present
        -- location from a historical receipt. Ordinary closure also depends
        -- on discrepancies, which are checked in the coupled guard.
    ELSIF source_kind = 'archive' AND target_kind = 'central_office' THEN
        IF ship.request_id IS NULL OR EXISTS (
            SELECT 1 FROM public.shipment_boxes WHERE shipment_id = p_shipment_id
        ) THEN
            RAISE EXCEPTION 'Invalid P09 manifest' USING ERRCODE = '23514';
        END IF;
        SELECT count(*), count(received_at) INTO total_count, receipt_count
        FROM public.shipment_cards WHERE shipment_id = p_shipment_id;
        IF EXISTS (
            SELECT 1 FROM public.shipment_cards sc
            JOIN public.cards c ON c.id = sc.card_id
            LEFT JOIN public.archive_request_items i ON i.id = sc.request_item_id
            WHERE sc.shipment_id = p_shipment_id AND (
                sc.box_id IS NOT NULL OR i.id IS NULL
                OR i.request_id IS DISTINCT FROM ship.request_id
                OR i.card_id IS DISTINCT FROM sc.card_id
                OR (sc.received_at IS NULL) <> (sc.received_by IS NULL)
                OR (sc.received_at IS NOT NULL AND (
                    ship.handed_over_at IS NULL OR sc.received_at < ship.handed_over_at
                    OR i.status <> 'completed'))
                OR (ship.handed_over_at IS NOT NULL AND sc.received_at IS NULL AND (
                    i.status <> 'issued_pending_receipt' OR NOT i.reservation_active
                    OR c.current_shipment_id IS DISTINCT FROM p_shipment_id
                    OR c.current_location_id IS NOT NULL))
            )
        ) THEN
            RAISE EXCEPTION 'Invalid P09 receipt or transit pointer' USING ERRCODE = '23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'Unsupported target transport route' USING ERRCODE = '23514';
    END IF;

    IF ship.handed_over_at IS NULL THEN
        IF receipt_count <> 0 OR ship.status NOT IN ('draft', 'cancelled') THEN
            RAISE EXCEPTION 'Receipt requires handover' USING ERRCODE = '23514';
        END IF;
        RETURN;
    END IF;
    IF total_count = 0 OR ship.status IN ('draft', 'cancelled')
       OR (ship.status = 'received' AND receipt_count <> total_count)
       OR (source_kind = 'archive' AND (
           (ship.status = 'in_transit' AND receipt_count <> 0)
           OR (ship.status = 'partially_received' AND
               (receipt_count = 0 OR receipt_count = total_count))
           OR ship.status NOT IN ('in_transit', 'partially_received', 'received')
       )) THEN
        RAISE EXCEPTION 'Receipt progress conflicts with shipment status' USING ERRCODE = '23514';
    END IF;
END;
$body$
"""
