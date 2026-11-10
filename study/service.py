import json
import random
import re
import uuid

from .dataset import PRACTICE, SUBJECTS
from .db import now


def uid():
    return str(uuid.uuid4())


def normalize_email(value):
    if not isinstance(value, str):
        raise TypeError("Enter a valid email address.")
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(
        r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}",
        value,
    ):
        raise ValueError("Enter a valid email address.")
    return value


def allocate(db, participant, config, demo):
    existing = db.execute(
        "SELECT 1 FROM sessions WHERE participant_id=?", (participant,)
    ).fetchone()
    if existing:
        return
    count = config["trials_per_condition"]
    per_subject = count // 2
    allocations = {"baseline": [], "ai": []}
    rng = random.SystemRandom()
    for subject in SUBJECTS:
        pool = [
            r[0]
            for r in db.execute("SELECT id FROM questions WHERE subject=?", (subject,))
        ]
        if len(pool) < count:
            raise ValueError(
                f"Configuration error: need {count} distinct {subject} questions."
            )
        sampled = rng.sample(pool, count)
        allocations["baseline"].extend(sampled[:per_subject])
        allocations["ai"].extend(sampled[per_subject:])
    encoded = json.dumps(config, sort_keys=True)
    frozen = db.execute("SELECT config FROM study_config WHERE id=1").fetchone()
    if frozen and frozen[0] != encoded:
        raise ValueError("Study configuration is frozen. Restore original settings.")
    db.execute(
        "INSERT INTO study_config VALUES(1,?) ON CONFLICT(id) DO NOTHING", (encoded,)
    )
    for condition, questions in allocations.items():
        rng.shuffle(questions)
        sid = uid()
        db.execute(
            "INSERT INTO sessions(id,participant_id,condition,dataset_id,config,demo,created_at) VALUES(?,?,?,1,?,?,?)",
            (sid, participant, condition, encoded, int(demo), now()),
        )
        db.execute(
            "INSERT INTO trials(id,session_id,position,practice) VALUES(?,?,0,1)",
            (uid(), sid),
        )
        for position, question in enumerate(questions, 1):
            db.execute(
                "INSERT INTO trials(id,session_id,question_id,position) VALUES(?,?,?,?)",
                (uid(), sid, question, position),
            )


def current_trial(db, sid):
    return db.execute(
        "SELECT * FROM trials WHERE session_id=? AND submitted_at IS NULL ORDER BY position LIMIT 1",
        (sid,),
    ).fetchone()


def public_question(db, trial):
    q = (
        PRACTICE
        if trial["practice"]
        else db.execute(
            "SELECT question,choices FROM questions WHERE id=?", (trial["question_id"],)
        ).fetchone()
    )
    return {
        "question": q["question"],
        "choices": q["choices"] if trial["practice"] else json.loads(q["choices"]),
    }


def session_state(db, row):
    answered = db.execute(
        "SELECT count(*) FROM trials WHERE session_id=? AND practice=0 AND submitted_at IS NOT NULL",
        (row["id"],),
    ).fetchone()[0]
    output = {
        "session_id": row["id"],
        "condition": row["condition"],
        "stage": row["stage"],
        "started": bool(row["started_at"]),
        "answered": answered,
        "total": json.loads(row["config"])["trials_per_condition"],
    }
    if row["stage"] == "trials":
        trial = current_trial(db, row["id"])
        if trial:
            output["trial"] = {
                **public_question(db, trial),
                "id": trial["id"],
                "position": trial["position"],
                "practice": bool(trial["practice"]),
            }
    return output
