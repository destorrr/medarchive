"""Add individual archive card shelf placements."""

from alembic import op

revision = "0003_card_placements"
down_revision = "0002_workflow_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE card_placements (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        card_id uuid NOT NULL REFERENCES cards(id) ON DELETE RESTRICT,
        shelf_id uuid NOT NULL REFERENCES archive_shelves(id) ON DELETE RESTRICT,
        placed_at timestamptz NOT NULL DEFAULT now(),
        placed_by uuid REFERENCES users(id) ON DELETE SET NULL,
        removed_at timestamptz,
        removed_by uuid REFERENCES users(id) ON DELETE SET NULL,
        CONSTRAINT ck_card_placement_dates
            CHECK (removed_at IS NULL OR removed_at >= placed_at)
    )
    """)
    op.execute("""
    CREATE UNIQUE INDEX uq_card_one_active_placement
    ON card_placements(card_id) WHERE removed_at IS NULL
    """)
    op.execute("""
    CREATE INDEX ix_card_placements_shelf
    ON card_placements(shelf_id, placed_at)
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_card_placement()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        location_kind text;
        shelf_active boolean;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Историю размещения карты нельзя удалять'
                USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'UPDATE' AND (
            NEW.card_id IS DISTINCT FROM OLD.card_id
            OR NEW.shelf_id IS DISTINCT FROM OLD.shelf_id
            OR NEW.placed_at IS DISTINCT FROM OLD.placed_at
            OR NEW.placed_by IS DISTINCT FROM OLD.placed_by
            OR (OLD.removed_at IS NOT NULL AND (
                NEW.removed_at IS DISTINCT FROM OLD.removed_at
                OR NEW.removed_by IS DISTINCT FROM OLD.removed_by
            ))
        ) THEN
            RAISE EXCEPTION 'Нельзя изменять исходные данные или повторно открывать размещение карты'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.removed_at IS NULL THEN
            SELECT l.kind INTO location_kind
            FROM cards c JOIN locations l ON l.id = c.current_location_id
            WHERE c.id = NEW.card_id AND c.current_shipment_id IS NULL AND c.status = 'active';
            SELECT is_active INTO shelf_active FROM archive_shelves WHERE id = NEW.shelf_id;
            IF location_kind IS DISTINCT FROM 'archive' OR shelf_active IS DISTINCT FROM true
               OR EXISTS (
                    SELECT 1 FROM box_card_memberships m
                    JOIN box_cycles bc ON bc.id = m.box_cycle_id
                    WHERE m.card_id = NEW.card_id AND m.removed_at IS NULL AND bc.ended_at IS NULL
               ) THEN
                RAISE EXCEPTION 'Размещать отдельно можно только свободную карту из архива на активной полке'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_card_placement_guard
    BEFORE INSERT OR UPDATE OR DELETE ON card_placements
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_card_placement()
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_card_leaving_shelf()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF (NEW.current_location_id IS DISTINCT FROM OLD.current_location_id
            OR NEW.current_shipment_id IS DISTINCT FROM OLD.current_shipment_id
            OR NEW.status IS DISTINCT FROM OLD.status)
           AND EXISTS (
                SELECT 1 FROM card_placements p
                WHERE p.card_id = OLD.id AND p.removed_at IS NULL
           ) THEN
            RAISE EXCEPTION 'Сначала снимите медицинскую карту с архивной полки'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_card_leaving_shelf_guard
    BEFORE UPDATE ON cards FOR EACH ROW
    EXECUTE FUNCTION medarchive_guard_card_leaving_shelf()
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_card_manifest_placement()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM card_placements p
            WHERE p.card_id = NEW.card_id AND p.removed_at IS NULL
        ) THEN
            RAISE EXCEPTION 'Перед отправкой медицинскую карту необходимо снять с архивной полки'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_shipment_card_not_placed
    BEFORE INSERT OR UPDATE ON shipment_cards
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_card_manifest_placement()
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_card_box_membership_placement()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM card_placements p
            WHERE p.card_id = NEW.card_id AND p.removed_at IS NULL
        ) THEN
            RAISE EXCEPTION 'Перед упаковкой медицинскую карту необходимо снять с архивной полки'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_box_membership_card_not_placed
    BEFORE INSERT OR UPDATE ON box_card_memberships
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_card_box_membership_placement()
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_box_placement_card_placements()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.removed_at IS NULL AND EXISTS (
            SELECT 1
            FROM box_cycles bc
            JOIN box_card_memberships m ON m.box_cycle_id = bc.id
            JOIN card_placements cp ON cp.card_id = m.card_id AND cp.removed_at IS NULL
            WHERE bc.box_id = NEW.box_id AND bc.ended_at IS NULL AND m.removed_at IS NULL
        ) THEN
            RAISE EXCEPTION 'Сначала снимите карты с индивидуальных архивных полок'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_box_placement_cards_not_placed
    BEFORE INSERT OR UPDATE ON box_placements
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_box_placement_card_placements()
    """)


def downgrade() -> None:
    raise RuntimeError("Card placement history migration is intentionally irreversible")
