from pathlib import Path

import pytest

from study.dataset import import_dataset, normalize
from study.db import connect

ROOT = Path(__file__).resolve().parents[1]


def row(**changes):
    return dict(
        trial_id="x",
        subject="formal_logic",
        question="Question?",
        choices=["a", "b", "c", "d"],
        correct_answer="C",
        **changes,
    )


def test_normalizes_letters_and_explicit_numeric_bases():
    assert normalize([row()], "v1", "sha")[0]["correct_index"] == 2
    r = row()
    r["correct_answer"] = 1
    assert normalize([r], "v1", "sha", answer_base=1)[0]["correct_index"] == 0
    assert normalize([r], "v1", "sha", answer_base=0)[0]["correct_index"] == 1
    with pytest.raises(ValueError, match="base"):
        normalize([r], "v1", "sha")


@pytest.mark.parametrize(
    "change",
    [
        {"subject": "other"},
        {"choices": ["a"]},
        {"correct_answer": "E"},
        {"question": ""},
    ],
)
def test_rejects_invalid_rows(change):
    r = row()
    r.update(change)
    with pytest.raises(ValueError):
        normalize([r], "v", "s")


def test_rejects_duplicates_and_reports_all_errors():
    with pytest.raises(ValueError, match="row 2"):
        normalize([row(), row()], "v", "s")


def test_real_dataset_import_and_original_output_join(tmp_path):
    db = connect(tmp_path / "study.db")
    info = import_dataset(
        db,
        ROOT / "questions.csv",
        "assignment2",
        results=ROOT.parent / "task_model_eval/final_run/results.csv",
    )
    assert info["count"] == 200
    assert (
        db.execute(
            "select count(*) from questions where original_answer is not null"
        ).fetchone()[0]
        == 200
    )
    assert (
        db.execute("select count(distinct subject) from questions").fetchone()[0] == 2
    )
    with pytest.raises(ValueError):
        import_dataset(db, ROOT / "questions.csv", "changed")


def test_import_failure_is_atomic_and_small_real_dataset_rejected(tmp_path):
    import json

    db = connect(tmp_path / "study.db")
    path = tmp_path / "small.json"
    path.write_text(json.dumps([row()]))
    with pytest.raises(ValueError, match="200"):
        import_dataset(db, path, "small")
    assert db.execute("select count(*) from datasets").fetchone()[0] == 0
    invalid = row()
    invalid["correct_answer"] = "Z"
    path.write_text(json.dumps([invalid, row()]))
    with pytest.raises(ValueError, match="row 1"):
        import_dataset(db, path, "invalid", demo=True)
    assert db.execute("select count(*) from questions").fetchone()[0] == 0
