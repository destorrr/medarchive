"""Create the initial PostgreSQL schema from the reviewed SQL source."""
from pathlib import Path
import re
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
    # Remove whole-line SQL comments before splitting, otherwise a comment
    # preceding CREATE TABLE would cause that first statement to be skipped.
    source = re.sub(r"(?m)^\\s*--.*$", "", source)
    for statement in source.split(";"):
        statement = statement.strip()
        if statement:
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Initial schema downgrade is intentionally unsupported; recreate a disposable DB")
