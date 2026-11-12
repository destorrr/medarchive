"""Freeze shipment transition metadata and archive request fulfillment facts."""

from alembic import op

revision = "0007_transition_metadata_immutable"
down_revision = "0006_external_custody_at_other"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.execute("""
    CREATE TRIGGER trg_shipment_transition_metadata_immutable
    BEFORE UPDATE ON shipments
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_transition_metadata()
    """)
    op.execute("""
    CREATE TRIGGER trg_archive_request_transition_metadata_immutable
    BEFORE UPDATE ON archive_requests
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_transition_metadata()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_archive_request_transition_metadata_immutable ON archive_requests")
    op.execute("DROP TRIGGER IF EXISTS trg_shipment_transition_metadata_immutable ON shipments")
    op.execute("DROP FUNCTION IF EXISTS medarchive_guard_transition_metadata()")
