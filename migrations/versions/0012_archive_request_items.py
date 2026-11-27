"""E2 additive request-item storage; workflow cutover is deferred to E4."""

from alembic import op

revision = "0012_archive_request_items"
down_revision = "0011_login_throttles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE archive_request_items (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            request_id uuid NOT NULL REFERENCES archive_requests(id) ON DELETE RESTRICT,
            card_id uuid NOT NULL REFERENCES cards(id) ON DELETE RESTRICT,
            status text NOT NULL CHECK (status IN (
                'pending', 'extracted', 'removed_by_central_office',
                'removed_by_archive', 'not_found', 'issued_pending_receipt',
                'completed', 'cancelled'
            )),
            reservation_active boolean NOT NULL,
            added_at timestamptz NOT NULL,
            added_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            extracted_at timestamptz,
            extracted_by uuid REFERENCES users(id) ON DELETE RESTRICT,
            resolved_at timestamptz,
            resolved_by uuid REFERENCES users(id) ON DELETE RESTRICT,
            source_membership_id uuid REFERENCES box_card_memberships(id) ON DELETE RESTRICT,
            returned_membership_id uuid REFERENCES box_card_memberships(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_request_item_extraction_pair CHECK (
                (extracted_at IS NULL) = (extracted_by IS NULL)
            ),
            CONSTRAINT ck_request_item_resolution_pair CHECK (
                (resolved_at IS NULL) = (resolved_by IS NULL)
            ),
            CONSTRAINT ck_request_item_tombstone CHECK (
                status <> 'removed_by_central_office' OR NOT reservation_active
            )
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_request_item_active_card
        ON archive_request_items(card_id) WHERE reservation_active
    """)
    op.execute("""
        CREATE INDEX ix_request_items_request
        ON archive_request_items(request_id)
    """)
    # No application writer is enabled before reconciliation and coupled guards.
    op.execute("REVOKE ALL ON archive_request_items FROM PUBLIC")


def downgrade() -> None:
    # Never silently discard populated request history.
    op.execute("""
        DO $$
        BEGIN
            LOCK TABLE archive_request_items IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM archive_request_items) THEN
                RAISE EXCEPTION 'Cannot downgrade populated archive_request_items';
            END IF;
        END
        $$
    """)
    op.execute("DROP TABLE archive_request_items")
