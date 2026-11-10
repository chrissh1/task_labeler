import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

from .db import begin_snapshot, milliseconds


def records(db):
    sessions = [
        dict(r)
        for r in db.execute("""SELECT s.*, d.version AS dataset_version,d.sha256 AS dataset_sha256,
        p.created_at AS participant_created_at FROM sessions s JOIN datasets d ON d.id=s.dataset_id
        JOIN participants p ON p.id=s.participant_id ORDER BY s.participant_id,s.condition""")
    ]
    trials = [
        dict(r)
        for r in db.execute("""SELECT t.*,q.subject,q.correct_index,q.original_answer,q.original_model,
        s.participant_id,s.condition,s.condition_order,s.demo,d.version AS dataset_version,d.sha256 AS dataset_sha256
        FROM trials t JOIN sessions s ON s.id=t.session_id JOIN datasets d ON d.id=s.dataset_id
        LEFT JOIN questions q ON q.id=t.question_id ORDER BY s.participant_id,s.condition,t.position""")
    ]
    calls = [
        dict(r)
        for r in db.execute("""SELECT a.*,t.practice,t.question_id,s.participant_id,s.condition,s.demo
        FROM ai_calls a JOIN trials t ON t.id=a.trial_id JOIN sessions s ON s.id=t.session_id
        ORDER BY a.started_at,a.id""")
    ]
    attention = [
        dict(r)
        for r in db.execute("""SELECT a.*,s.participant_id,s.condition,s.demo FROM attention a
        JOIN sessions s ON s.id=a.session_id ORDER BY a.id""")
    ]
    by_session = defaultdict(list)
    by_trial = defaultdict(list)
    for t in trials:
        by_session[t["session_id"]].append(t)
    for c in calls:
        by_trial[c["trial_id"]].append(c)
    for s in sessions:
        scored = [t for t in by_session[s["id"]] if not t["practice"]]
        s["completed"] = int(s["stage"] == "complete" and s["completed_at"] is not None)
        s["valid"] = int(
            not s["demo"]
            and s["completed"]
            and len(scored) == json.loads(s["config"])["trials_per_condition"]
            and all(t["submitted_at"] and t["confidence"] is not None for t in scored)
            and any(a["session_id"] == s["id"] and a["passed"] for a in attention)
            and any(t["practice"] and t["submitted_at"] for t in by_session[s["id"]])
        )
    session_map = {s["id"]: s for s in sessions}
    for t in trials:
        s = session_map[t["session_id"]]
        t.update(session_completed=s["completed"], session_valid=s["valid"])
        trial_calls = by_trial[t["id"]]
        suggestions = [
            c for c in trial_calls if c["kind"] == "suggestion" and c["status"] == "ok"
        ]
        initial = min(suggestions, key=lambda c: c["attempt"]) if suggestions else None
        exposures = [
            c
            for c in suggestions
            if c["acknowledged_at"]
            and (not t["submitted_at"] or c["acknowledged_at"] <= t["submitted_at"])
        ]
        t["suggestion_exposed"] = int(bool(exposures))
        t["initial_ai_answer"] = initial["answer_index"] if initial else None
        t["initial_ai_correct"] = (
            int(initial["answer_index"] == t["correct_index"])
            if initial and not t["practice"]
            else None
        )
        t["agreement_initial_ai"] = (
            int(t["selected_index"] == initial["answer_index"])
            if initial and t["submitted_at"]
            else None
        )
        t["first_suggestion_displayed_at"] = (
            min(c["acknowledged_at"] for c in exposures) if exposures else None
        )
        t["before_suggestion_ms"] = (
            milliseconds(t["first_visible_at"], t["first_suggestion_displayed_at"])
            if t["first_suggestion_displayed_at"] and t["first_visible_at"]
            else None
        )
        t["ai_failed_attempts"] = sum(
            c["status"] in ("error", "stale", "late_ok", "late_error")
            for c in trial_calls
        )
        t["ai_pending_attempts"] = sum(c["status"] == "pending" for c in trial_calls)
        t["followup_turns"] = len(
            {c["slot"] for c in trial_calls if c["kind"] == "chat"}
        )
        t["ai_statuses"] = ",".join(c["status"] for c in trial_calls)
    return sessions, trials, calls, attention


def safe_cell(value):
    if isinstance(value, str) and (
        value.lstrip().startswith(("=", "+", "-", "@"))
        or value.startswith(("\t", "\r", "\n"))
    ):
        return "'" + value
    return value


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: safe_cell(row.get(k)) for k in fields} for row in rows)


def export_data(db, path, include_demo=False):
    path = Path(path)
    if (
        getattr(db, "environment", None) == "preview"
        or db.execute("SELECT demo FROM datasets").fetchone()[0]
    ) and not include_demo:
        raise ValueError(
            "Demo database cannot enter study exports. Use --include-demo for a clearly marked demo export."
        )
    if path.exists() and any(path.iterdir()):
        raise ValueError("Export directory must be new or empty")
    path.mkdir(parents=True, exist_ok=True)
    begin_snapshot(db)
    try:
        sessions, trials, calls, attention = records(db)
        tables = {
            "sessions": sessions,
            "trials": [t for t in trials if not t["practice"]],
            "ai_interactions": calls,
            "attention_checks": attention,
            "participants": [
                dict(r)
                for r in db.execute(
                    "SELECT id AS participant_id,created_at FROM participants"
                )
            ],
        }
        for name, rows in tables.items():
            if not include_demo:
                rows = [r for r in rows if not r.get("demo")]
            write_csv(
                path / f"{name}.csv",
                rows,
                list(rows[0])
                if rows
                else {
                    "sessions": [
                        "id",
                        "participant_id",
                        "condition",
                        "completed",
                        "valid",
                        "demo",
                    ],
                    "trials": [
                        "id",
                        "session_id",
                        "participant_id",
                        "condition",
                        "question_id",
                        "session_valid",
                        "demo",
                    ],
                    "ai_interactions": [
                        "id",
                        "trial_id",
                        "participant_id",
                        "condition",
                        "practice",
                        "demo",
                        "status",
                    ],
                    "attention_checks": [
                        "id",
                        "session_id",
                        "participant_id",
                        "condition",
                        "passed",
                        "restart",
                        "demo",
                    ],
                    "participants": ["participant_id", "created_at"],
                }[name],
            )
        (path / "manifest.json").write_text(
            json.dumps(
                {
                    "demo_export": include_demo,
                    "dataset": dict(
                        db.execute(
                            "SELECT version,sha256,results_sha256,demo FROM datasets"
                        ).fetchone()
                    ),
                    "analysis": analyze(db),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        db.rollback()


def mean(values):
    return statistics.mean(values) if values else None


def analyze(db):
    sessions, trials, _calls, attention = records(db)
    valid = {s["id"] for s in sessions if s["valid"]}
    scored = [t for t in trials if t["session_id"] in valid and not t["practice"]]
    by_session = defaultdict(list)
    for t in scored:
        by_session[t["session_id"]].append(t)
    metrics = []
    for s in sessions:
        if s["id"] not in valid:
            continue
        rows = by_session[s["id"]]
        durations = [
            t["elapsed_ms"] / 1000 for t in rows if t["elapsed_ms"] is not None
        ]
        metrics.append(
            {
                "participant_id": s["participant_id"],
                "session_id": s["id"],
                "condition": s["condition"],
                "condition_order": s["condition_order"],
                "scored_trials": len(rows),
                "accuracy": mean([t["correct"] for t in rows]),
                "mean_confidence": mean([t["confidence"] for t in rows]),
                "median_elapsed_seconds": statistics.median(durations)
                if durations
                else None,
            }
        )
    conditions = {}
    for condition in ("baseline", "ai"):
        people = [m for m in metrics if m["condition"] == condition]
        assigned = [
            s for s in sessions if s["condition"] == condition and not s["demo"]
        ]
        conditions[condition] = {
            "assigned_participants": len(assigned),
            "started_participants": sum(bool(s["started_at"]) for s in assigned),
            "partial_started_sessions": sum(
                bool(s["started_at"]) and not s["completed"] for s in assigned
            ),
            "completed_sessions": sum(s["completed"] for s in assigned),
            "valid_completed_participants": len(people),
            "recruitment_target_met": len(people) >= 5,
            "mean_participant_accuracy": mean([m["accuracy"] for m in people]),
            "mean_participant_confidence": mean([m["mean_confidence"] for m in people]),
            "mean_participant_median_elapsed_seconds": mean(
                [
                    m["median_elapsed_seconds"]
                    for m in people
                    if m["median_elapsed_seconds"] is not None
                ]
            ),
        }
    completed_by_person = defaultdict(set)
    for m in metrics:
        completed_by_person[m["participant_id"]].add(m["condition"])
    subject_metrics = []
    groups = defaultdict(list)
    for t in scored:
        groups[(t["participant_id"], t["condition"], t["subject"])].append(t)
    for (pid, condition, subject), rows in groups.items():
        subject_metrics.append(
            {
                "participant_id": pid,
                "condition": condition,
                "subject": subject,
                "accuracy": mean([t["correct"] for t in rows]),
            }
        )
    subject_summary = []
    for condition in ("baseline", "ai"):
        for subject in ("formal_logic", "logical_fallacies"):
            rows = [
                r
                for r in subject_metrics
                if r["condition"] == condition and r["subject"] == subject
            ]
            subject_summary.append(
                {
                    "condition": condition,
                    "subject": subject,
                    "participants": len(rows),
                    "mean_participant_accuracy": mean([r["accuracy"] for r in rows]),
                }
            )
    ai_trials = [t for t in scored if t["condition"] == "ai"]
    original = [
        dict(r)
        for r in db.execute(
            "SELECT id,correct_index,original_answer FROM questions WHERE original_output IS NOT NULL"
        )
    ]
    sampled_ids = {t["question_id"] for t in scored}
    sampled = [r for r in original if r["id"] in sampled_ids]
    return {
        "policy": "Primary comparison: valid completed real sessions, assigned condition regardless of AI use/failure. No inferential tests.",
        "conditions": conditions,
        "paired_completions": sum(len(v) == 2 for v in completed_by_person.values()),
        "participant_metrics": metrics,
        "subject_summary": subject_summary,
        "subject_participant_metrics": subject_metrics,
        "condition_order_counts": {
            f"{c}_started_{order}": sum(
                s["condition"] == c and s["condition_order"] == order and not s["demo"]
                for s in sessions
            )
            for c in ("baseline", "ai")
            for order in (1, 2)
        },
        "ai_valid_scored_trials": {
            "trials": len(ai_trials),
            "suggestion_exposed": sum(t["suggestion_exposed"] for t in ai_trials),
            "failed_attempts": sum(t["ai_failed_attempts"] for t in ai_trials),
            "pending_attempts": sum(t["ai_pending_attempts"] for t in ai_trials),
            "trials_with_followups": sum(t["followup_turns"] > 0 for t in ai_trials),
            "followup_turns": sum(t["followup_turns"] for t in ai_trials),
            "agreement_with_initial_suggestion": mean(
                [
                    t["agreement_initial_ai"]
                    for t in ai_trials
                    if t["agreement_initial_ai"] is not None
                ]
            ),
            "agreement_note": "Descriptive trial-level agreement among available initial suggestions; not proof of reliance.",
            "live_initial_suggestion_accuracy": mean(
                [
                    t["initial_ai_correct"]
                    for t in ai_trials
                    if t["initial_ai_correct"] is not None
                ]
            ),
            "live_initial_suggestions_available": sum(
                t["initial_ai_correct"] is not None for t in ai_trials
            ),
        },
        "original_assignment2": {
            "full_benchmark_questions": len(original),
            "full_benchmark_accuracy": mean(
                [int(r["original_answer"] == r["correct_index"]) for r in original]
            ),
            "unique_sampled_questions_in_valid_sessions": len(sampled),
            "sampled_question_accuracy": mean(
                [int(r["original_answer"] == r["correct_index"]) for r in sampled]
            ),
        },
        "attention": {
            "attempts": sum(not a["demo"] for a in attention),
            "failures": sum(not a["passed"] and not a["demo"] for a in attention),
            "restarts": sum(a["restart"] and not a["demo"] for a in attention),
        },
    }
