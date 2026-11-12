"""Close shipment pointer reassignment gaps and preserve exception metadata on receipt."""

from alembic import op

revision = "0008_shipment_integrity"
down_revision = "0007_transition_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Exception details are historical facts. Keep them when an exceptional
    # shipment is eventually received instead of clearing immutable metadata.
    op.execute("""
        ALTER TABLE shipments
        DROP CONSTRAINT ck_shipment_exception_fields
    """)
    op.execute("""
        ALTER TABLE shipments
        ADD CONSTRAINT ck_shipment_exception_fields CHECK (
            (
                status = 'exception'
                AND exception_by IS NOT NULL
                AND exception_at IS NOT NULL
                AND NULLIF(btrim(exception_note), '') IS NOT NULL
            )
            OR (
                status = 'received'
                AND (
                    (
                        exception_by IS NULL
                        AND exception_at IS NULL
                        AND exception_note IS NULL
                    )
                    OR (
                        exception_by IS NOT NULL
                        AND exception_at IS NOT NULL
                        AND NULLIF(btrim(exception_note), '') IS NOT NULL
                    )
                )
            )
            OR (
                status NOT IN ('exception', 'received')
                AND exception_by IS NULL
                AND exception_at IS NULL
                AND exception_note IS NULL
            )
        )
    """)

    # The current_shipment_id pointer is the authoritative indication that an
    # object is in transit. A pointer may be cleared only in the same transaction
    # that completes receipt of its previous shipment. This deferred check also
    # covers direct SQL updates and pointer reassignment to another shipment.
    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_previous_shipment_pointer()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        previous_shipment_id uuid;
        previous_status text;
    BEGIN
        IF TG_OP <> 'UPDATE' THEN
            RETURN NULL;
        END IF;

        IF TG_TABLE_NAME = 'cards' THEN
            IF OLD.current_shipment_id IS NULL
               OR OLD.current_shipment_id IS NOT DISTINCT FROM NEW.current_shipment_id THEN
                RETURN NULL;
            END IF;
            previous_shipment_id := OLD.current_shipment_id;
        ELSE
            IF OLD.current_shipment_id IS NULL
               OR OLD.current_shipment_id IS NOT DISTINCT FROM NEW.current_shipment_id THEN
                RETURN NULL;
            END IF;
            previous_shipment_id := OLD.current_shipment_id;
        END IF;

        SELECT status INTO previous_status
        FROM shipments
        WHERE id = previous_shipment_id;

        IF previous_status IS DISTINCT FROM 'received' THEN
            RAISE EXCEPTION
                'Нельзя изменить местонахождение объекта до приёмки предыдущего отправления'
                USING ERRCODE = '23514';
        END IF;

        RETURN NULL;
    END;
    $$;
    """)
    for table in ("cards", "boxes"):
        op.execute(f"""
        CREATE CONSTRAINT TRIGGER trg_{table}_previous_shipment_pointer
        AFTER UPDATE ON {table}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION medarchive_guard_previous_shipment_pointer()
        """)


def downgrade() -> None:
    raise RuntimeError("Shipment integrity guards are intentionally irreversible; recreate a disposable DB")
