"""Add PostgreSQL workflow guards and archive request fulfillment linkage."""

from alembic import op

revision = "0002_workflow_guards"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE archive_requests
        ADD COLUMN fulfillment_shipment_id uuid
        REFERENCES shipments(id) ON DELETE RESTRICT
    """)
    op.execute("""
        ALTER TABLE archive_requests
        ADD CONSTRAINT ck_archive_request_fulfillment
        CHECK (
            (status IN ('new', 'not_found', 'cancelled') AND fulfillment_shipment_id IS NULL)
            OR (status IN ('in_progress', 'completed') AND fulfillment_shipment_id IS NOT NULL)
        )
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_shipment_update()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF OLD.status <> 'draft' AND (
            NEW.from_location_id IS DISTINCT FROM OLD.from_location_id OR
            NEW.to_location_id IS DISTINCT FROM OLD.to_location_id OR
            NEW.courier_id IS DISTINCT FROM OLD.courier_id OR
            NEW.issued_by IS DISTINCT FROM OLD.issued_by
        ) THEN
            RAISE EXCEPTION 'Нельзя менять маршрут, курьера или отправителя после передачи'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_shipment_route_immutable
    BEFORE UPDATE ON shipments
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_shipment_update()
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_manifest_mutation()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        sid uuid;
        shipment_status text;
    BEGIN
        sid := CASE WHEN TG_OP = 'DELETE' THEN OLD.shipment_id ELSE NEW.shipment_id END;
        SELECT status INTO shipment_status FROM shipments WHERE id = sid FOR UPDATE;
        IF shipment_status IS NULL OR shipment_status <> 'draft' THEN
            RAISE EXCEPTION 'Состав отправления можно менять только в черновике'
                USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'UPDATE' AND OLD.shipment_id IS DISTINCT FROM NEW.shipment_id THEN
            SELECT status INTO shipment_status FROM shipments WHERE id = OLD.shipment_id FOR UPDATE;
            IF shipment_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'Нельзя переносить позицию из переданного отправления'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END;
    $$;
    """)
    for table in ("shipment_boxes", "shipment_cards"):
        op.execute(f"""
        CREATE TRIGGER trg_{table}_draft_only
        BEFORE INSERT OR UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION medarchive_guard_manifest_mutation()
        """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_box_membership_mutation()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        cycle_id uuid;
        box_id_value uuid;
    BEGIN
        cycle_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.box_cycle_id ELSE NEW.box_cycle_id END;
        SELECT box_id INTO box_id_value FROM box_cycles WHERE id = cycle_id;
        IF box_id_value IS NOT NULL AND (
            EXISTS (SELECT 1 FROM boxes b WHERE b.id = box_id_value AND b.current_shipment_id IS NOT NULL)
            OR EXISTS (
                SELECT 1 FROM shipment_boxes sb
                JOIN shipments s ON s.id = sb.shipment_id
                WHERE sb.box_id = box_id_value AND s.status IN ('draft', 'in_transit')
            )
            OR EXISTS (
                SELECT 1 FROM box_placements p
                WHERE p.box_id = box_id_value AND p.removed_at IS NULL
            )
        ) THEN
            RAISE EXCEPTION 'Состав коробки нельзя менять во время отправления или размещения на полке'
                USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_box_membership_mutation_guard
    BEFORE INSERT OR UPDATE OR DELETE ON box_card_memberships
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_box_membership_mutation()
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_assert_box_card_consistency()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM box_card_memberships m
            JOIN box_cycles bc ON bc.id = m.box_cycle_id
            JOIN boxes b ON b.id = bc.box_id
            JOIN cards c ON c.id = m.card_id
            WHERE m.removed_at IS NULL
              AND (
                bc.ended_at IS NOT NULL
                OR (b.current_location_id IS NOT NULL AND (
                    c.current_location_id IS DISTINCT FROM b.current_location_id
                    OR c.current_shipment_id IS NOT NULL
                ))
                OR (b.current_shipment_id IS NOT NULL AND (
                    c.current_shipment_id IS DISTINCT FROM b.current_shipment_id
                    OR c.current_location_id IS NOT NULL
                ))
              )
        ) THEN
            RAISE EXCEPTION 'Активный состав коробки не совпадает с местонахождением карты или коробки'
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$;
    """)
    for table in ("cards", "boxes", "box_card_memberships", "box_cycles"):
        op.execute(f"""
        CREATE CONSTRAINT TRIGGER trg_{table}_box_card_consistency
        AFTER INSERT OR UPDATE OR DELETE ON {table}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION medarchive_assert_box_card_consistency()
        """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_assert_shipment_consistency()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        sid uuid;
        shipment_status text;
        target_id uuid;
        source_kind text;
        target_kind text;
    BEGIN
        IF TG_TABLE_NAME = 'shipments' THEN
            sid := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        ELSIF TG_TABLE_NAME IN ('shipment_boxes', 'shipment_cards') THEN
            sid := CASE WHEN TG_OP = 'DELETE' THEN OLD.shipment_id ELSE NEW.shipment_id END;
        ELSIF TG_TABLE_NAME = 'boxes' THEN
            sid := COALESCE(NEW.current_shipment_id, OLD.current_shipment_id);
        ELSE
            sid := COALESCE(NEW.current_shipment_id, OLD.current_shipment_id);
        END IF;
        IF sid IS NULL THEN RETURN NULL; END IF;

        SELECT s.status, s.to_location_id, src.kind, dst.kind
          INTO shipment_status, target_id, source_kind, target_kind
          FROM shipments s
          JOIN locations src ON src.id = s.from_location_id
          JOIN locations dst ON dst.id = s.to_location_id
         WHERE s.id = sid;
        IF shipment_status IS NULL OR shipment_status = 'draft' OR shipment_status = 'cancelled' THEN
            RETURN NULL;
        END IF;

        IF shipment_status IN ('in_transit', 'exception') THEN
            IF NOT EXISTS (SELECT 1 FROM shipment_boxes WHERE shipment_id = sid)
               AND NOT EXISTS (SELECT 1 FROM shipment_cards WHERE shipment_id = sid) THEN
                RAISE EXCEPTION 'Переданное отправление не может быть пустым'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM shipment_boxes sb
                JOIN boxes b ON b.id = sb.box_id
                WHERE sb.shipment_id = sid
                  AND (b.current_shipment_id IS DISTINCT FROM sid OR b.current_location_id IS NOT NULL
                       OR b.status <> 'active')
            ) THEN
                RAISE EXCEPTION 'Местонахождение коробки не соответствует отправлению'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM shipment_boxes sb
                WHERE sb.shipment_id = sid
                  AND NOT EXISTS (
                    SELECT 1 FROM box_cycles bc
                    JOIN box_card_memberships m ON m.box_cycle_id = bc.id
                    WHERE bc.box_id = sb.box_id AND bc.ended_at IS NULL AND m.removed_at IS NULL
                  )
            ) THEN
                RAISE EXCEPTION 'В отправлении есть пустая коробка'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM shipment_boxes sb
                JOIN box_cycles bc ON bc.box_id = sb.box_id AND bc.ended_at IS NULL
                JOIN box_card_memberships m ON m.box_cycle_id = bc.id AND m.removed_at IS NULL
                LEFT JOIN shipment_cards sc
                  ON sc.shipment_id = sid AND sc.card_id = m.card_id AND sc.box_id = sb.box_id
                WHERE sb.shipment_id = sid AND sc.card_id IS NULL
            ) THEN
                RAISE EXCEPTION 'В манифесте отсутствует карта из отправляемой коробки'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM shipment_cards sc
                JOIN cards c ON c.id = sc.card_id
                WHERE sc.shipment_id = sid
                  AND (c.current_shipment_id IS DISTINCT FROM sid OR c.current_location_id IS NOT NULL)
            ) THEN
                RAISE EXCEPTION 'Местонахождение карты не соответствует отправлению'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM shipment_cards sc
                WHERE sc.shipment_id = sid AND (
                    (sc.box_id IS NULL AND EXISTS (
                        SELECT 1 FROM box_card_memberships m
                        JOIN box_cycles bc ON bc.id = m.box_cycle_id
                        WHERE m.card_id = sc.card_id AND m.removed_at IS NULL AND bc.ended_at IS NULL
                    ))
                    OR
                    (sc.box_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM box_card_memberships m
                        JOIN box_cycles bc ON bc.id = m.box_cycle_id
                        WHERE m.card_id = sc.card_id AND m.removed_at IS NULL
                          AND bc.ended_at IS NULL AND bc.box_id = sc.box_id
                    ))
                )
            ) THEN
                RAISE EXCEPTION 'Карта указана в манифесте не в соответствии с активным составом коробки'
                    USING ERRCODE = '23514';
            END IF;
            IF source_kind = 'archive' AND target_kind = 'central_office' AND EXISTS (
                SELECT 1 FROM shipment_cards sc
                LEFT JOIN archive_requests ar
                  ON ar.card_id = sc.card_id
                 AND ar.fulfillment_shipment_id = sid
                 AND ar.status = 'in_progress'
                WHERE sc.shipment_id = sid AND ar.id IS NULL
            ) THEN
                RAISE EXCEPTION 'Для каждой карты из архива нужна заявка ЦО, связанная с этим отправлением'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF shipment_status = 'received' THEN
            IF EXISTS (
                SELECT 1 FROM shipment_boxes sb JOIN boxes b ON b.id = sb.box_id
                WHERE sb.shipment_id = sid
                  AND (b.current_location_id IS DISTINCT FROM target_id OR b.current_shipment_id IS NOT NULL)
            ) OR EXISTS (
                SELECT 1 FROM shipment_cards sc JOIN cards c ON c.id = sc.card_id
                WHERE sc.shipment_id = sid
                  AND (c.current_location_id IS DISTINCT FROM target_id OR c.current_shipment_id IS NOT NULL)
            ) THEN
                RAISE EXCEPTION 'Приёмка должна переместить весь состав в локацию получателя'
                    USING ERRCODE = '23514';
            END IF;
            IF source_kind = 'archive' AND target_kind = 'central_office' AND EXISTS (
                SELECT 1 FROM shipment_cards sc
                LEFT JOIN archive_requests ar
                  ON ar.card_id = sc.card_id
                 AND ar.fulfillment_shipment_id = sid
                 AND ar.status = 'completed'
                WHERE sc.shipment_id = sid AND ar.id IS NULL
            ) THEN
                RAISE EXCEPTION 'Заявка ЦО должна завершиться вместе с приёмкой отправления'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NULL;
    END;
    $$;
    """)
    op.execute("""
    CREATE CONSTRAINT TRIGGER trg_shipments_consistency
    AFTER INSERT OR UPDATE ON shipments DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION medarchive_assert_shipment_consistency()
    """)
    for table in ("shipment_boxes", "shipment_cards"):
        op.execute(f"""
        CREATE CONSTRAINT TRIGGER trg_{table}_consistency
        AFTER INSERT OR UPDATE OR DELETE ON {table}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION medarchive_assert_shipment_consistency()
        """)
    for table in ("cards", "boxes"):
        op.execute(f"""
        CREATE CONSTRAINT TRIGGER trg_{table}_shipment_consistency
        AFTER UPDATE ON {table}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION medarchive_assert_shipment_consistency()
        """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_archive_request()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        requester_role text;
        card_location_kind text;
        fulfillment_status text;
    BEGIN
        SELECT role INTO requester_role FROM users WHERE id = NEW.requested_by;
        IF requester_role IS DISTINCT FROM 'central_office' THEN
            RAISE EXCEPTION 'Заявку на выдачу архивной карты может создавать только ЦО'
                USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'INSERT' THEN
            SELECT l.kind INTO card_location_kind
            FROM cards c JOIN locations l ON l.id = c.current_location_id
            WHERE c.id = NEW.card_id AND c.status = 'active';
            IF card_location_kind IS DISTINCT FROM 'archive' OR NEW.status <> 'new'
               OR NEW.fulfillment_shipment_id IS NOT NULL THEN
                RAISE EXCEPTION 'Заявка создаётся только на активную карту, находящуюся в архиве'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            IF NEW.card_id IS DISTINCT FROM OLD.card_id
               OR NEW.requested_by IS DISTINCT FROM OLD.requested_by THEN
                RAISE EXCEPTION 'Нельзя менять карту или автора заявки'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT (
                (OLD.status = 'new' AND NEW.status IN ('in_progress', 'not_found', 'cancelled'))
                OR (OLD.status = 'in_progress' AND NEW.status IN ('completed', 'not_found'))
                OR (OLD.status = NEW.status)
            ) THEN
                RAISE EXCEPTION 'Недопустимый переход статуса архивной заявки'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status IN ('in_progress', 'completed') THEN
                SELECT status INTO fulfillment_status FROM shipments
                WHERE id = NEW.fulfillment_shipment_id
                  AND from_location_id = (SELECT id FROM locations WHERE kind = 'archive')
                  AND to_location_id = (SELECT id FROM locations WHERE kind = 'central_office');
                IF fulfillment_status IS NULL OR
                   (NEW.status = 'in_progress' AND fulfillment_status NOT IN ('draft', 'in_transit')) OR
                   (NEW.status = 'completed' AND fulfillment_status NOT IN ('in_transit', 'received')) THEN
                    RAISE EXCEPTION 'Заявка связана с некорректным отправлением архив → ЦО'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.fulfillment_shipment_id IS NOT NULL THEN
                RAISE EXCEPTION 'Для этого статуса заявке не требуется отправление'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_archive_request_guard
    BEFORE INSERT OR UPDATE ON archive_requests
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_archive_request()
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_archive_placement()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        location_kind text;
        shelf_active boolean;
    BEGIN
        IF TG_OP = 'UPDATE' AND (
            NEW.box_id IS DISTINCT FROM OLD.box_id OR NEW.shelf_id IS DISTINCT FROM OLD.shelf_id
            OR NEW.placed_at IS DISTINCT FROM OLD.placed_at OR NEW.placed_by IS DISTINCT FROM OLD.placed_by
        ) THEN
            RAISE EXCEPTION 'Нельзя изменять исходные данные размещения коробки'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.removed_at IS NULL THEN
            SELECT l.kind INTO location_kind
            FROM boxes b JOIN locations l ON l.id = b.current_location_id
            WHERE b.id = NEW.box_id AND b.current_shipment_id IS NULL AND b.status = 'active';
            SELECT is_active INTO shelf_active FROM archive_shelves WHERE id = NEW.shelf_id;
            IF location_kind IS DISTINCT FROM 'archive' OR shelf_active IS DISTINCT FROM true
               OR NOT EXISTS (
                    SELECT 1 FROM box_cycles bc
                    JOIN box_card_memberships m ON m.box_cycle_id = bc.id
                    WHERE bc.box_id = NEW.box_id AND bc.ended_at IS NULL AND m.removed_at IS NULL
               ) THEN
                RAISE EXCEPTION 'Размещать можно только активную коробку, находящуюся в архиве, на активной полке'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_archive_placement_guard
    BEFORE INSERT OR UPDATE ON box_placements
    FOR EACH ROW EXECUTE FUNCTION medarchive_guard_archive_placement()
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_box_leaving_shelf()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF (NEW.current_location_id IS DISTINCT FROM OLD.current_location_id
            OR NEW.current_shipment_id IS DISTINCT FROM OLD.current_shipment_id
            OR NEW.status IS DISTINCT FROM OLD.status)
           AND EXISTS (SELECT 1 FROM box_placements p WHERE p.box_id = OLD.id AND p.removed_at IS NULL) THEN
            RAISE EXCEPTION 'Сначала снимите коробку с архивной полки'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_box_leaving_shelf_guard
    BEFORE UPDATE ON boxes FOR EACH ROW
    EXECUTE FUNCTION medarchive_guard_box_leaving_shelf()
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_guard_box_manifest_placement()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF EXISTS (SELECT 1 FROM box_placements p
                   WHERE p.box_id = NEW.box_id AND p.removed_at IS NULL) THEN
            RAISE EXCEPTION 'Перед отправкой коробку необходимо снять с архивной полки'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_shipment_box_not_placed
    BEFORE INSERT OR UPDATE ON shipment_boxes FOR EACH ROW
    EXECUTE FUNCTION medarchive_guard_box_manifest_placement()
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION medarchive_domain_events_append_only()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'Журнал событий неизменяем: добавьте корректирующее событие'
            USING ERRCODE = '23514';
    END;
    $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_domain_events_append_only
    BEFORE UPDATE OR DELETE ON domain_events
    FOR EACH ROW EXECUTE FUNCTION medarchive_domain_events_append_only()
    """)


def downgrade() -> None:
    raise RuntimeError("Workflow guard triggers are intentionally irreversible; recreate a disposable DB")
