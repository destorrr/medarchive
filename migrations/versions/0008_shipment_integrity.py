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

    # The old composite UNIQUE index added no uniqueness because id is
    # already the boxes primary key. Keep the useful lookup path, but model it
    # honestly as a non-unique partial index.
    op.execute("DROP INDEX IF EXISTS uq_box_one_active_shipment")
    op.execute("""
        CREATE INDEX ix_boxes_current_shipment
        ON boxes(current_shipment_id)
        WHERE current_shipment_id IS NOT NULL
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


    # Location changes are only valid as part of a shipment handover/receipt.
    # This closes direct-SQL teleportation and verifies that an object starts
    # from the shipment origin and arrives at its declared destination.
    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_object_movement()
    RETURNS trigger LANGUAGE plpgsql AS $
    DECLARE
        expected_location_id uuid;
    BEGIN
        IF OLD.current_shipment_id IS NULL
           AND NEW.current_shipment_id IS NULL THEN
            IF NEW.current_location_id IS DISTINCT FROM OLD.current_location_id THEN
                RAISE EXCEPTION
                    'Местонахождение объекта можно менять только через отправление'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.current_shipment_id IS NULL
              AND NEW.current_shipment_id IS NOT NULL THEN
            SELECT from_location_id INTO expected_location_id
            FROM shipments WHERE id = NEW.current_shipment_id;
            IF OLD.current_location_id IS DISTINCT FROM expected_location_id THEN
                RAISE EXCEPTION
                    'Отправление должно начинаться из фактической локации объекта'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.current_shipment_id IS NOT NULL
              AND NEW.current_shipment_id IS NULL THEN
            SELECT to_location_id INTO expected_location_id
            FROM shipments WHERE id = OLD.current_shipment_id;
            IF NEW.current_location_id IS DISTINCT FROM expected_location_id THEN
                RAISE EXCEPTION
                    'Объект должен прибыть в локацию получателя отправления'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.current_shipment_id IS DISTINCT FROM NEW.current_shipment_id THEN
            RAISE EXCEPTION
                'Нельзя напрямую переключать объект между отправлениями'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $;
    """)
    for table in ("cards", "boxes"):
        op.execute(f"""
        CREATE TRIGGER trg_{table}_movement_guard
        BEFORE UPDATE OF current_location_id, current_shipment_id ON {table}
        FOR EACH ROW EXECUTE FUNCTION medarchive_guard_object_movement()
        """)


    # New cards and boxes enter the system at a physical location; they cannot
    # be inserted already attached to a shipment. A transit pointer written by
    # an UPDATE must resolve to a handed-over (or exceptional) shipment.
    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_transit_object_insert()
    RETURNS trigger LANGUAGE plpgsql AS $
    BEGIN
        IF NEW.current_shipment_id IS NOT NULL THEN
            RAISE EXCEPTION 'Нельзя создавать объект сразу в составе отправления'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $;
    """)
    for table in ("cards", "boxes"):
        op.execute(f"""
        CREATE TRIGGER trg_{table}_transit_insert_guard
        BEFORE INSERT ON {table}
        FOR EACH ROW EXECUTE FUNCTION medarchive_guard_transit_object_insert()
        """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_current_shipment_status()
    RETURNS trigger LANGUAGE plpgsql AS $
    DECLARE
        shipment_status text;
    BEGIN
        IF NEW.current_shipment_id IS NULL THEN
            RETURN NULL;
        END IF;
        SELECT status INTO shipment_status
        FROM shipments WHERE id = NEW.current_shipment_id;
        IF shipment_status IS NULL OR shipment_status NOT IN ('in_transit', 'exception') THEN
            RAISE EXCEPTION
                'Объект может числиться в отправлении только после передачи курьеру'
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $;
    """)
    for table in ("cards", "boxes"):
        op.execute(f"""
        CREATE CONSTRAINT TRIGGER trg_{table}_current_shipment_status
        AFTER UPDATE ON {table}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION medarchive_guard_current_shipment_status()
        """)


def downgrade() -> None:
    raise RuntimeError("Shipment integrity guards are intentionally irreversible; recreate a disposable DB")
