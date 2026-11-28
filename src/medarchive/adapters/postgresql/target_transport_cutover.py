"""Transactional transport DDL for E4.5 rehearsal, not an Alembic head.

Only use with the rest of the target parent/manifest schema switch in the
same maintenance transaction. No production entry point is provided here.
"""
from sqlalchemy import text


async def install_transport_constraints(connection):
    """Replace pointer checks and status constraints in one transaction.

    The caller owns the transaction and must complete the parent and
    manifest switch before commit. Existing box/card consistency stays.
    """
    if not connection.in_transaction():
        raise RuntimeError("Transport switch requires an enclosing transaction")
    async with connection.begin_nested():
        await connection.execute(text("""
            LOCK TABLE public.archive_requests, public.archive_request_items,
                public.box_card_memberships, public.box_cycles, public.boxes,
                public.cards, public.shipment_boxes, public.shipment_cards,
                public.shipments IN ACCESS EXCLUSIVE MODE
        """))
        await connection.execute(text("""
            ALTER TABLE public.shipments
                DROP CONSTRAINT shipments_status_check,
                DROP CONSTRAINT ck_shipment_handover_time,
                ADD CONSTRAINT shipments_status_check CHECK (
                    status IN ('draft','in_transit','partially_received',
                               'received','exception','cancelled')
                    AND (status <> 'partially_received' OR request_id IS NOT NULL)
                ),
                ADD CONSTRAINT ck_shipment_handover_time CHECK (
                    (status IN ('draft','cancelled') AND handed_over_at IS NULL)
                    OR (status IN ('in_transit','partially_received','received','exception')
                        AND handed_over_at IS NOT NULL)
                )
        """))
        await connection.execute(text("""
            ALTER TABLE public.cards DROP CONSTRAINT fk_card_shipment_manifest
        """))
        for table in ("cards", "boxes"):
            for suffix in ("previous_shipment_pointer", "current_shipment_status"):
                await connection.execute(text(
                    f"DROP TRIGGER trg_{table}_{suffix} ON public.{table}"
                ))
            await connection.execute(text(f"""
                CREATE CONSTRAINT TRIGGER trg_{table}_target_transport_pointer
                AFTER UPDATE ON public.{table}
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                EXECUTE FUNCTION public.medarchive_guard_target_transport_pointer()
            """))
        # Existing pointers must satisfy the replacement before leaving
        # the savepoint; no implicit repair or inferred receipt.
        await connection.execute(text("""
            DO $guard$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM public.cards c
                    WHERE c.current_shipment_id IS NOT NULL
                      AND NOT public.medarchive_target_transport_object(
                          'cards', c.id, c.current_shipment_id, false)
                ) OR EXISTS (
                    SELECT 1 FROM public.boxes b
                    WHERE b.current_shipment_id IS NOT NULL
                      AND NOT public.medarchive_target_transport_object(
                          'boxes', b.id, b.current_shipment_id, false)
                ) THEN
                    RAISE EXCEPTION 'Unresolved target transport pointers'
                        USING ERRCODE = '23514';
                END IF;
            END
            $guard$
        """))
