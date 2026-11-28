"""E4.4: execute request guards with their owning database principal.

Trigger-only functions use fixed, qualified relations and no dynamic SQL.
No runtime table or function grants are added.
"""
from alembic import op

revision = "0020_request_guard_owner"
down_revision = "0019_request_guards"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "medarchive_guard_target_request_composition",
    "medarchive_check_target_request_deferred",
)


def upgrade() -> None:
    for function in FUNCTIONS:
        op.execute(f"ALTER FUNCTION public.{function}() SECURITY DEFINER")
        op.execute(f"ALTER FUNCTION public.{function}() SET search_path TO pg_catalog, pg_temp")
        op.execute(f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC")


def downgrade() -> None:
    for function in FUNCTIONS:
        op.execute(f"ALTER FUNCTION public.{function}() SECURITY INVOKER")
        op.execute(f"ALTER FUNCTION public.{function}() SET search_path TO pg_catalog, public")
