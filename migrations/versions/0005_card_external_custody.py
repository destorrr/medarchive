"""Add external custody state and enforce unboxed archive issuance."""

from alembic import op

revision = "0005_card_external_custody"
down_revision = "0004_receipt_manifest_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE cards
        ADD COLUMN custody_status text NOT NULL DEFAULT 'available'
            CHECK (custody_status IN (
                'available', 'at_court', 'at_insurance', 'at_external_organization'
            )),
        ADD COLUMN external_organization text,
        ADD COLUMN external_comment text,
        ADD COLUMN external_since timestamptz,
        ADD CONSTRAINT ck_card_external_custody_fields CHECK (
            (custody_status = 'available' AND external_since IS NULL)
            OR (custody_status <> 'available' AND external_since IS NOT NULL)
        )
    """)

    # A card on an external hold remains on the Central Office balance, but it
    # cannot be packed or added to an internal shipment until it is returned.
    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_card_external_custody()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        custody text;
    BEGIN
        SELECT custody_status INTO custody FROM cards WHERE id = NEW.card_id;
        IF custody IS DISTINCT FROM 'available' THEN
            RAISE EXCEPTION 'Карта передана во внешнюю организацию и недоступна для внутреннего перемещения'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.box_id IS NOT NULL AND EXISTS (
            SELECT 1
            FROM shipments s
            JOIN locations src ON src.id = s.from_location_id
            JOIN locations dst ON dst.id = s.to_location_id
            WHERE s.id = NEW.shipment_id
              AND src.kind = 'archive' AND dst.kind = 'central_office'
        ) THEN
            RAISE EXCEPTION 'Архив выдаёт карты ЦО только отдельно, без коробок'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_shipment_card_custody_and_archive_box_guard
    BEFORE INSERT OR UPDATE ON shipment_cards
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_card_external_custody()
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_archive_box_issue()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM shipments s
            JOIN locations src ON src.id = s.from_location_id
            JOIN locations dst ON dst.id = s.to_location_id
            WHERE s.id = NEW.shipment_id
              AND src.kind = 'archive' AND dst.kind = 'central_office'
        ) THEN
            RAISE EXCEPTION 'По заявкам ЦО архив отправляет только отдельные карты, без коробок'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_shipment_box_archive_issue_guard
    BEFORE INSERT OR UPDATE ON shipment_boxes
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_archive_box_issue()
    """)


def downgrade() -> None:
    raise RuntimeError("Card custody migration is intentionally irreversible; recreate a disposable DB")
