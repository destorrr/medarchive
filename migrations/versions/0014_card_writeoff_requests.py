"""E2 additive writeoff request storage; no workflow cutover."""
from alembic import op

revision = "0014_card_writeoff_requests"
down_revision = "0013_shipment_receipt_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.card_writeoff_requests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            card_id uuid NOT NULL REFERENCES public.cards(id) ON DELETE RESTRICT,
            requested_by uuid NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
            requested_at timestamptz NOT NULL DEFAULT now(),
            status text NOT NULL DEFAULT 'submitted',
            reason_code text NOT NULL,
            comment text,
            document_reference text,
            processing_at timestamptz,
            processing_by uuid REFERENCES public.users(id) ON DELETE RESTRICT,
            resolved_at timestamptz,
            resolved_by uuid REFERENCES public.users(id) ON DELETE RESTRICT,
            resolution_reason_code text,
            resolution_comment text,
            CONSTRAINT ck_writeoff_status CHECK (
                status IN ('submitted','processing','completed','rejected','cancelled')
            ),
            CONSTRAINT ck_writeoff_reason CHECK (
                reason_code IN ('lost','damaged','other')
            ),
            CONSTRAINT ck_writeoff_processing_pair CHECK (
                (processing_at IS NULL) = (processing_by IS NULL)
            ),
            CONSTRAINT ck_writeoff_resolution_pair CHECK (
                (resolved_at IS NULL) = (resolved_by IS NULL)
            )
        )
    """)
    op.execute("CREATE INDEX ix_card_writeoff_requests_card_id ON public.card_writeoff_requests(card_id)")
    op.execute("REVOKE ALL ON public.card_writeoff_requests FROM PUBLIC")


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            LOCK TABLE public.card_writeoff_requests IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM public.card_writeoff_requests) THEN
                RAISE EXCEPTION 'Cannot downgrade populated card_writeoff_requests';
            END IF;
        END
        $$
    """)
    op.execute("DROP TABLE public.card_writeoff_requests")
