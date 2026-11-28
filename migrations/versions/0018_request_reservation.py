"""E4.4 target reservation and parent/item consistency validator.

Installed without activating target lifecycle triggers against legacy writers.
E4.5 must invoke it from deferred guards on both parent and item mutations.
"""
from alembic import op

revision = "0018_request_reservation"
down_revision = "0017_request_item_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.medarchive_validate_request_reservation(p_request_id uuid)
        RETURNS void LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $body$
        DECLARE
            parent_status text;
        BEGIN
            SELECT status INTO parent_status
            FROM public.archive_requests WHERE id = p_request_id;
            IF NOT FOUND OR parent_status NOT IN
                ('draft', 'submitted', 'processing', 'completed', 'cancelled') THEN
                RAISE EXCEPTION 'Invalid target request parent' USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.archive_request_items i
                WHERE i.request_id = p_request_id
                AND (
                    i.reservation_active IS DISTINCT FROM
                        (parent_status IN ('draft', 'submitted', 'processing')
                         AND i.status <> 'removed_by_central_office')
                    OR (i.status <> 'removed_by_central_office' AND NOT (
                        (parent_status IN ('draft', 'submitted') AND i.status = 'pending')
                        OR (parent_status = 'processing' AND i.status IN
                            ('extracted', 'removed_by_archive', 'not_found',
                             'issued_pending_receipt', 'completed'))
                        OR (parent_status = 'completed' AND i.status IN
                            ('completed', 'removed_by_archive', 'not_found'))
                        OR (parent_status = 'cancelled' AND i.status IN
                            ('cancelled', 'removed_by_archive', 'not_found'))
                    ))
                )
            ) THEN
                RAISE EXCEPTION 'Request item status or reservation conflicts with parent'
                    USING ERRCODE = '23514';
            END IF;
            IF parent_status IN ('submitted', 'processing', 'completed')
               AND NOT EXISTS (
                   SELECT 1 FROM public.archive_request_items
                   WHERE request_id = p_request_id AND status <> 'removed_by_central_office'
               ) THEN
                RAISE EXCEPTION 'Target request requires a nonempty composition'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $body$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.medarchive_validate_request_reservation(uuid) FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION public.medarchive_validate_request_reservation(uuid)")
