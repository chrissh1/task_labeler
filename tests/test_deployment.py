import pytest

from study.deployment import deployment_settings, verify_database_identity

URL = "postgresql://study_runtime:secret@preview.example:6543/postgres?sslmode=require"
ID = "87d620a4-dfe0-40f9-946b-68b4d5e68f34"


def test_vercel_preview_never_falls_back_to_production_or_sqlite():
    with pytest.raises(ValueError, match="PREVIEW_DATABASE_URL"):
        deployment_settings(
            {"VERCEL": "1", "VERCEL_ENV": "preview", "PRODUCTION_DATABASE_URL": URL}
        )
    with pytest.raises(ValueError, match="Postgres"):
        deployment_settings(
            {
                "VERCEL_ENV": "preview",
                "PREVIEW_DATABASE_URL": "instance/study.sqlite3",
                "PREVIEW_DATABASE_ID": ID,
            }
        )


def test_platform_environment_cannot_be_overridden():
    with pytest.raises(ValueError, match="APP_ENV"):
        deployment_settings(
            {
                "VERCEL_ENV": "preview",
                "APP_ENV": "production",
                "PREVIEW_DATABASE_URL": URL,
                "PREVIEW_DATABASE_ID": ID,
            }
        )
    with pytest.raises(ValueError, match="VERCEL_ENV"):
        deployment_settings({"VERCEL": "1"})


def test_preview_selection_and_tls_validation():
    config = deployment_settings(
        {
            "VERCEL_ENV": "preview",
            "PREVIEW_DATABASE_URL": URL,
            "PREVIEW_DATABASE_ID": ID,
        }
    )
    assert config.environment == "preview" and config.database == URL and config.hosted
    with pytest.raises(ValueError, match="TLS"):
        deployment_settings(
            {
                "VERCEL_ENV": "preview",
                "PREVIEW_DATABASE_URL": URL.split("?")[0],
                "PREVIEW_DATABASE_ID": ID,
            }
        )


def test_database_side_marker_rejects_swapped_credentials_before_writes():
    class Cursor:
        def fetchone(self):
            return {"environment": "production", "database_id": ID, "schema_version": 1}

    class Database:
        def execute(self, query):
            assert query.startswith("SELECT")
            return Cursor()

    config = deployment_settings(
        {
            "VERCEL_ENV": "preview",
            "PREVIEW_DATABASE_URL": URL,
            "PREVIEW_DATABASE_ID": ID,
        }
    )
    with pytest.raises(ValueError, match="identity"):
        verify_database_identity(Database(), config)


def test_missing_or_wrong_database_id_is_rejected():
    with pytest.raises(ValueError, match="PREVIEW_DATABASE_ID"):
        deployment_settings({"VERCEL_ENV": "preview", "PREVIEW_DATABASE_URL": URL})

    class Cursor:
        def fetchone(self):
            return {
                "environment": "preview",
                "database_id": "different",
                "schema_version": 1,
            }

    class Database:
        def execute(self, query):
            return Cursor()

    config = deployment_settings(
        {
            "VERCEL_ENV": "preview",
            "PREVIEW_DATABASE_URL": URL,
            "PREVIEW_DATABASE_ID": ID,
        }
    )
    with pytest.raises(ValueError, match="identity"):
        verify_database_identity(Database(), config)


def test_local_sqlite_is_still_default_and_does_not_use_cloud_secrets():
    config = deployment_settings({"PRODUCTION_DATABASE_URL": URL})
    assert config.environment == "local" and not config.hosted
    assert config.database is None
