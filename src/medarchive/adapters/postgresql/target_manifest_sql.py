"""E4 SQL manifest validator for the coupled cutover.

Not installed by an Alembic revision yet. Legacy workflows remain unchanged.
The validator is called at the final manifest boundary, after the parent and
shipment are locked and before handover changes extracted item statuses.
"""

TARGET_MANIFEST_SQL = """
CREATE FUNCTION public.medarchive_validate_target_manifest(p_shipment_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $body$
DECLARE
    source_kind text;
    target_kind text;
    parent_id uuid;
BEGIN
    SELECT src.kind, dst.kind, s.request_id
      INTO source_kind, target_kind, parent_id
    FROM public.shipments s
    JOIN public.locations src ON src.id = s.from_location_id
    JOIN public.locations dst ON dst.id = s.to_location_id
    WHERE s.id = p_shipment_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Target shipment does not exist' USING ERRCODE = '23514';
    END IF;

    IF source_kind IN ('branch', 'central_office') AND target_kind = 'archive' THEN
        IF parent_id IS NOT NULL
           OR EXISTS (SELECT 1 FROM public.shipment_cards sc
                      WHERE sc.shipment_id = p_shipment_id)
           OR NOT EXISTS (SELECT 1 FROM public.shipment_boxes sb
                          WHERE sb.shipment_id = p_shipment_id) THEN
            RAISE EXCEPTION 'Ordinary shipment requires a nonempty box-only manifest'
                USING ERRCODE = '23514';
        END IF;
        RETURN;
    END IF;

    IF source_kind <> 'archive' OR target_kind <> 'central_office' THEN
        RAISE EXCEPTION 'Unsupported target transport route' USING ERRCODE = '23514';
    END IF;
    IF parent_id IS NULL
       OR EXISTS (SELECT 1 FROM public.shipment_boxes sb
                  WHERE sb.shipment_id = p_shipment_id)
       OR NOT EXISTS (SELECT 1 FROM public.shipment_cards sc
                      WHERE sc.shipment_id = p_shipment_id) THEN
        RAISE EXCEPTION 'P09 requires one parent and a nonempty card-only manifest'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.archive_request_items i
        WHERE i.request_id = parent_id
          AND (i.status NOT IN ('extracted', 'not_found', 'removed_by_archive')
               OR NOT i.reservation_active)
    ) OR EXISTS (
        SELECT 1 FROM public.shipment_cards sc
        LEFT JOIN public.archive_request_items i ON i.id = sc.request_item_id
        WHERE sc.shipment_id = p_shipment_id
          AND (sc.box_id IS NOT NULL OR i.id IS NULL
               OR i.request_id IS DISTINCT FROM parent_id
               OR i.card_id IS DISTINCT FROM sc.card_id
               OR i.status <> 'extracted' OR NOT i.reservation_active)
    ) OR EXISTS (
        SELECT 1 FROM public.archive_request_items i
        WHERE i.request_id = parent_id AND i.status = 'extracted'
          AND NOT EXISTS (
              SELECT 1 FROM public.shipment_cards sc
              WHERE sc.shipment_id = p_shipment_id
                AND sc.request_item_id = i.id AND sc.card_id = i.card_id
          )
    ) THEN
        RAISE EXCEPTION 'P09 manifest must equal all extracted positions of a fully processed parent'
            USING ERRCODE = '23514';
    END IF;
END;
$body$
"""
