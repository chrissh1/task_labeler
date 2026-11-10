import argparse
from types import SimpleNamespace

import pytest

from study.__main__ import backup_database, connection_target
from study.db import PostgresConnection

UUID = "87d620a4-dfe0-40f9-946b-68b4d5e68f34"


def test_owner_preview_command_cannot_fall_back_to_production(monkeypatch):
    monkeypatch.setenv(
        "PRODUCTION_ADMIN_DATABASE_URL",
        "postgresql://owner:secret@production.example/postgres?sslmode=require",
    )
    args = SimpleNamespace(
        database=None, environment="preview", admin=True, command="import"
    )
    with pytest.raises(SystemExit):
        connection_target(args, argparse.ArgumentParser())


def test_owner_cli_selects_only_explicit_environment(monkeypatch):
    preview = "postgresql://owner:secret@preview.example/postgres?sslmode=require"
    monkeypatch.setenv("PREVIEW_ADMIN_DATABASE_URL", preview)
    monkeypatch.setenv("PREVIEW_DATABASE_ID", UUID)
    monkeypatch.setenv("VERCEL_ENV", "production")
    args = SimpleNamespace(
        database=None, environment="preview", admin=True, command="import"
    )
    url, settings = connection_target(args, argparse.ArgumentParser())
    assert url == preview and settings.environment == "preview"


def test_backup_keeps_password_out_of_arguments_and_removes_failed_artifact(
    monkeypatch, tmp_path
):
    captured = []
    destination = tmp_path / "backup.dump"

    def fake_run(arguments, **kwargs):
        captured.append((arguments, kwargs))
        destination.write_bytes(b"incomplete")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("study.__main__.subprocess.run", fake_run)
    db = object.__new__(PostgresConnection)
    url = "postgresql://owner:never-print-me@preview.example/postgres?sslmode=require"
    with pytest.raises(ValueError, match="pg_dump failed"):
        backup_database(db, url, destination)
    arguments, kwargs = captured[0]
    assert all("never-print-me" not in argument for argument in arguments)
    assert kwargs["env"]["PGPASSWORD"] == "never-print-me"
    assert not destination.exists()


def test_existing_backup_is_not_overwritten(tmp_path):
    path = tmp_path / "existing.dump"
    path.write_bytes(b"previous backup")
    with pytest.raises(ValueError, match="already exists"):
        backup_database(object.__new__(PostgresConnection), "unused", path)
    assert path.read_bytes() == b"previous backup"
