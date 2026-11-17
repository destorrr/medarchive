"""Restore the agreed external-organization custody status value."""

from alembic import op

revision = "0009_external_org_custody"
down_revision = "0008_shipment_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE cards
        DROP CONSTRAINT IF EXISTS cards_custody_status_check
    """)
    op.execute("""
        UPDATE cards
        SET custody_status = 'at_external_organization'
        WHERE custody_status = 'at_other'
    """)
    op.execute("""
        ALTER TABLE cards
        ADD CONSTRAINT cards_custody_status_check
        CHECK (custody_status IN (
            'available', 'at_court', 'at_insurance', 'at_external_organization'
        ))
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE cards
        DROP CONSTRAINT IF EXISTS cards_custody_status_check
    """)
    op.execute("""
        UPDATE cards
        SET custody_status = 'at_other'
        WHERE custody_status = 'at_external_organization'
    """)
    op.execute("""
        ALTER TABLE cards
        ADD CONSTRAINT cards_custody_status_check
        CHECK (custody_status IN (
            'available', 'at_court', 'at_insurance', 'at_other'
        ))
    """)
