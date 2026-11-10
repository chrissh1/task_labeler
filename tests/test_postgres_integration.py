"""Real Postgres contracts, run only by scripts/check_postgres.py on a disposable server."""

import csv
import os
import subprocess
import threading
from pathlib import Path

import psycopg
import psycopg.conninfo
import pytest
from conftest import enter, identify, post, submit

from study import create_app
from study.dataset import import_dataset
from study.db import begin_snapshot, connect, transaction
from study.deployment import DeploymentSettings
from study.reporting import analyze, export_data

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = "87d620a4-dfe0-40f9-946b-68b4d5e68f34"
pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="Run scripts/check_postgres.py for a disposable Postgres server",
)


@pytest.fixture
def pg_database(request):
    deployment_environment = getattr(request, "param", "preview")
    url = os.environ["TEST_DATABASE_URL"]
    options = psycopg.conninfo.conninfo_to_dict(url)
    assert options["dbname"] == "task_study_test"
    assert options["host"].startswith(
        ("/tmp/task-study-pg-", "/private/tmp/task-study-pg-")
    )
    # The runner creates this private Unix socket. Never reset a remote/user database.
    environment = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    environment["PGDATABASE"] = options["dbname"]
    environment["PGHOST"] = options["host"]
    environment["PGSSLMODE"] = "disable"
    subprocess.run(
        [
            "psql",
            "--no-password",
            "-v",
            f"deployment_environment={deployment_environment}",
            "-v",
            f"database_id={IDENTITY}",
            "-f",
            str(ROOT / "scripts/provision_postgres.sql"),
        ],
        env=environment,
        check=True,
        capture_output=True,
    )
    owner_settings = DeploymentSettings(deployment_environment, url, IDENTITY, False)
    owner = connect(url, owner_settings)
    try:
        import_dataset(
            owner,
            ROOT / "questions.csv",
            "pg-test",
            results=ROOT.parent / "task_model_eval/final_run/results.csv",
        )
        runtime_options = dict(options, user="study_runtime")
        runtime_url = (
            "postgresql://study_runtime@localhost/task_study_test?"
            + __import__("urllib.parse", fromlist=["urlencode"]).urlencode(
                {
                    k: v
                    for k, v in runtime_options.items()
                    if k not in ("user", "dbname")
                }
            )
        )
        settings = DeploymentSettings(
            deployment_environment, runtime_url, IDENTITY, False
        )
        yield owner, settings
    finally:
        owner.close()
        with psycopg.connect(url, autocommit=True) as cleanup:
            cleanup.execute("DROP SCHEMA study CASCADE")
            cleanup.execute("DROP ROLE study_runtime")


def make_app(monkeypatch, settings):
    # Only TLS transport configuration is substituted for the local Unix-socket server;
    # the actual connection identity checks, SQL, transactions and role remain real.
    monkeypatch.setattr("study.deployment_settings", lambda environ: settings)
    return create_app(
        {"TESTING": True, "MODE": "study", "SECRET_KEY": "test-secret-" * 4},
        provider=lambda context: {
            "raw": '{"answer":"B","explanation":"A short explanation."}'
        },
    )


def test_real_postgres_full_flow_preview_exclusion_and_privileges(
    pg_database, monkeypatch, tmp_path
):
    owner, settings = pg_database
    app = make_app(monkeypatch, settings)
    client = app.test_client()
    assert identify(client).status_code == 200
    for condition in ("ai", "baseline"):
        state = enter(client, condition)
        for index in range(5):
            tid = state["trial"]["id"]
            if condition == "ai":
                post(
                    client,
                    f"/trials/{tid}/event",
                    {
                        "id": f"v{index}",
                        "page_id": "p",
                        "kind": "visible",
                        "visible_ms": 0,
                    },
                )
                call = post(
                    client,
                    f"/trials/{tid}/ai",
                    {"kind": "suggestion", "request_id": f"call{index}"},
                )
                assert call.json["status"] == "ok"
                assert (
                    post(client, f"/trials/{tid}/ai/{call.json['id']}/ack").status_code
                    == 200
                )
                assert (
                    "correct_index"
                    not in owner.execute(
                        "SELECT context FROM ai_calls WHERE id=?", (call.json["id"],)
                    ).fetchone()[0]
                )
            else:
                assert (
                    post(
                        client,
                        f"/trials/{tid}/ai",
                        {"kind": "suggestion", "request_id": f"baseline{index}"},
                    ).status_code
                    == 403
                )
            assert submit(client, tid).status_code == 200
            assert (
                post(
                    client, f"/trials/{tid}/submit", {"answer": 1, "confidence": 4}
                ).status_code
                == 200
            )
            state = client.get("/api/state?condition=" + condition).json
        assert state["stage"] == "comment"
        assert (
            post(
                client,
                f"/sessions/{state['session_id']}/complete",
                {"comment": "=test"},
            ).status_code
            == 200
        )
    other = app.test_client()
    identify(other, " PERSON+tag@example.com ")
    assert other.get("/api/state?condition=baseline").json["stage"] == "complete"
    assert owner.execute("SELECT count(*) FROM participants").fetchone()[0] == 1
    assert (
        owner.execute(
            "SELECT count(*) FROM trials WHERE submitted_at IS NOT NULL"
        ).fetchone()[0]
        == 10
    )
    assert owner.execute("SELECT sum(demo) FROM sessions").fetchone()[0] == 2
    assert analyze(owner)["participant_metrics"] == []
    with pytest.raises(ValueError, match="Demo"):
        export_data(owner, tmp_path / "denied")
    export_data(owner, tmp_path / "preview-export", include_demo=True)
    with (tmp_path / "preview-export/trials.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 8 and all(row["demo"] == "1" for row in rows)
    runtime = connect(settings.database, settings)
    try:
        assert runtime.raw.prepare_threshold is None
        for query in (
            "UPDATE deployment_metadata SET environment='production'",
            "DELETE FROM participants",
            "UPDATE questions SET correct_index=0",
            "CREATE TABLE study.bad(id integer)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(query)
        with pytest.raises(psycopg.errors.CheckViolation):
            runtime.execute("UPDATE sessions SET condition='ai'")
    finally:
        runtime.close()


@pytest.mark.parametrize("pg_database", ["preview", "production"], indirect=True)
def test_wrong_database_marker_refuses_start_before_any_write(pg_database, monkeypatch):
    owner, settings = pg_database
    wrong_environment = "production" if settings.environment == "preview" else "preview"
    wrong = DeploymentSettings(
        wrong_environment, settings.database, settings.database_id, True
    )
    with pytest.raises(ValueError, match="identity"):
        make_app(monkeypatch, wrong)
    assert owner.execute("SELECT count(*) FROM participants").fetchone()[0] == 0
    assert owner.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_postgres_concurrent_ai_dedup_does_not_block_answer(pg_database, monkeypatch):
    owner, settings = pg_database
    app = make_app(monkeypatch, settings)
    client = app.test_client()
    identify(client)
    tid = enter(client, "ai")["trial"]["id"]
    post(
        client,
        f"/trials/{tid}/event",
        {"id": "v", "page_id": "p", "kind": "visible", "visible_ms": 0},
    )
    entered = threading.Event()
    release = threading.Event()
    results = []

    def slow(context):
        entered.set()
        assert release.wait(10)
        return {"raw": '{"answer":"A","explanation":"Late answer."}'}

    app.extensions["provider"] = slow
    other = app.test_client()
    identify(other, "person+tag@example.com")
    thread = threading.Thread(
        target=lambda: results.append(
            post(
                other, f"/trials/{tid}/ai", {"kind": "suggestion", "request_id": "slow"}
            )
        )
    )
    thread.start()
    try:
        assert entered.wait(5)
        duplicate = post(
            client,
            f"/trials/{tid}/ai",
            {"kind": "suggestion", "request_id": "duplicate"},
        )
        assert duplicate.status_code == 202 and duplicate.json["id"] == "slow"
        assert (
            post(
                client, f"/trials/{tid}/submit", {"answer": 1, "confidence": 4}
            ).status_code
            == 200
        )
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and results[0].status_code == 200
    assert post(client, f"/trials/{tid}/ai/slow/ack").status_code == 409
    row = owner.execute("SELECT * FROM ai_calls").fetchone()
    assert row["raw_response"] and row["acknowledged_at"] is None
    assert owner.execute("SELECT count(*) FROM ai_calls").fetchone()[0] == 1


def test_snapshot_and_transaction_rollback(pg_database):
    owner, settings = pg_database
    runtime = connect(settings.database, settings)
    try:
        begin_snapshot(owner)
        assert owner.execute("SELECT count(*) FROM participants").fetchone()[0] == 0
        with transaction(runtime):
            runtime.execute(
                "INSERT INTO participants VALUES(?,?,?)",
                ("one", "one@example.test", "now"),
            )
        assert owner.execute("SELECT count(*) FROM participants").fetchone()[0] == 0
        owner.rollback()
        assert owner.execute("SELECT count(*) FROM participants").fetchone()[0] == 1
        with pytest.raises(RuntimeError), transaction(runtime):
            runtime.execute(
                "INSERT INTO participants VALUES(?,?,?)",
                ("two", "two@example.test", "now"),
            )
            raise RuntimeError("roll back this write")
        assert owner.execute("SELECT count(*) FROM participants").fetchone()[0] == 1
    finally:
        owner.rollback()
        runtime.close()


@pytest.mark.parametrize("pg_database", ["production"], indirect=True)
def test_production_sessions_count_and_backup_round_trip(
    pg_database, monkeypatch, tmp_path
):
    from study.__main__ import backup_database

    owner, settings = pg_database
    app = make_app(monkeypatch, settings)
    client = app.test_client()
    identify(client)
    for condition in ("baseline", "ai"):
        state = enter(client, condition)
        for _ in range(5):
            assert submit(client, state["trial"]["id"]).status_code == 200
            state = client.get("/api/state?condition=" + condition).json
        post(client, f"/sessions/{state['session_id']}/complete", {"comment": ""})
    report = analyze(owner)
    assert report["paired_completions"] == 1
    assert report["conditions"]["baseline"]["valid_completed_participants"] == 1
    assert report["conditions"]["ai"]["valid_completed_participants"] == 1
    export_data(owner, tmp_path / "real-export")
    assert owner.execute("SELECT sum(demo) FROM sessions").fetchone()[0] == 0
    backup = tmp_path / "study.dump"
    backup_database(owner, os.environ["TEST_DATABASE_URL"], backup)
    assert backup.is_file()
    listing = subprocess.run(
        ["pg_restore", "--list", str(backup)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "TABLE DATA study participants" in listing.stdout
    # Restore into a separate disposable database to verify schema and saved responses.
    options = psycopg.conninfo.conninfo_to_dict(os.environ["TEST_DATABASE_URL"])
    subprocess.run(
        ["createdb", "-h", options["host"], "task_study_restore"],
        check=True,
        capture_output=True,
    )
    try:
        subprocess.run(
            [
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "-h",
                options["host"],
                "-d",
                "task_study_restore",
                str(backup),
            ],
            check=True,
            capture_output=True,
        )
        restored_url = "postgresql:///task_study_restore?" + __import__(
            "urllib.parse", fromlist=["urlencode"]
        ).urlencode({"host": options["host"], "sslmode": "disable"})
        with psycopg.connect(restored_url) as restored:
            assert (
                restored.execute(
                    "SELECT count(*) FROM study.trials WHERE submitted_at IS NOT NULL"
                ).fetchone()[0]
                == 10
            )
            assert (
                restored.execute(
                    "SELECT environment FROM study.deployment_metadata"
                ).fetchone()[0]
                == "production"
            )
    finally:
        subprocess.run(
            ["dropdb", "-h", options["host"], "task_study_restore"],
            check=True,
            capture_output=True,
        )
