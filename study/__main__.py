import argparse
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import psycopg.conninfo

from .dataset import import_dataset
from .db import DATABASE_ERRORS, PostgresConnection, begin_snapshot, connect
from .deployment import deployment_settings
from .reporting import analyze, export_data


def connection_target(args, parser):
    if args.database:
        if args.admin:
            parser.error("--admin requires --environment")
        if args.command != "import" and not args.database.is_file():
            parser.error("Database does not exist")
        return args.database, None
    environment = dict(os.environ)
    environment.pop("VERCEL", None)
    environment.pop("VERCEL_ENV", None)
    environment["APP_ENV"] = args.environment
    if args.admin:
        name = args.environment.upper() + "_ADMIN_DATABASE_URL"
        url = environment.get(name)
        if not url:
            parser.error(f"{name} is required for owner commands")
        environment[args.environment.upper() + "_DATABASE_URL"] = url
    settings = deployment_settings(environment)
    if not settings.database:
        parser.error("Owner Postgres commands require an explicit database URL")
    return settings.database, settings


def backup_database(db, target, destination):
    if destination.exists():
        raise ValueError("Backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(db, PostgresConnection):
        # Keep the credential-bearing URL out of process arguments and command output.
        environment = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        options = psycopg.conninfo.conninfo_to_dict(str(target))
        password = options.pop("password", None)
        if password:
            environment["PGPASSWORD"] = password
        connection_info = psycopg.conninfo.make_conninfo(**options)
        try:
            result = subprocess.run(
                [
                    "pg_dump",
                    "--dbname=" + connection_info,
                    "--no-password",
                    "--format=custom",
                    "--schema=study",
                    "--no-owner",
                    "--no-acl",
                    "--file",
                    str(destination),
                ],
                env=environment,
                capture_output=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            destination.unlink(missing_ok=True)
            raise ValueError(
                "pg_dump unavailable or timed out; no complete backup produced."
            ) from None
        if result.returncode:
            destination.unlink(missing_ok=True)
            raise ValueError(
                "pg_dump failed; check connectivity, permissions, and client/server versions."
            )
    else:
        output = sqlite3.connect(destination)
        try:
            db.backup(output)
        finally:
            output.close()


def main():
    parser = argparse.ArgumentParser(
        description="Owner-only study commands; never calls an AI provider."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--database", type=Path, help="Local SQLite path")
    target.add_argument(
        "--environment",
        choices=("local", "preview", "production"),
        help="Explicit Postgres target",
    )
    parser.add_argument(
        "--admin",
        action="store_true",
        help="Use the selected environment ADMIN_DATABASE_URL for import/maintenance",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("import")
    command.add_argument("source", type=Path)
    command.add_argument("--version", required=True)
    command.add_argument("--results", type=Path)
    command.add_argument("--answer-base", type=int, choices=(0, 1))
    command.add_argument("--demo", action="store_true")
    command = commands.add_parser("export")
    command.add_argument("directory", type=Path)
    command.add_argument("--include-demo", action="store_true")
    commands.add_parser("analyze")
    commands.add_parser("check")
    command = commands.add_parser("backup")
    command.add_argument("destination", type=Path)
    args = parser.parse_args()
    db = None
    try:
        path, settings = connection_target(args, parser)
        if args.environment == "production" and getattr(args, "demo", False):
            raise ValueError("Production imports must use the real dataset.")
        db = connect(path, settings)
        if args.command == "import":
            print(
                json.dumps(
                    import_dataset(
                        db,
                        args.source,
                        args.version,
                        args.results,
                        args.answer_base,
                        args.demo,
                    ),
                    indent=2,
                )
            )
        elif args.command == "export":
            export_data(db, args.directory, args.include_demo)
            print(f"Exported to {args.directory}")
        elif args.command == "analyze":
            begin_snapshot(db)
            print(json.dumps(analyze(db), indent=2))
            db.rollback()
        elif args.command == "backup":
            backup_database(db, path, args.destination)
            print(f"Backup saved to {args.destination}")
        elif args.command == "check":
            print(
                json.dumps(
                    {
                        "environment": settings.environment if settings else "local",
                        "schema_verified": bool(settings),
                        "questions": db.execute(
                            "SELECT count(*) FROM questions"
                        ).fetchone()[0],
                        "participants": db.execute(
                            "SELECT count(*) FROM participants"
                        ).fetchone()[0],
                    }
                )
            )
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    except DATABASE_ERRORS as exc:
        parser.exit(
            1,
            f"Database operation failed ({type(exc).__name__}); check selected environment, identity, and permissions.\n",
        )
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    main()
