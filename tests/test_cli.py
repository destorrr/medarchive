import sys

import pytest

import medarchive.cli as cli


def _set_cli_args(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "medarchive-admin",
            "create-admin",
            "--username",
            "first-admin",
            "--full-name",
            "First Admin",
        ],
    )


def test_postgresql_bootstrap_cli_accepts_four_character_password(monkeypatch):
    _set_cli_args(monkeypatch)
    answers = iter(["pass", "pass"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(answers))
    created = {}

    async def fake_create_first_admin(username, full_name, password):
        created.update(username=username, full_name=full_name, password=password)

    monkeypatch.setattr(cli, "create_first_admin", fake_create_first_admin)

    cli.main()

    assert created == {
        "username": "first-admin",
        "full_name": "First Admin",
        "password": "pass",
    }


def test_postgresql_bootstrap_cli_rejects_three_character_password(monkeypatch):
    _set_cli_args(monkeypatch)
    answers = iter(["abc", "abc"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(answers))

    async def unexpected_create(*_args, **_kwargs):
        pytest.fail("bootstrap must not run for a short password")

    monkeypatch.setattr(cli, "create_first_admin", unexpected_create)

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
