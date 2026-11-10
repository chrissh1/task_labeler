import json

from conftest import enter, identify, post

from study.db import connect


def ai_trial(client):
    identify(client)
    return enter(client, "ai")["trial"]["id"]


def test_live_suggestion_dedup_context_and_ack(app, client):
    calls = []

    def fake(context):
        calls.append(context)
        return {
            "raw": json.dumps(
                {"answer": "C", "explanation": "Compare the definitions."}
            ),
            "metadata": {"tokens": 12},
        }

    app.extensions["provider"] = fake
    tid = ai_trial(client)
    post(
        client,
        f"/trials/{tid}/event",
        {"id": "visible", "page_id": "p", "kind": "visible", "visible_ms": 0},
    )
    body = {"kind": "suggestion", "request_id": "first"}
    a = post(client, f"/trials/{tid}/ai", body)
    assert a.status_code == 200
    assert a.json["text"] == "C. Compare the definitions."
    assert (
        post(client, f"/trials/{tid}/ai", dict(body, request_id="double-click")).json[
            "id"
        ]
        == a.json["id"]
    )
    assert len(calls) == 1
    assert all(
        k not in json.dumps(calls)
        for k in ("correct_index", "original_output", "email")
    )
    post(client, f"/trials/{tid}/ai/{a.json['id']}/ack")
    post(client, f"/trials/{tid}/ai/{a.json['id']}/ack")
    chat = post(
        client,
        f"/trials/{tid}/ai",
        {"kind": "chat", "request_id": "chat1", "text": "Explain the key concept"},
    )
    assert chat.status_code == 200
    assert "Compare the definitions." in calls[-1]
    assert (
        post(
            client, f"/trials/{tid}/submit", {"answer": 1, "confidence": 4}
        ).status_code
        == 200
    )
    assert post(client, f"/trials/{tid}/ai/{chat.json['id']}/ack").status_code == 409
    db = connect(app.config["DATABASE"])
    assert (
        db.execute(
            "select count(*) from ai_calls where acknowledged_at is not null"
        ).fetchone()[0]
        == 1
    )
    new = client.get("/api/state?condition=ai").json["trial"]["id"]
    post(
        client,
        f"/trials/{new}/event",
        {"id": "new-visible", "page_id": "p", "kind": "visible", "visible_ms": 0},
    )
    post(client, f"/trials/{new}/ai", {"kind": "suggestion", "request_id": "new"})
    assert "Explain the key concept" not in calls[-1]


def test_provider_failure_invalid_output_and_bounded_manual_retry(app, client):
    tid = ai_trial(client)
    post(
        client,
        f"/trials/{tid}/event",
        {"id": "v", "page_id": "p", "kind": "visible", "visible_ms": 0},
    )

    def failure(context):
        raise TimeoutError("secret key must not be logged")

    app.extensions["provider"] = failure
    response = post(
        client, f"/trials/{tid}/ai", {"kind": "suggestion", "request_id": "one"}
    )
    assert response.json["status"] == "error"
    app.extensions["provider"] = lambda context: {
        "raw": '{"answer":"Z","explanation":"bad"}',
        "metadata": {},
    }
    response = post(
        client,
        f"/trials/{tid}/ai",
        {"kind": "suggestion", "request_id": "two", "retry": True},
    )
    assert response.json["status"] == "error"
    assert (
        post(
            client,
            f"/trials/{tid}/ai",
            {"kind": "suggestion", "request_id": "three", "retry": True},
        ).status_code
        == 409
    )
    assert "secret key" not in json.dumps(
        [
            dict(r)
            for r in connect(app.config["DATABASE"]).execute("select * from ai_calls")
        ]
    )
    assert (
        post(
            client, f"/trials/{tid}/submit", {"answer": 0, "confidence": 1}
        ).status_code
        == 200
    )


def test_model_confidence_percentages_are_not_displayed():
    import pytest

    from study.ai import parse_output

    with pytest.raises(ValueError, match="confidence"):
        parse_output(
            json.dumps(
                {"answer": "A", "explanation": "I am 90% confident that A is correct."}
            )
        )
