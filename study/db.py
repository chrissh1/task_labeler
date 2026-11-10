import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import psycopg.conninfo
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from .deployment import verify_database_identity

DATABASE_ERRORS = (sqlite3.Error, psycopg.Error)


def now():
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def milliseconds(start, end):
    return max(
        0,
        (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        * 1000,
    )


class Record(dict):
    """Retain SQLite Row's named and positional access for shared query callers."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def record_row(cursor):
    factory = dict_row(cursor)
    return lambda values: Record(factory(values))


class PostgresConnection:
    def __init__(self, url):
        options = psycopg.conninfo.conninfo_to_dict(url)
        options.setdefault("sslmode", "require")
        options["connect_timeout"] = "10"
        self.raw = psycopg.connect(
            **options, row_factory=record_row, prepare_threshold=None, autocommit=True
        )
        self.environment = None

    def configure_transaction(self):
        self.raw.execute("SET LOCAL search_path TO study, pg_catalog")
        self.raw.execute("SET LOCAL statement_timeout TO '15s'")
        self.raw.execute("SET LOCAL lock_timeout TO '5s'")
        self.raw.execute("SET LOCAL idle_in_transaction_session_timeout TO '20s'")

    def execute(self, query, parameters=()):
        # All SQL is application-owned. Values (including any question marks) remain
        # bound parameters; no caller interpolates participant data into SQL text.
        query = query.replace("?", "%s")
        if self.raw.info.transaction_status == TransactionStatus.IDLE:
            with self.raw.transaction():
                self.configure_transaction()
                return self.raw.execute(query, parameters or None)
        return self.raw.execute(query, parameters or None)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def connect(path, deployment=None):
    if str(path).startswith(("postgresql://", "postgres://")):
        if deployment is None:
            raise ValueError(
                "Postgres connections require an explicit deployment identity."
            )
        db = PostgresConnection(str(path))
        try:
            verify_database_identity(db, deployment)
            db.environment = deployment.environment
            return db
        except BaseException:
            db.close()
            raise
    if deployment and deployment.environment != "local":
        raise ValueError(
            "Hosted environments require Postgres; SQLite fallback is disabled."
        )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript(Path(__file__).with_name("schema.sql").read_text())
    return db


@contextmanager
def transaction(db):
    if isinstance(db, PostgresConnection):
        db.raw.execute("BEGIN")
        try:
            db.configure_transaction()
            # Match SQLite's single-writer guarantee across serverless instances.
            # Only short database operations hold this database-local lock, never AI I/O.
            db.raw.execute("SELECT pg_advisory_xact_lock(594, 3)")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise


def begin_snapshot(db):
    if isinstance(db, PostgresConnection):
        db.raw.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        db.configure_transaction()
    else:
        db.execute("BEGIN")
