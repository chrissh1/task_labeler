import csv

from conftest import enter, identify, post, submit

from study.db import connect
from study.reporting import analyze, export_data


def test_export_joins_validity_privacy_and_analysis(app, client, tmp_path):
    identify(client)
    state = enter(client)
    for _ in range(5):
        submit(client, state["trial"]["id"])
        state = client.get("/api/state?condition=baseline").json
    post(client, f"/sessions/{state['session_id']}/complete", {"comment": "=SUM(A1)"})
    db = connect(app.config["DATABASE"])
    export_data(db, tmp_path / "out")
    with (tmp_path / "out/trials.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert (
        len(rows) == 8
    )  # Includes assigned, unanswered trials explicitly marked partial.
    assert all(r["question_id"] and r["participant_id"] for r in rows)
    assert "email" not in rows[0]
    with (tmp_path / "out/sessions.csv").open() as handle:
        sessions = list(csv.DictReader(handle))
    assert (
        next(r for r in sessions if r["condition"] == "baseline")["comment"]
        == "'=SUM(A1)"
    )
    report = analyze(db)
    assert report["conditions"]["baseline"]["valid_completed_participants"] == 1
    assert report["conditions"]["ai"]["valid_completed_participants"] == 0
    assert report["paired_completions"] == 0
    assert len(report["participant_metrics"]) == 1
    assert report["participant_metrics"][0]["scored_trials"] == 4
    assert report["conditions"]["baseline"]["recruitment_target_met"] is False
