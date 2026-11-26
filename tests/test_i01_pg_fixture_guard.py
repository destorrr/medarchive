"""Guard contract for the new isolated PostgreSQL fixture path."""
import pytest
from pg_fixture_guard import validate_test_url

URL = "postgresql+asyncpg://medarchive_runtime:synthetic@localhost/medarchive_test_i01"


@pytest.mark.parametrize("url,enabled,database,role", [
    (URL + "?host=example.invalid", "1", "medarchive_test_i01", "medarchive_runtime"),
    (URL, "0", "medarchive_test_i01", "medarchive_runtime"),
    (URL.replace("localhost", "example.invalid"), "1", "medarchive_test_i01", "medarchive_runtime"),
    (URL, "1", "medarchive", "medarchive_runtime"),
    (URL.replace("medarchive_runtime", "postgres"), "1", "medarchive_test_i01", "postgres"),
    ("sqlite+aiosqlite:///test.db", "1", "medarchive_test_i01", "medarchive_runtime"),
])
def test_guard_rejects_unsafe_configuration(url, enabled, database, role):
    with pytest.raises(ValueError):
        validate_test_url(url, enabled=enabled, database=database, role=role)


def test_guard_accepts_explicit_isolated_configuration():
    assert validate_test_url(URL, enabled="1", database="medarchive_test_i01",
                             role="medarchive_runtime").database == "medarchive_test_i01"
