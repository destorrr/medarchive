"""Legacy fixture environment restoration during transition to PostgreSQL."""
import os
from pathlib import Path

import pytest
from tests.conftest import test_config

@pytest.mark.parametrize("exceptional", [False, True])
def test_fixture_restores_environment(monkeypatch, exceptional):
    monkeypatch.setenv("MEDARCHIVE_DB_PATH", "previous-test-path")
    monkeypatch.setenv("MEDARCHIVE_SECRET_KEY", "previous-test-secret")
    fixture = test_config.__wrapped__(monkeypatch)
    next(fixture)
    path = Path(os.environ["MEDARCHIVE_DB_PATH"])
    assert path.is_file()
    try:
        if exceptional:
            with pytest.raises(RuntimeError, match="synthetic failure"):
                fixture.throw(RuntimeError("synthetic failure"))
        else:
            with pytest.raises(StopIteration):
                next(fixture)
    finally:
        fixture.close()
    assert os.environ["MEDARCHIVE_DB_PATH"] == "previous-test-path"
    assert os.environ["MEDARCHIVE_SECRET_KEY"] == "previous-test-secret"
    assert not path.exists()
