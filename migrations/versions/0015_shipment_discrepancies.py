"""E2 additive shipment discrepancy facts; no receipt workflow cutover."""
from alembic import op

revision = "0015_shipment_discrepancies"
down_revision = "0014_card_writeoff_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.shipment_discrepancies (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            shipment_id uuid NOT NULL REFERENCES public.shipments(id) ON DELETE RESTRICT,
            kind text NOT NULL,
            expected_box_id uuid REFERENCES public.boxes(id) ON DELETE RESTRICT,
            observed_box_id uuid REFERENCES public.boxes(id) ON DELETE RESTRICT,
            recorded_at timestamptz NOT NULL DEFAULT now(),
            recorded_by uuid NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
            CONSTRAINT ck_shipment_discrepancy_kind CHECK (
                kind IN ('missing_box', 'extra_box')
            )
        )
    """)
    op.execute("CREATE INDEX ix_shipment_discrepancies_shipment_id ON public.shipment_discrepancies(shipment_id)")
    op.execute("REVOKE ALL ON public.shipment_discrepancies FROM PUBLIC")


def downgrade() -> None:
    op.execute("""
        DO $guard$
        BEGIN
            LOCK TABLE public.shipment_discrepancies IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM public.shipment_discrepancies) THEN
                RAISE EXCEPTION 'Cannot downgrade populated shipment_discrepancies';
            END IF;
        END
        $guard$
    """)
    op.execute("DROP TABLE public.shipment_discrepancies")
