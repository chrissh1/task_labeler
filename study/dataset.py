import csv
import hashlib
import io
import json
from pathlib import Path

from .db import now, transaction

SUBJECTS = ("formal_logic", "logical_fallacies")
LETTERS = "ABCD"
PRACTICE = {
    "question": "Practice: Which number is even?",
    "choices": ["3", "4", "5", "7"],
    "correct_index": 1,
}


def answer_index(value, base=None):
    if (
        isinstance(value, str)
        and value.strip().upper() in LETTERS
        and len(value.strip()) == 1
    ):
        return LETTERS.index(value.strip().upper())
    if base not in (0, 1):
        raise ValueError("numeric answers require explicit --answer-base 0 or 1")
    if isinstance(value, bool) or str(value).strip() not in ("0", "1", "2", "3", "4"):
        raise ValueError("invalid answer")
    result = int(value) - base
    if result not in range(4):
        raise ValueError("answer outside four choices")
    return result


def normalize(rows, version, sha, answer_base=None):
    output, errors, ids, contents = [], [], set(), set()
    for number, row in enumerate(rows, 1):
        try:
            question = row.get("question", row.get("question_text"))
            if not isinstance(question, str) or not question.strip():
                raise ValueError("missing question")
            subject = row.get("subject")
            if subject not in SUBJECTS:
                raise ValueError("unsupported subject")
            choices = row.get("choices")
            if isinstance(choices, str):
                choices = json.loads(choices)
            if choices is None:
                choices = [row.get(f"choice_{letter}") for letter in LETTERS]
            if (
                not isinstance(choices, list)
                or len(choices) != 4
                or any(not isinstance(c, str) or not c.strip() for c in choices)
            ):
                raise ValueError("expected four nonempty ordered choices")
            digest = hashlib.sha256(
                " ".join(question.split()).casefold().encode()
            ).hexdigest()
            key = (
                row.get("question_id", row.get("trial_id"))
                or f"{subject}:sha256:{digest}"
            )
            if not isinstance(key, str) or key in ids:
                raise ValueError("invalid or duplicate question ID")
            if digest in contents:
                raise ValueError("duplicate normalized question content")
            correct = answer_index(
                row.get("correct_answer", row.get("correct_index", row.get("answer"))),
                answer_base,
            )
            prediction = row.get("predicted_answer", row.get("original_answer"))
            original = (
                answer_index(prediction, answer_base)
                if prediction not in (None, "")
                else None
            )
            output.append(
                {
                    "id": key,
                    "subject": subject,
                    "question": question,
                    "choices": choices,
                    "correct_index": correct,
                    "source": row.get(
                        "source", "cais/mmlu" if ":test:" in key else None
                    ),
                    "split": row.get("split", "test" if ":test:" in key else None),
                    "original_answer": original,
                    "original_output": row.get(
                        "model_output", row.get("original_output")
                    ),
                    "original_model": row.get("model"),
                    "content_hash": digest,
                    "version": version,
                    "sha256": sha,
                }
            )
            ids.add(key)
            contents.add(digest)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"row {number}: {exc}")
    if errors:
        raise ValueError(
            f"{len(rows)} rows; {len(errors)} invalid; no rows imported.\n"
            + "\n".join(errors)
        )
    return output


def read_rows(path):
    raw = Path(path).read_bytes()
    if Path(path).suffix.lower() == ".json":
        rows = json.loads(raw)
        if isinstance(rows, dict):
            rows = rows.get("questions")
    else:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("expected a list of question objects")
    return raw, rows


def import_dataset(db, path, version, results=None, answer_base=None, demo=False):
    raw, rows = read_rows(path)
    sha = hashlib.sha256(raw).hexdigest()
    normalized = normalize(rows, version, sha, answer_base)
    result_raw = None
    if results:
        result_raw, result_rows = read_rows(results)
        evaluated = normalize(result_rows, version, sha, answer_base)
        by_id = {r["id"]: r for r in evaluated}
        if set(by_id) != {r["id"] for r in normalized}:
            raise ValueError("evaluation IDs must exactly match supplied dataset")
        for question in normalized:
            old = by_id[question["id"]]
            if any(
                question[k] != old[k]
                for k in ("subject", "question", "choices", "correct_index")
            ):
                raise ValueError(f"evaluation content mismatch: {question['id']}")
            for key in ("original_answer", "original_output", "original_model"):
                question[key] = old[key]
    if not demo and len(normalized) < 200:
        raise ValueError(
            f"production requires at least 200 distinct questions; found {len(normalized)}"
        )
    with transaction(db):
        if db.execute("SELECT 1 FROM datasets").fetchone():
            raise ValueError(
                "dataset already imported; use a separate database for a new version"
            )
        db.execute(
            "INSERT INTO datasets VALUES (1,?,?,?,?,?,?,?,?)",
            (
                version,
                sha,
                Path(path).name,
                raw,
                result_raw,
                hashlib.sha256(result_raw).hexdigest() if result_raw else None,
                int(demo),
                now(),
            ),
        )
        for q in normalized:
            db.execute(
                "INSERT INTO questions VALUES (?,1,?,?,?,?,?,?,?,?,?,?)",
                (
                    q["id"],
                    q["subject"],
                    q["question"],
                    json.dumps(q["choices"]),
                    q["correct_index"],
                    q["source"],
                    q["split"],
                    q["original_answer"],
                    q["original_output"],
                    q["original_model"],
                    q["content_hash"],
                ),
            )
    return {
        "count": len(normalized),
        "version": version,
        "sha256": sha,
        "subjects": {s: sum(q["subject"] == s for q in normalized) for s in SUBJECTS},
    }
