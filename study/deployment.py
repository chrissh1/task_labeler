"""Fail-closed deployment/database selection, independent of the database driver."""

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DeploymentSettings:
    environment: str
    database: str | None
    database_id: str | None
    hosted: bool


def deployment_settings(environ):
    platform = environ.get("VERCEL_ENV")
    hosted = environ.get("VERCEL") == "1" or platform in ("preview", "production")
    if hosted and platform not in ("preview", "production"):
        raise ValueError("Hosted deployments require VERCEL_ENV=preview or production.")
    requested = environ.get("APP_ENV")
    if hosted and requested and requested != platform:
        raise ValueError(
            "APP_ENV cannot override the VERCEL_ENV deployment environment."
        )
    environment = platform if hosted else requested or "local"
    if environment not in ("local", "preview", "production"):
        raise ValueError("APP_ENV must be local, preview, or production.")
    prefix = environment.upper()
    url = environ.get(f"{prefix}_DATABASE_URL")
    if environment == "local" and not url:
        return DeploymentSettings(environment, None, None, hosted)
    if not url:
        raise ValueError(
            f"{prefix}_DATABASE_URL is required; database fallback is disabled."
        )
    validate_postgres_url(url, require_tls=environment != "local")
    identity = environ.get(f"{prefix}_DATABASE_ID")
    try:
        identity = str(UUID(identity or ""))
    except ValueError:
        raise ValueError(
            f"{prefix}_DATABASE_ID must be the UUID recorded during initialization."
        ) from None
    return DeploymentSettings(environment, url, identity, hosted)


def validate_postgres_url(url, require_tls=True):
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if (
            parsed.scheme not in ("postgresql", "postgres")
            or not parsed.hostname
            or not parsed.username
            or not parsed.path.strip("/")
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("A valid Postgres connection URL is required.") from None
    if require_tls and query.get("sslmode") not in (
        ["require"],
        ["verify-ca"],
        ["verify-full"],
    ):
        raise ValueError(
            "Hosted database connections require TLS (sslmode=require or verify-full)."
        )
    if any(key in query for key in ("options", "service", "passfile")):
        raise ValueError(
            "Database URL may not override connection options, service, or password files."
        )


def verify_database_identity(db, settings):
    marker = db.execute(
        "SELECT environment,database_id,schema_version FROM deployment_metadata WHERE id=1"
    ).fetchone()
    if (
        not marker
        or marker["environment"] != settings.environment
        or marker["database_id"] != settings.database_id
    ):
        raise ValueError(
            "Database identity/environment mismatch; refusing to access study data."
        )
    if marker["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            "Unsupported database schema version; run the matching owner migration before deployment."
        )
