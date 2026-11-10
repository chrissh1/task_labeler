import hmac
import json
import math
import os
import secrets
from pathlib import Path

from flask import (
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.exceptions import HTTPException

from .dataset import PRACTICE
from .db import DATABASE_ERRORS, connect, milliseconds, now, transaction
from .deployment import deployment_settings
from .service import allocate, current_trial, normalize_email, session_state, uid


def create_app(config=None, provider=None):
    deployment = deployment_settings(os.environ)
    app = Flask(__name__, static_folder=None if deployment.hosted else "static")
    if deployment.hosted:
        app.add_url_rule("/static/<path:filename>", endpoint="static", build_only=True)
    mode = os.getenv("STUDY_MODE", "demo")
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY"),
        MODE=mode,
        DATABASE=os.getenv(
            "STUDY_DATABASE",
            str(Path(__file__).resolve().parents[1] / "instance" / f"{mode}.sqlite3"),
        ),
        TRIALS_PER_CONDITION=int(os.getenv("TRIALS_PER_CONDITION", "4")),
        MAX_FOLLOWUPS=int(os.getenv("MAX_FOLLOWUPS", "3")),
        MAX_AI_ATTEMPTS=2,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
        MAX_CONTENT_LENGTH=16384,
    )
    app.config.update(config or {})
    if deployment.database:
        app.config["DATABASE"] = deployment.database
    if deployment.environment == "production" and app.config["MODE"] != "study":
        raise ValueError("production deployments require study mode.")
    if deployment.environment != "local":
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_NAME"] = "study_" + deployment.environment
    app.extensions["deployment"] = deployment
    app.context_processor(lambda: {"preview": deployment.environment == "preview"})
    if app.config["MODE"] not in ("demo", "study"):
        raise ValueError("STUDY_MODE must be demo or study")
    if not app.config["SECRET_KEY"]:
        raise ValueError("Set a persistent SECRET_KEY (at least 32 random characters).")
    if not app.testing and len(app.config["SECRET_KEY"]) < 32:
        raise ValueError("SECRET_KEY must have at least 32 characters")
    count = app.config["TRIALS_PER_CONDITION"]
    if type(count) is not int or count < 2 or count > 40 or count % 2:
        raise ValueError("TRIALS_PER_CONDITION must be even, between 2 and 40")
    if not 0 <= app.config["MAX_FOLLOWUPS"] <= 10:
        raise ValueError("MAX_FOLLOWUPS must be 0–10")
    from .ai import (
        MODEL,
        PROMPT_VERSION,
        SETTINGS,
        FakeProvider,
        GeminiProvider,
        register_ai,
    )

    if provider is not None and app.config["MODE"] == "study" and not app.testing:
        raise ValueError("Fake/custom providers are allowed only in demo or tests")
    app.extensions["provider"] = provider or (
        FakeProvider() if app.config["MODE"] == "demo" else GeminiProvider()
    )
    app.extensions["study_config"] = {
        "version": "study-v1",
        "trials_per_condition": count,
        "max_followups": app.config["MAX_FOLLOWUPS"],
        "max_ai_attempts": app.config["MAX_AI_ATTEMPTS"],
        "model": MODEL,
        "settings": SETTINGS,
        "prompt_version": PROMPT_VERSION,
    }
    db = connect(app.config["DATABASE"], deployment)
    dataset = db.execute("SELECT * FROM datasets").fetchone()
    if not dataset or bool(dataset["demo"]) != (app.config["MODE"] == "demo"):
        db.close()
        raise ValueError(
            "Import a validated dataset into the correct demo/study database before startup."
        )
    frozen = db.execute("SELECT config FROM study_config").fetchone()
    if frozen and frozen[0] != json.dumps(
        app.extensions["study_config"], sort_keys=True
    ):
        db.close()
        raise ValueError(
            "Study configuration is frozen; restore original settings or use a new database."
        )
    if (
        app.config["MODE"] == "study"
        and db.execute("SELECT count(*) FROM questions").fetchone()[0] < 200
    ):
        db.close()
        raise ValueError("Study mode requires 200 real questions")
    db.close()

    def get_db():
        if "db" not in g:
            g.db = connect(app.config["DATABASE"], deployment)
        return g.db

    app.extensions["get_db"] = get_db

    @app.teardown_appcontext
    def close_db(error=None):
        if "db" in g:
            g.db.close()

    @app.before_request
    def protect():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        if request.method == "POST":
            token = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
            if not isinstance(token, str) or not hmac.compare_digest(
                token, session["csrf"]
            ):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    @app.errorhandler(ValueError)
    def bad_value(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    def database_error(error):
        return jsonify(
            error="Saving is temporarily unavailable. Your selections remain here; please retry."
        ), 503

    for error_type in DATABASE_ERRORS:
        app.register_error_handler(error_type, database_error)

    def data():
        value = request.get_json(silent=True) if request.is_json else request.form
        if not isinstance(value, dict) and not hasattr(value, "get"):
            abort(400)
        return value

    def owned_session(sid):
        row = (
            get_db()
            .execute(
                "SELECT * FROM sessions WHERE id=? AND participant_id=?",
                (sid, session.get("participant_id")),
            )
            .fetchone()
        )
        if not row:
            abort(404)
        return row

    def owned_trial(tid, active=True):
        db = get_db()
        trial = db.execute(
            "SELECT t.* FROM trials t JOIN sessions s ON s.id=t.session_id WHERE t.id=? AND s.participant_id=?",
            (tid, session.get("participant_id")),
        ).fetchone()
        if not trial:
            abort(404)
        s = owned_session(trial["session_id"])
        if active:
            current = current_trial(db, s["id"])
            if s["stage"] != "trials" or not current or current["id"] != tid:
                abort(409, "This question is no longer active.")
        return trial, s

    app.extensions.update(owned_trial=owned_trial, request_data=data)

    @app.get("/")
    def home():
        states = []
        if session.get("participant_id"):
            states = [
                session_state(get_db(), r)
                for r in get_db().execute(
                    "SELECT * FROM sessions WHERE participant_id=? ORDER BY condition",
                    (session["participant_id"],),
                )
            ]
        return render_template(
            "home.html",
            states=states,
            identified=bool(session.get("participant_id")),
            requested=request.args.get("condition")
            if request.args.get("condition") in ("ai", "baseline")
            else "",
            demo=app.config["MODE"] == "demo",
        )

    @app.post("/identify")
    def identify():
        body = data()
        try:
            email = normalize_email(body.get("email"))
        except TypeError:
            abort(400, "Enter a valid email address.")
        db = get_db()
        with transaction(db):
            db.execute(
                "INSERT INTO participants VALUES(?,?,?) ON CONFLICT(email) DO NOTHING",
                (uid(), email, now()),
            )
            pid = db.execute(
                "SELECT id FROM participants WHERE email=?", (email,)
            ).fetchone()[0]
        session.clear()
        session["participant_id"] = pid
        session["csrf"] = secrets.token_urlsafe(32)
        condition = body.get("condition")
        target = (
            url_for("study", condition=condition)
            if condition in ("baseline", "ai")
            else url_for("home")
        )
        return jsonify(redirect=target) if request.is_json else redirect(target, 303)

    @app.post("/signout")
    def signout():
        session.clear()
        return redirect(url_for("home"), 303)

    @app.get("/study")
    def study():
        condition = request.args.get("condition")
        if condition not in ("baseline", "ai"):
            abort(400, "Choose a study condition.")
        if not session.get("participant_id"):
            return redirect(url_for("home", condition=condition))
        with transaction(get_db()):
            allocate(
                get_db(),
                session["participant_id"],
                app.extensions["study_config"],
                app.config["MODE"] == "demo" or deployment.environment == "preview",
            )
        row = (
            get_db()
            .execute(
                "SELECT * FROM sessions WHERE participant_id=? AND condition=?",
                (session["participant_id"], condition),
            )
            .fetchone()
        )
        return render_template(
            "study.html",
            state=session_state(get_db(), row),
            demo=app.config["MODE"] == "demo",
            max_followups=app.config["MAX_FOLLOWUPS"],
        )

    @app.get("/api/state")
    def state():
        row = (
            get_db()
            .execute(
                "SELECT * FROM sessions WHERE participant_id=? AND condition=?",
                (session.get("participant_id"), request.args.get("condition")),
            )
            .fetchone()
        )
        if not row:
            abort(404)
        return jsonify(session_state(get_db(), row))

    @app.post("/sessions/<sid>/begin")
    def begin(sid):
        db = get_db()
        with transaction(db):
            row = owned_session(sid)
            if row["stage"] != "instructions":
                abort(409)
            order = (
                db.execute(
                    "SELECT count(*) FROM sessions WHERE participant_id=? AND started_at IS NOT NULL",
                    (row["participant_id"],),
                ).fetchone()[0]
                + 1
            )
            db.execute(
                "UPDATE sessions SET started_at=coalesce(started_at,?),condition_order=coalesce(condition_order,?),stage='attention',attention_failures=0 WHERE id=?",
                (now(), order, sid),
            )
        return jsonify(ok=True)

    @app.post("/sessions/<sid>/attention")
    def attention(sid):
        answer = data().get("answer")
        validate_integer(answer, 0, 3)
        db = get_db()
        with transaction(db):
            row = owned_session(sid)
            if row["stage"] != "attention":
                abort(409)
            passed = answer == 2
            failures = row["attention_failures"] + int(not passed)
            restart = not passed and failures >= 2
            db.execute(
                "INSERT INTO attention(session_id,selected_index,passed,restart,created_at) VALUES(?,?,?,?,?)",
                (sid, answer, int(passed), int(restart), now()),
            )
            stage = "trials" if passed else ("instructions" if restart else "attention")
            db.execute(
                "UPDATE sessions SET stage=?,attention_failures=? WHERE id=?",
                (stage, failures, sid),
            )
        return jsonify(ok=True, passed=passed, restart=restart)

    @app.post("/trials/<tid>/event")
    def event(tid):
        body = data()
        event_id = body.get("id")
        page_id = body.get("page_id")
        kind = body.get("kind")
        duration = body.get("visible_ms")
        if (
            not valid_id(event_id)
            or not valid_id(page_id)
            or kind
            not in (
                "visible",
                "hidden",
                "checkpoint",
                "answer_change",
                "confidence_change",
                "leave",
            )
        ):
            abort(400)
        if (
            type(duration) not in (int, float)
            or not math.isfinite(duration)
            or not 0 <= duration <= 86400000
        ):
            abort(400)
        payload = {}
        if kind in ("answer_change", "confidence_change"):
            value = body.get("value")
            validate_integer(
                value,
                0 if kind == "answer_change" else 1,
                3 if kind == "answer_change" else 5,
            )
            payload["value"] = value
        db = get_db()
        with transaction(db):
            owned_trial(tid)
            stamp = now()
            existing = db.execute(
                "SELECT * FROM events WHERE id=?", (event_id,)
            ).fetchone()
            if existing:
                if existing["trial_id"] != tid or existing["page_id"] != page_id:
                    abort(409)
                return jsonify(ok=True)
            db.execute(
                "UPDATE trials SET first_visible_at=coalesce(first_visible_at,?) WHERE id=?",
                (stamp, tid),
            )
            previous = db.execute(
                "SELECT * FROM checkpoints WHERE trial_id=? AND page_id=?",
                (tid, page_id),
            ).fetchone()
            # Bound client checkpoints by server wall time; counts are still approximate across tabs.
            old = previous["visible_ms"] if previous else 0
            limit = (
                milliseconds(previous["updated_at"], stamp) + 1000 if previous else 1000
            )
            accepted = max(old, min(duration, old + limit))
            db.execute(
                "INSERT INTO checkpoints VALUES(?,?,?,?) ON CONFLICT(trial_id,page_id) DO UPDATE SET visible_ms=excluded.visible_ms,updated_at=excluded.updated_at",
                (tid, page_id, accepted, stamp),
            )
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                (event_id, tid, page_id, kind, accepted, stamp, json.dumps(payload)),
            )
            db.execute(
                "UPDATE trials SET visible_ms=(SELECT sum(visible_ms) FROM checkpoints WHERE trial_id=?) WHERE id=?",
                (tid, tid),
            )
        return jsonify(ok=True)

    @app.post("/trials/<tid>/submit")
    def submit(tid):
        body = data()
        answer = body.get("answer")
        confidence = body.get("confidence")
        validate_integer(answer, 0, 3)
        validate_integer(confidence, 1, 5)
        db = get_db()
        with transaction(db):
            trial, s = owned_trial(tid, False)
            if trial["submitted_at"]:
                if (
                    trial["selected_index"] != answer
                    or trial["confidence"] != confidence
                ):
                    abort(409, "An answer has already been saved.")
                return jsonify(ok=True)
            owned_trial(tid)
            if not trial["first_visible_at"]:
                abort(409, "Wait for this question to finish loading, then retry.")
            correct = (
                PRACTICE["correct_index"]
                if trial["practice"]
                else db.execute(
                    "SELECT correct_index FROM questions WHERE id=?",
                    (trial["question_id"],),
                ).fetchone()[0]
            )
            stamp = now()
            elapsed = milliseconds(trial["first_visible_at"], stamp)
            db.execute(
                "UPDATE trials SET selected_index=?,confidence=?,submitted_at=?,correct=?,elapsed_ms=?,visible_ms=CASE WHEN visible_ms < ? THEN visible_ms ELSE ? END WHERE id=?",
                (
                    answer,
                    confidence,
                    stamp,
                    int(answer == correct),
                    elapsed,
                    elapsed,
                    elapsed,
                    tid,
                ),
            )
            if not current_trial(db, s["id"]):
                db.execute("UPDATE sessions SET stage='comment' WHERE id=?", (s["id"],))
        return jsonify(ok=True)

    @app.post("/sessions/<sid>/complete")
    def complete(sid):
        comment = data().get("comment", "")
        if not isinstance(comment, str) or len(comment) > 2000:
            abort(400)
        with transaction(get_db()):
            row = owned_session(sid)
            if row["stage"] == "complete":
                return jsonify(ok=True)
            if row["stage"] != "comment":
                abort(409)
            get_db().execute(
                "UPDATE sessions SET comment=?,completed_at=?,stage='complete' WHERE id=?",
                (comment, now(), sid),
            )
        return jsonify(ok=True)

    register_ai(app)
    return app


def validate_integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        abort(400, "Choose a valid option for each required field.")


def valid_id(value):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 100
        and all(c.isalnum() or c in "-_" for c in value)
    )
