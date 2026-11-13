"""Allow immutable archive-fulfilment attribution at courier handover."""

from alembic import op

revision = "0010_archive_fulfillment_handover"
down_revision = "0009_external_org_custody"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The archive operator becomes the fulfiller when the requested cards are
    # handed to the courier. Completion time and result notes remain facts of
    # the later receipt/closure transition and cannot be written at handover.
    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_transition_metadata()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_TABLE_NAME = 'shipments' THEN
            IF OLD.handed_over_at IS NOT NULL
               AND NEW.handed_over_at IS DISTINCT FROM OLD.handed_over_at THEN
                RAISE EXCEPTION 'Время передачи отправления курьеру неизменяемо'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.received_by IS NOT NULL AND (
                NEW.received_by IS DISTINCT FROM OLD.received_by
                OR NEW.received_at IS DISTINCT FROM OLD.received_at
            ) THEN
                RAISE EXCEPTION 'Данные приёмки отправления неизменяемы'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.exception_by IS NOT NULL AND (
                NEW.exception_by IS DISTINCT FROM OLD.exception_by
                OR NEW.exception_at IS DISTINCT FROM OLD.exception_at
                OR NEW.exception_note IS DISTINCT FROM OLD.exception_note
            ) THEN
                RAISE EXCEPTION 'Данные инцидента отправления неизменяемы'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.cancelled_by IS NOT NULL AND (
                NEW.cancelled_by IS DISTINCT FROM OLD.cancelled_by
                OR NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at
            ) THEN
                RAISE EXCEPTION 'Данные отмены отправления неизменяемы'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF TG_TABLE_NAME = 'archive_requests' THEN
            IF OLD.fulfillment_shipment_id IS NOT NULL
               AND NEW.fulfillment_shipment_id IS DISTINCT FROM OLD.fulfillment_shipment_id THEN
                RAISE EXCEPTION 'Связь заявки с отправлением неизменяема'
                    USING ERRCODE = '23514';
            END IF;
            IF (
                NEW.completed_at IS DISTINCT FROM OLD.completed_at
                OR NEW.fulfilled_by IS DISTINCT FROM OLD.fulfilled_by
                OR NEW.result_note IS DISTINCT FROM OLD.result_note
            ) AND NOT (
                (OLD.status = 'new' AND NEW.status = 'not_found')
                OR (OLD.status = 'in_progress' AND NEW.status IN ('completed', 'not_found'))
                OR (
                    OLD.status = 'new' AND NEW.status = 'in_progress'
                    AND NEW.fulfilled_by IS NOT NULL
                    AND NEW.fulfillment_shipment_id IS NOT NULL
                    AND NEW.completed_at IS NOT DISTINCT FROM OLD.completed_at
                    AND NEW.result_note IS NOT DISTINCT FROM OLD.result_note
                )
            ) THEN
                RAISE EXCEPTION 'Результат обработки архивной заявки можно зафиксировать только при её завершении или передаче курьеру'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;
    """)


def downgrade() -> None:
    # Restore the previous guard. This is safe because no existing row needs
    # to be rewritten; only future in-progress attribution is disallowed.
    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_transition_metadata()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_TABLE_NAME = 'shipments' THEN
            IF OLD.handed_over_at IS NOT NULL
               AND NEW.handed_over_at IS DISTINCT FROM OLD.handed_over_at THEN
                RAISE EXCEPTION 'Время передачи отправления курьеру неизменяемо'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.received_by IS NOT NULL AND (
                NEW.received_by IS DISTINCT FROM OLD.received_by
                OR NEW.received_at IS DISTINCT FROM OLD.received_at
            ) THEN
                RAISE EXCEPTION 'Данные приёмки отправления неизменяемы'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.exception_by IS NOT NULL AND (
                NEW.exception_by IS DISTINCT FROM OLD.exception_by
                OR NEW.exception_at IS DISTINCT FROM OLD.exception_at
                OR NEW.exception_note IS DISTINCT FROM OLD.exception_note
            ) THEN
                RAISE EXCEPTION 'Данные инцидента отправления неизменяемы'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.cancelled_by IS NOT NULL AND (
                NEW.cancelled_by IS DISTINCT FROM OLD.cancelled_by
                OR NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at
            ) THEN
                RAISE EXCEPTION 'Данные отмены отправления неизменяемы'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF TG_TABLE_NAME = 'archive_requests' THEN
            IF OLD.fulfillment_shipment_id IS NOT NULL
               AND NEW.fulfillment_shipment_id IS DISTINCT FROM OLD.fulfillment_shipment_id THEN
                RAISE EXCEPTION 'Связь заявки с отправлением неизменяема'
                    USING ERRCODE = '23514';
            END IF;
            IF (
                NEW.completed_at IS DISTINCT FROM OLD.completed_at
                OR NEW.fulfilled_by IS DISTINCT FROM OLD.fulfilled_by
                OR NEW.result_note IS DISTINCT FROM OLD.result_note
            ) AND NOT (
                (OLD.status = 'new' AND NEW.status = 'not_found')
                OR (OLD.status = 'in_progress' AND NEW.status IN ('completed', 'not_found'))
            ) THEN
                RAISE EXCEPTION 'Результат обработки архивной заявки можно зафиксировать только при её завершении'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
