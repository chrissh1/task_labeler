import json
import sqlite3

from conftest import enter, identify, post, submit

from study.db import connect


def test_identity_balanced_fixed_assignment_and_resume(app, client):
    assert identify(client).status_code == 200
    client.get("/study?condition=ai")
    db = connect(app.config["DATABASE"])
    before = [
        tuple(r)
        for r in db.execute(
            "select session_id,question_id,position from trials order by id"
        )
    ]
    assert len(before) == 10
    assert len({r[1] for r in before if r[1]}) == 8
    assert [
        r[0]
        for r in db.execute(
            "select count(*) from trials t join questions q on q.id=t.question_id group by session_id,subject"
        )
    ] == [2] * 4
    assert (
        db.execute(
            "select count(*) from sessions where started_at is not null"
        ).fetchone()[0]
        == 0
    )
    state = enter(client)
    tid = state["trial"]["id"]
    assert submit(client, tid).status_code == 200
    assert (
        post(
            client, f"/trials/{tid}/submit", {"answer": 1, "confidence": 4}
        ).status_code
        == 200
    )
    assert (
        post(
            client, f"/trials/{tid}/submit", {"answer": 2, "confidence": 4}
        ).status_code
        == 409
    )
    other = app.test_client()
    identify(other, " person+tag@example.com ")
    other.get("/study?condition=baseline")
    assert [
        tuple(r)
        for r in db.execute(
            "select session_id,question_id,position from trials order by id"
        )
    ] == before
    assert db.execute("select count(*) from participants").fetchone()[0] == 1
    assert other.get("/api/state?condition=baseline").json["trial"]["id"] != tid


def test_payloads_ownership_csrf_invalid_inputs_and_condition_lock(app, client):
    assert identify(client, "invalid").status_code == 400
    identify(client)
    state = enter(client)
    text = json.dumps(state)
    assert all(
        k not in text
        for k in ("correct_index", "original_output", "original_answer", "email")
    )
    tid = state["trial"]["id"]
    assert (
        client.post(
            f"/trials/{tid}/submit", json={"answer": 1, "confidence": 4}
        ).status_code
        == 403
    )
    for values in (
        {"answer": 4, "confidence": 1},
        {"answer": True, "confidence": 1},
        {"answer": 1, "confidence": 0},
    ):
        assert post(client, f"/trials/{tid}/submit", values).status_code == 400
    other = app.test_client()
    identify(other, "other@example.com")
    assert (
        post(other, f"/trials/{tid}/submit", {"answer": 1, "confidence": 1}).status_code
        == 404
    )
    assert (
        post(
            client,
            f"/trials/{tid}/ai",
            {"kind": "suggestion", "request_id": "request-1"},
        ).status_code
        == 403
    )
    db = connect(app.config["DATABASE"])
    try:
        db.execute(
            "update sessions set condition='ai' where id=?", (state["session_id"],)
        )
        assert False
    except sqlite3.IntegrityError:
        pass


def test_complete_both_conditions_and_attention_restarts(app, client):
    identify(client)
    client.get("/study?condition=ai")
    sid = client.get("/api/state?condition=ai").json["session_id"]
    post(client, f"/sessions/{sid}/begin")
    for _ in range(2):
        post(client, f"/sessions/{sid}/attention", {"answer": 0})
    assert client.get("/api/state?condition=ai").json["stage"] == "instructions"
    for condition in ("ai", "baseline"):
        state = enter(client, condition)
        assert state["trial"]["practice"]
        for _ in range(5):
            assert submit(client, state["trial"]["id"]).status_code == 200
            state = client.get("/api/state?condition=" + condition).json
        assert state["stage"] == "comment"
        assert (
            post(
                client,
                f"/sessions/{state['session_id']}/complete",
                {"comment": "=safe text"},
            ).status_code
            == 200
        )
        assert (
            client.get("/api/state?condition=" + condition).json["stage"] == "complete"
        )
        client.get("/study?condition=" + condition)
    db = connect(app.config["DATABASE"])
    assert [
        tuple(r)
        for r in db.execute(
            "select condition,condition_order from sessions order by condition_order"
        )
    ] == [("ai", 1), ("baseline", 2)]
    assert db.execute("select sum(restart) from attention").fetchone()[0] == 1
    assert (
        db.execute(
            "select count(*) from trials where submitted_at is not null"
        ).fetchone()[0]
        == 10
    )


def test_direct_link_preserves_condition_and_html_omits_private_fields(app, client):
    response = client.get("/study?condition=ai")
    assert response.status_code == 302 and response.location == "/?condition=ai"
    home = client.get(response.location).text
    assert 'name="condition" value="ai"' in home
    assert (
        post(
            client, "/identify", {"email": "direct@example.com", "condition": "ai"}
        ).json["redirect"]
        == "/study?condition=ai"
    )
    state = enter(client, "ai")
    submit(client, state["trial"]["id"])
    state = client.get("/api/state?condition=ai").json
    html = client.get("/study?condition=ai").text
    assert 'id="chat-form"' in html
    assert "correct_index" not in html and "original_output" not in html
    assert " checked" not in html
    baseline = client.get("/study?condition=baseline").text
    assert 'id="chat-form"' not in baseline
    assert 'id="messages"' not in baseline


def test_independent_participant_sampling_and_explicit_pool_error(
    app, client, monkeypatch
):
    import random

    from study.db import transaction
    from study.service import allocate

    # Seed the random source in this test only, so independence is deterministic.
    generator = random.Random(594)
    monkeypatch.setattr("study.service.random.SystemRandom", lambda: generator)
    identify(client)
    client.get("/study?condition=baseline")
    other = app.test_client()
    identify(other, "independent@example.org")
    other.get("/study?condition=ai")
    db = connect(app.config["DATABASE"])
    assignments = {}
    for row in db.execute(
        "select s.participant_id,t.question_id from trials t join sessions s on s.id=t.session_id where t.practice=0 order by t.position"
    ):
        assignments.setdefault(row[0], []).append(row[1])
    first, second = assignments.values()
    assert first != second
    with transaction(db):
        import pytest

        with pytest.raises(ValueError, match="Configuration error"):
            allocate(db, "not-inserted", {"trials_per_condition": 202}, False)
