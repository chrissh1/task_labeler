"""Run integration tests using installed PostgreSQL binaries and disposable storage."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode


def main():
    binaries = {
        name: shutil.which(name)
        for name in (
            "initdb",
            "pg_ctl",
            "createdb",
            "dropdb",
            "psql",
            "pg_dump",
            "pg_restore",
        )
    }
    if not all(binaries.values()):
        raise SystemExit(
            "Install PostgreSQL server and client binaries (including pg_dump/pg_restore) on PATH."
        )
    project_dir = Path(__file__).resolve().parents[1]
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("PG", "PREVIEW_", "PRODUCTION_", "LOCAL_"))
        and name
        not in (
            "DATABASE_URL",
            "EMAIL_HASH_KEY",
            "VERCEL",
            "VERCEL_ENV",
            "APP_ENV",
            "STUDY_MODE",
            "GEMINI_API_KEY",
            "SECRET_KEY",
        )
    }
    with tempfile.TemporaryDirectory(prefix="task-study-pg-", dir="/tmp") as directory:
        root = Path(directory)
        data = root / "data"
        subprocess.run(
            [
                binaries["initdb"],
                "-D",
                str(data),
                "-A",
                "trust",
                "--no-locale",
                "-E",
                "UTF8",
            ],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        started = False
        try:
            subprocess.run(
                [
                    binaries["pg_ctl"],
                    "-D",
                    str(data),
                    "-l",
                    str(root / "postgres.log"),
                    "-o",
                    f"-k {root} -c listen_addresses=''",
                    "-w",
                    "start",
                ],
                env=environment,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            started = True
            subprocess.run(
                [binaries["createdb"], "-h", str(root), "task_study_test"],
                env=environment,
                check=True,
            )
            environment["TEST_DATABASE_URL"] = (
                "postgresql:///task_study_test?"
                + urlencode({"host": str(root), "sslmode": "disable"})
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_postgres_integration.py",
                ],
                cwd=project_dir,
                env=environment,
                check=True,
                timeout=120,
            )
        finally:
            if started:
                subprocess.run(
                    [binaries["pg_ctl"], "-D", str(data), "-m", "fast", "-w", "stop"],
                    env=environment,
                    check=True,
                    stdout=subprocess.DEVNULL,
                )


if __name__ == "__main__":
    main()
