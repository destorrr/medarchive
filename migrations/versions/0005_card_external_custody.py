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
            (custody_status = 'available' AND external_since IS NULL AND external_organization IS NULL)
            OR (
                custody_status <> 'available'
                AND external_since IS NOT NULL
                AND NULLIF(btrim(external_organization), '') IS NOT NULL
            )
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

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_card_external_custody_update()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        location_kind text;
    BEGIN
        IF NEW.custody_status <> 'available' THEN
            SELECT kind INTO location_kind FROM locations WHERE id = NEW.current_location_id;
            IF location_kind IS DISTINCT FROM 'central_office' OR NEW.current_shipment_id IS NOT NULL THEN
                RAISE EXCEPTION 'Внешнее состояние допустимо только для карты на балансе ЦО'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM shipment_cards sc
                JOIN shipments s ON s.id = sc.shipment_id
                WHERE sc.card_id = NEW.id AND s.status IN ('draft', 'in_transit')
            ) OR EXISTS (
                SELECT 1 FROM box_card_memberships m
                WHERE m.card_id = NEW.id AND m.removed_at IS NULL
            ) THEN
                RAISE EXCEPTION 'Нельзя передать во внешнюю организацию карту в коробке или активном отправлении'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_card_external_custody_update
    BEFORE UPDATE OF custody_status, external_organization, external_since, current_location_id, current_shipment_id
    ON cards FOR EACH ROW EXECUTE FUNCTION medarchive_guard_card_external_custody_update()
    """)


    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_external_card_box_membership()
    RETURNS trigger LANGUAGE plpgsql AS $
    BEGIN
        IF EXISTS (
            SELECT 1 FROM cards
            WHERE id = NEW.card_id AND custody_status <> 'available'
        ) THEN
            RAISE EXCEPTION 'Нельзя упаковать карту, переданную во внешнюю организацию'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $;
    """)
    op.execute("""
    CREATE TRIGGER trg_external_card_box_membership_guard
    BEFORE INSERT OR UPDATE ON box_card_memberships
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_external_card_box_membership()
    """)

    # Prevent a draft route change from bypassing the no-box rule after a box
    # manifest row was already inserted.
    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_archive_central_route_manifest()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM locations src, locations dst
            WHERE src.id = NEW.from_location_id AND dst.id = NEW.to_location_id
              AND src.kind = 'archive' AND dst.kind = 'central_office'
        ) AND (
            EXISTS (SELECT 1 FROM shipment_boxes WHERE shipment_id = NEW.id)
            OR EXISTS (
                SELECT 1 FROM shipment_cards
                WHERE shipment_id = NEW.id AND box_id IS NOT NULL
            )
        ) THEN
            RAISE EXCEPTION 'Отправление архив → ЦО не может содержать коробки'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_archive_central_route_manifest_guard
    BEFORE UPDATE OF from_location_id, to_location_id ON shipments
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_archive_central_route_manifest()
    """)


def downgrade() -> None:
    raise RuntimeError("Card custody migration is intentionally irreversible; recreate a disposable DB")
