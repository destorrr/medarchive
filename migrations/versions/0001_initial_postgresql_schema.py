"""Create the initial PostgreSQL schema from the reviewed SQL source."""
from pathlib import Path
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[2] / "db" / "postgresql" / "001_initial_schema.sql"
    source = sql_path.read_text(encoding="utf-8")
    # Alembic owns the transaction. asyncpg accepts one command per execute call.
    source = source.replace("BEGIN;", "").replace("COMMIT;", "")
    for statement in source.split(";"):
        statement = statement.strip()
        if statement and not statement.startswith("--"):
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Initial schema downgrade is intentionally unsupported; recreate a disposable DB")
