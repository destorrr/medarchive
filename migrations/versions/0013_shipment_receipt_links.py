"""E2 additive fulfillment links and receipt metadata; no workflow cutover."""
from alembic import op

revision = "0013_shipment_receipt_links"
down_revision = "0012_archive_request_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE shipments
        ADD COLUMN request_id uuid REFERENCES archive_requests(id) ON DELETE RESTRICT,
        ADD CONSTRAINT uq_shipments_request_id UNIQUE (request_id)
    """)
    op.execute("""
        ALTER TABLE shipment_cards
        ADD COLUMN request_item_id uuid REFERENCES archive_request_items(id) ON DELETE RESTRICT,
        ADD CONSTRAINT uq_shipment_cards_request_item UNIQUE (request_item_id)
    """)
    for table in ("shipment_cards", "shipment_boxes"):
        op.execute(f"""
            ALTER TABLE {table}
            ADD COLUMN received_at timestamptz,
            ADD COLUMN received_by uuid REFERENCES users(id) ON DELETE RESTRICT,
            ADD CONSTRAINT ck_{table}_receipt_pair CHECK (
                (received_at IS NULL) = (received_by IS NULL)
            )
        """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            LOCK TABLE shipments, shipment_cards, shipment_boxes IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM shipments WHERE request_id IS NOT NULL)
               OR EXISTS (SELECT 1 FROM shipment_cards WHERE request_item_id IS NOT NULL
                          OR received_at IS NOT NULL OR received_by IS NOT NULL)
               OR EXISTS (SELECT 1 FROM shipment_boxes WHERE received_at IS NOT NULL
                          OR received_by IS NOT NULL) THEN
                RAISE EXCEPTION 'Cannot downgrade populated shipment receipt links';
            END IF;
        END
        $$
    """)
    for table in ("shipment_cards", "shipment_boxes"):
        op.execute(f"""
            ALTER TABLE {table}
            DROP CONSTRAINT ck_{table}_receipt_pair,
            DROP COLUMN received_by,
            DROP COLUMN received_at
        """)
    op.execute("ALTER TABLE shipment_cards DROP COLUMN request_item_id")
    op.execute("ALTER TABLE shipments DROP COLUMN request_id")
