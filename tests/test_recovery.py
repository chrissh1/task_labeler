import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import enter, identify, post, submit

from study import create_app
from study.ai import parse_output
from study.dataset import import_dataset
from study.db import connect


def test_pending_dedup_late_response_and_no_write_lock(app, client):
    identify(client)
    state = enter(client, "ai")
    tid = state["trial"]["id"]
    post(
        client,
        f"/trials/{tid}/event",
        {"id": "visible", "page_id": "p", "kind": "visible", "visible_ms": 0},
    )
    entered = threading.Event()
    release = threading.Event()
    requests = []
    result = []

    def slow(context):
        requests.append(context)
        entered.set()
        assert release.wait(5)
        return {"raw": '{"answer":"A","explanation":"Delayed response."}'}

    app.extensions["provider"] = slow
    second = app.test_client()
    identify(second, "person+tag@example.com")
    thread = threading.Thread(
        target=lambda: result.append(
            post(
                second,
                f"/trials/{tid}/ai",
                {"kind": "suggestion", "request_id": "slow"},
            )
        )
    )
    thread.start()
    try:
        assert entered.wait(3)
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
        thread.join(5)
    assert not thread.is_alive() and len(requests) == 1
    assert result[0].json["status"] == "ok"
    assert post(client, f"/trials/{tid}/ai/slow/ack").status_code == 409
    row = connect(app.config["DATABASE"]).execute("select * from ai_calls").fetchone()
    assert row["raw_response"] and row["acknowledged_at"] is None


def test_stale_pending_recovery_requires_explicit_retry(app, client):
    identify(client)
    tid = enter(client, "ai")["trial"]["id"]
    post(
        client,
        f"/trials/{tid}/event",
        {"id": "v", "page_id": "p", "kind": "visible", "visible_ms": 0},
    )
    post(client, f"/trials/{tid}/ai", {"kind": "suggestion", "request_id": "stale"})
    db = connect(app.config["DATABASE"])
    past = (datetime.now(UTC) - timedelta(seconds=100)).isoformat()
    db.execute(
        "update ai_calls set status='pending',started_at=?,displayed_text=null where id='stale'",
        (past,),
    )
    recovered = client.get(f"/trials/{tid}/ai").json["calls"][0]
    assert recovered["status"] == "stale"
    assert (
        post(
            client, f"/trials/{tid}/ai", {"kind": "suggestion", "request_id": "refresh"}
        ).json["id"]
        == "stale"
    )
    assert (
        post(
            client,
            f"/trials/{tid}/ai",
            {"kind": "suggestion", "request_id": "retry", "retry": True},
        ).json["status"]
        == "ok"
    )
    assert db.execute("select count(*) from ai_calls").fetchone()[0] == 2


def test_followup_limits_and_wrong_owner(app, client):
    identify(client)
    tid = enter(client, "ai")["trial"]["id"]
    post(
        client,
        f"/trials/{tid}/event",
        {"id": "v", "page_id": "p", "kind": "visible", "visible_ms": 0},
    )
    post(client, f"/trials/{tid}/ai", {"kind": "suggestion", "request_id": "s"})
    for i in range(3):
        assert (
            post(
                client,
                f"/trials/{tid}/ai",
                {"kind": "chat", "request_id": f"c{i}", "text": "Explain"},
            ).status_code
            == 200
        )
    assert (
        post(
            client,
            f"/trials/{tid}/ai",
            {"kind": "chat", "request_id": "too-many", "text": "Explain"},
        ).status_code
        == 409
    )
    other = app.test_client()
    identify(other, "another@example.org")
    assert other.get(f"/trials/{tid}/ai").status_code == 404
    assert post(other, f"/trials/{tid}/ai/s/ack").status_code == 404


def test_restart_freeze_and_timing_dedup(app, client):
    identify(client)
    state = enter(client)
    tid = state["trial"]["id"]
    body = {"id": "v", "page_id": "p", "kind": "visible", "visible_ms": 0}
    post(client, f"/trials/{tid}/event", body)
    post(client, f"/trials/{tid}/event", body)
    db = connect(app.config["DATABASE"])
    assert db.execute("select count(*) from events").fetchone()[0] == 1
    before = dict(db.execute("select * from trials where id=?", (tid,)).fetchone())
    restarted = create_app(dict(app.config), provider=app.extensions["provider"])
    returning = restarted.test_client()
    identify(returning, "PERSON+tag@example.com")
    assert returning.get("/api/state?condition=baseline").json["trial"]["id"] == tid
    assert (
        dict(db.execute("select * from trials where id=?", (tid,)).fetchone()) == before
    )
    with pytest.raises(ValueError, match="frozen"):
        create_app(
            dict(app.config, TRIALS_PER_CONDITION=6),
            provider=app.extensions["provider"],
        )


def test_explicit_demo_is_excluded_and_missing_credentials_fail(tmp_path, monkeypatch):
    from study.reporting import analyze, export_data

    path = tmp_path / "demo.db"
    db = connect(path)
    import_dataset(
        db,
        Path(__file__).resolve().parents[1] / "demo_questions.json",
        "demo",
        demo=True,
    )
    app = create_app(
        {
            "TESTING": True,
            "MODE": "demo",
            "DATABASE": str(path),
            "SECRET_KEY": "testing",
        }
    )
    client = app.test_client()
    identify(client)
    state = enter(client)
    for _ in range(5):
        submit(client, state["trial"]["id"])
        state = client.get("/api/state?condition=baseline").json
    post(client, f"/sessions/{state['session_id']}/complete")
    assert analyze(db)["participant_metrics"] == []
    with pytest.raises(ValueError, match="Demo"):
        export_data(db, tmp_path / "export")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        create_app(
            {
                "MODE": "study",
                "DATABASE": str(tmp_path / "missing.db"),
                "SECRET_KEY": "x" * 32,
            }
        )


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"answer":"AB","explanation":"x"}',
        json.dumps({"answer": "A", "explanation": "word " * 121}),
        "{bad",
    ],
)
def test_strict_model_output_validation(raw):
    with pytest.raises((ValueError, TypeError)):
        parse_output(raw)


def test_response_after_stale_notice_does_not_rewrite_display_log(app, client):
    identify(client)
    tid = enter(client, "ai")["trial"]["id"]
    post(
        client,
        f"/trials/{tid}/event",
        {"id": "v", "page_id": "p", "kind": "visible", "visible_ms": 0},
    )
    entered = threading.Event()
    release = threading.Event()

    def slow(context):
        entered.set()
        assert release.wait(5)
        return {"raw": '{"answer":"A","explanation":"Late content."}'}

    app.extensions["provider"] = slow
    other = app.test_client()
    identify(other, "person+tag@example.com")
    thread = threading.Thread(
        target=lambda: post(
            other, f"/trials/{tid}/ai", {"kind": "suggestion", "request_id": "late"}
        )
    )
    thread.start()
    try:
        assert entered.wait(3)
        db = connect(app.config["DATABASE"])
        db.execute(
            "update ai_calls set started_at=?",
            ((datetime.now(UTC) - timedelta(seconds=100)).isoformat(),),
        )
        notice = client.get(f"/trials/{tid}/ai").json["calls"][0]["text"]
        post(client, f"/trials/{tid}/ai/late/ack")
    finally:
        release.set()
        thread.join(5)
    row = db.execute("select * from ai_calls").fetchone()
    assert row["displayed_text"] == notice
    assert row["status"] == "late_ok"
    assert "Late content" in row["raw_response"]


def test_visible_time_accumulates_across_refresh_and_includes_confidence(
    app, client, monkeypatch
):
    identify(client)
    tid = enter(client)["trial"]["id"]
    stamp = ["2026-11-09T12:00:00.000+00:00"]
    monkeypatch.setattr("study.now", lambda: stamp[0])
    post(
        client,
        f"/trials/{tid}/event",
        {"id": "first", "page_id": "one", "kind": "visible", "visible_ms": 0},
    )
    stamp[0] = "2026-11-09T12:00:10.000+00:00"
    body = {
        "id": "checkpoint",
        "page_id": "one",
        "kind": "confidence_change",
        "visible_ms": 10000,
        "value": 4,
    }
    post(client, f"/trials/{tid}/event", body)
    post(client, f"/trials/{tid}/event", body)
    post(
        client,
        f"/trials/{tid}/event",
        {"id": "refresh", "page_id": "two", "kind": "visible", "visible_ms": 0},
    )
    stamp[0] = "2026-11-09T12:00:20.000+00:00"
    post(
        client,
        f"/trials/{tid}/event",
        {"id": "hidden", "page_id": "two", "kind": "hidden", "visible_ms": 5000},
    )
    post(client, f"/trials/{tid}/submit", {"answer": 1, "confidence": 4})
    row = (
        connect(app.config["DATABASE"])
        .execute("select * from trials where id=?", (tid,))
        .fetchone()
    )
    assert row["visible_ms"] == 15000 and row["elapsed_ms"] == 20000
    assert row["timing_approximate"] == 1
