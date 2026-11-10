import pytest

from study import create_app


def test_hosted_app_requires_preview_database_before_opening_sqlite(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.delenv("PREVIEW_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="PREVIEW_DATABASE_URL"):
        create_app({"TESTING": True, "SECRET_KEY": "x" * 40, "MODE": "demo"})


def test_production_rejects_demo_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "PRODUCTION_DATABASE_URL",
        "postgresql://user:password@db.example/postgres?sslmode=require",
    )
    monkeypatch.setenv("PRODUCTION_DATABASE_ID", "87d620a4-dfe0-40f9-946b-68b4d5e68f34")
    with pytest.raises(ValueError, match="production.*study"):
        create_app({"TESTING": True, "SECRET_KEY": "x" * 40, "MODE": "demo"})
