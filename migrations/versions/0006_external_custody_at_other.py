"""Align external-custody enum with the agreed at_other status."""

from alembic import op

revision = "0006_external_custody_at_other"
down_revision = "0005_card_external_custody"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        ADD CONSTRAINT ck_cards_custody_status
        CHECK (custody_status IN (
            'available', 'at_court', 'at_insurance', 'at_other'
        ))
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE cards
        DROP CONSTRAINT IF EXISTS ck_cards_custody_status
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
