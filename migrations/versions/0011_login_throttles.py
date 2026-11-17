"""Persisted, privacy-preserving login throttling counters."""

from alembic import op

revision = "0011_login_throttles"
down_revision = "0010_archive_fulfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE auth_login_throttles (
            throttle_key char(64) PRIMARY KEY,
            failed_attempts integer NOT NULL CHECK (failed_attempts > 0),
            window_started_at timestamptz NOT NULL,
            blocked_until timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX ix_auth_login_throttles_updated_at
        ON auth_login_throttles(updated_at)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE auth_login_throttles")
