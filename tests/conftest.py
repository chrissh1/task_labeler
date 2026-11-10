import os
from pathlib import Path

import pytest

from study import create_app
from study.dataset import import_dataset
from study.db import connect


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    # Running pytest after sourcing a deployment .env must never select that database.
    for name in list(os.environ):
        if name.startswith(("PREVIEW_", "PRODUCTION_", "LOCAL_")) or name in (
            "VERCEL",
            "VERCEL_ENV",
            "APP_ENV",
            "STUDY_DATABASE",
            "STUDY_MODE",
            "GEMINI_API_KEY",
            "SECRET_KEY",
            "TRIALS_PER_CONDITION",
            "MAX_FOLLOWUPS",
            "COOKIE_SECURE",
        ):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def app(tmp_path):
    path = tmp_path / "test.db"
    db = connect(path)
    import_dataset(
        db, Path(__file__).resolve().parents[1] / "questions.csv", "test-real"
    )
    db.close()
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(path),
            "SECRET_KEY": "test-only-secret",
            "MODE": "study",
        },
        provider=lambda context: {
            "raw": '{"answer":"B","explanation":"A brief explanation."}',
            "metadata": {},
        },
    )


@pytest.fixture
def client(app):
    return app.test_client()


def post(client, url, data=None):
    client.get("/")
    with client.session_transaction() as s:
        token = s["csrf"]
    return client.post(url, json=data or {}, headers={"X-CSRF-Token": token})


def identify(client, email="Person+tag@Example.COM"):
    return post(client, "/identify", {"email": email})


def enter(client, condition="baseline"):
    client.get("/study?condition=" + condition)
    state = client.get("/api/state?condition=" + condition).json
    sid = state["session_id"]
    assert post(client, f"/sessions/{sid}/begin").status_code == 200
    assert post(client, f"/sessions/{sid}/attention", {"answer": 2}).status_code == 200
    return client.get("/api/state?condition=" + condition).json


def submit(client, trial_id, answer=1, confidence=4):
    assert (
        post(
            client,
            f"/trials/{trial_id}/event",
            {
                "id": "visible-" + trial_id,
                "page_id": "page",
                "kind": "visible",
                "visible_ms": 0,
            },
        ).status_code
        == 200
    )
    return post(
        client,
        f"/trials/{trial_id}/submit",
        {"answer": answer, "confidence": confidence},
    )
