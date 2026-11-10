# EECS 594 Assignment 3 — reasoning study

A Flask/Jinja application with local SQLite and Supabase Postgres support, comparing final-answer accuracy with no assistance
and live AI assistance. Default: four scored questions per condition, two per subject,
plus an identical instructions check and authored practice question in each condition.
No public deployment or real participant collection is performed by setup or tests.

## Vercel and Supabase

For the prepared serverless deployment, follow [the deployment guide](docs/VERCEL_SUPABASE.md).
It uses separate Supabase projects for preview and production, restricted runtime roles,
and checked database identities. Preview responses never enter real-study analysis.
The single-instance SQLite deployment below remains an alternative.

## Quick start: explicit development demo

Python 3.11+ is recommended. From `task_labeler`:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Edit .env: replace SECRET_KEY with output from:
python -c 'import secrets; print(secrets.token_urlsafe(48))'
# .env is ignored by git; the app deliberately does not load it automatically.
set -a
source .env
set +a
python -m study --database instance/demo.sqlite3 import demo_questions.json --version authored-demo-v1 --demo
flask --app wsgi run --host 127.0.0.1 --port 5000
```

Open `http://127.0.0.1:5000/`. Direct links are
`/study?condition=baseline` and `/study?condition=ai`.
The requested condition survives email entry. Neither completion page automatically
opens the other condition. Demo uses explicitly authored questions and a deterministic
fake provider, with a visible demo banner. It uses a separate database and cannot be
exported as real study data. Never share a demo link as a real collection link.

The local `instance/demo.sqlite3` and `instance/study.sqlite3` databases have already
been imported during implementation verification, with zero participant records.
Skip the corresponding import command when using these local files; imports intentionally
refuse to overwrite them. A fresh checkout needs the import commands below.

## Import the actual Assignment 2 dataset

The supplied `questions.csv` is byte-identical to
`../task_model_eval/final_run/questions.csv`: 200 questions, 100 per subject.
SHA-256: `e2837a54a6619a9b0a1965bf9bcfa4e438c1a8373e9203978cf24872265bfda4`.
The original evaluation is `../task_model_eval/final_run/results.csv`; provider code
is `../task_model_eval/pilot.py`; the report is `../task_model_eval/EECS 594 Assignment 2.pdf`
and machine-readable provenance is in `final_run/selection.json` and `ai.json`.
No MMLU resampling is performed.

```sh
python -m study --database instance/study.sqlite3 import questions.csv \
  --version assignment2-final-v1 \
  --results ../task_model_eval/final_run/results.csv
```

The importer validates every row, reports all row failures, and imports nothing on
failure. It requires at least 200 distinct real questions, supported subjects,
four nonempty ordered choices, unique IDs, and unique whitespace-normalized,
case-folded question text. The optional results file must match **all** IDs,
question text, subjects, choices, and answer keys exactly. It never drops invalid rows.
Source and evaluation bytes are preserved inside the database, with separate hashes.
Keep the original Assignment 2 directory for its report and selection metadata.
Questions are immutable. Import refuses a nonempty database, even before collection;
use a new database and version for a deliberate dataset change.

Accepted CSV columns:

```text
trial_id,subject,question,choice_A,choice_B,choice_C,choice_D,correct_answer
```

JSON accepts a list (or an object containing `questions`), for example:

```json
[{"question_id":"source:split:123", "subject":"formal_logic",
  "question":"Your actual question", "choices":["A text","B text","C text","D text"],
  "correct_answer":"B", "source":"source identifier", "split":"test"}]
```

Aliases: `question_id`/`trial_id`; `question`/`question_text`;
`correct_answer`/`correct_index`/`answer`; `choices` may be a JSON list in CSV.
Letter answers A–D are case-insensitive. Numeric answers require explicit
`--answer-base 0` or `--answer-base 1`, including numeric `correct_index` values;
no ambiguous numeric indexing is guessed. If IDs are absent, content hashes produce
stable IDs. Source/split are retained when supplied, or inferred from Assignment 2's
`subject:test:index` IDs. Optional `predicted_answer`, `model_output`, and `model`
are preserved; an empty prediction with a saved invalid original output counts as
incorrect in AI-only analysis. These fields and ground truth are server-only.

## Real study configuration

Set `STUDY_MODE=study`, `STUDY_DATABASE` to the imported real database, a persistent
random `SECRET_KEY`, and `GEMINI_API_KEY`. Study startup refuses missing credentials,
missing/undersized/demo datasets, or configuration changes after assignment begins.
The provider is never replaced with a fake when study credentials or calls fail.
A fake/custom provider in study mode is accepted only by the test factory.

| Variable | Default / meaning |
| --- | --- |
| `STUDY_MODE` | `demo`; explicitly set `study` for real collection |
| `STUDY_DATABASE` | `instance/<mode>.sqlite3`; use an absolute persistent path in production |
| `SECRET_KEY` | Required; at least 32 random characters outside tests; retain across restarts |
| `GEMINI_API_KEY` | Required in study mode; server-only |
| `TRIALS_PER_CONDITION` | 4; even integer 2–40, balanced equally by subject |
| `MAX_FOLLOWUPS` | 3 per trial, configurable 0–10 before collection |
| `COOKIE_SECURE` | 0 locally; set 1 behind production HTTPS |

The complete study configuration is frozen on first assignment creation (slightly
before first start). Changing trial count, prompt/model/settings, or follow-up limits
then requires a new study database/version. Pilot timing **before** collecting real
responses. There is no participant condition toggle, score reveal, or admin dashboard.

### Assignment and identity policy

Emails are trimmed and lowercased, without stripping dots or plus tags. A unique
normalized email resolves one internal participant ID. A signed, HttpOnly, SameSite
cookie remembers that ID; every trial/session endpoint checks it. Default exports
omit the email mapping. Email never enters model context or application logs.

This is **unverified identification**, not authentication. Someone entering another
person's email can resume that person's progress. Do not describe it as secure login.
Keep the database, backups, and free-text exports private. Chat users are asked not
to provide personal information; voluntary text is still potentially identifying.

On first study entry, one short transaction samples both assignments using system
randomness: eight distinct questions by default, four per condition, two per subject.
Each condition's order is independently shuffled and persisted before display.
Other participants sample independently. The practice item is outside the dataset.
Insufficient subject counts fail explicitly; no exclusion is relaxed.

Starting a condition means pressing **Continue to instructions check**. Assignment
creation alone does not start it. The first such action records condition order and
a UTC start timestamp; revisits and attention restarts never replace them. Refresh,
condition switching, and email re-entry retain the exact assignments and saved answers.
Completed conditions cannot produce new counted sessions.

The course's “same 6 tasks in the other condition” is interpreted as revisiting the
same six classmates' studies, not repeating identical question items here. This
study uses distinct subject-balanced sets to reduce answer recall and records actual
condition order for analysis. There is no enforced universal order.

### AI integration and recording

Assignment 2's recorded model is **`gemini-3.5-flash-lite`**, temperature **0**, maximum
output tokens **2048**, using Google Gen AI `generate_content` with JSON output.
The identifier is taken from the actual artifact, not a model-name guess. Model
availability and key access still need a real credentialed pilot; no billable calls
were made during implementation.

`study/ai.py` reuses this integration and generation configuration. The versioned
prompt adds trial-scoped clarification context and a strict 120-word / 1,800-character
explanation limit. It requests a short user-facing justification, not hidden
chain-of-thought. Input chat is limited to 600 characters. Questions and choice order
are sent through an explicit public-field allowlist. Answer keys, previous benchmark
outputs, emails, and participant responses are never in the model context. A fresh
context begins on each trial. Only acknowledged successful prior responses enter
follow-up context. AI does not select the participant's answer or display a confidence
percentage. Text is rendered with `textContent`, never HTML.

Provider timeout is 60 seconds, with SDK automatic retries disabled (`attempts=1`).
There are at most two application attempts per suggestion/follow-up slot, the second
requiring a manual retry. This differs from the offline Assignment 2 backoff loop to
keep waiting bounded. See the provider's [HTTP options and retry implementation](https://github.com/googleapis/python-genai/blob/main/google/genai/_api_client.py)
and [structured-output SDK documentation](https://googleapis.github.io/python-genai/#json-response-schema).

A persisted database request reservation deduplicates refreshes, concurrent requests, and double
clicks before provider I/O. No transaction remains open during the call. Successful
suggestions are reused. Calls, exact context, raw output, model/settings, parsed display
text, usage metadata, latency, and sanitized exception type are saved before returning.
A browser acknowledgment after DOM insertion/paint separately records exposure; it
is deduplicated and rejected after submission. This indicates browser rendering, not
proof of human attention. Late responses remain stored without exposure.

After 90 seconds, pending requests become `stale` on the next request/poll. They are
never automatically reissued. A manual retry can duplicate provider billing if a
process died after the provider accepted a request: exactly-once external billing
cannot be guaranteed without provider idempotency. The maximum remains two attempts.
A response arriving after a stale notice preserves that notice's display record and
stores the late raw response as `late_ok`/`late_error`; it is not shown as a suggestion.
Restart recovery and these concurrency cases are tested.

### Attention and validity policy (set before collection)

The separate check asks for option C, without AI. Every attempt is recorded. One
failure gets explanatory retry feedback. A second consecutive failure returns to
instructions and records a restart; the participant may attempt again. A subsequent
pass permits practice. No one is excluded for a single mistake or for restarts.

A **valid completed session** is real (not demo), has passed the attention check,
has submitted practice and every configured scored question with confidence, and
has pressed Finish on the optional-comment page. The comment itself may be empty.
Unfinished sessions are reported separately, not silently counted as valid.
No accuracy, speed, AI-use, or attention-failure threshold is applied post hoc.
Practice and attention trials are excluded from accuracy/confidence/timing summaries.
AI-available sessions remain in the primary assigned-condition comparison even when
calls fail or suggestions are not exposed. Aim for at least **five valid completed
participants per condition**; analysis explicitly reports whether this is achieved.

## Owner-only export, backup, and analysis

There are no public data download endpoints. Run these on the server or a private copy:

```sh
python -m study --database /var/lib/task-labeler/study.sqlite3 backup backups/study-2026-11-09.sqlite3
python -m study --database /var/lib/task-labeler/study.sqlite3 export exports/run-01
python -m study --database /var/lib/task-labeler/study.sqlite3 analyze > exports/analysis.json
```

Export requires a new/empty directory. A read transaction gives one consistent
snapshot. UTF-8 CSVs: scored `trials.csv` (including unanswered assignments),
`sessions.csv`, `participants.csv` (no email), `ai_interactions.csv`,
`attention_checks.csv`, plus a manifest and analysis. AI interaction exports retain
practice calls with an explicit flag for auditing; analysis excludes them.
Formula-like text is prefixed with an apostrophe for spreadsheet safety; original
text remains intact in the database. Even non-email exports can contain identifying free text.
Demo export requires `--include-demo`, is marked in its manifest and row flags,
and remains excluded from analysis. See [DATA_DICTIONARY.md](DATA_DICTIONARY.md).

Analysis computes each valid participant's accuracy, mean confidence, and median
elapsed trial duration per condition, then averages those participant summaries.
It reports paired completions, subject-level participant summaries, actual condition
order, AI failures/exposure/follow-ups, and descriptive agreement with the initial
successful live suggestion. Agreement is not evidence of reliance. It separately
reports original AI accuracy on unique sampled questions in valid sessions, the full
original evaluated benchmark, and live initial-suggestion accuracy with its denominator.
No significance test is implemented, and trials are not treated as independent people.

Use the SQLite online backup command above; do not copy a live `.sqlite3` file alone
while WAL writes are active. Store backups off the mounted disk with restricted access.
To recover: stop the service, restore a verified backup to a **new path**, point
`STUDY_DATABASE` to it, retain the secret/configuration, run an integrity check and
resume check, then restart. Keep the prior database until recovery is confirmed.

## Alternative: single-instance SQLite deployment (not deployed)

Use one Linux instance with Python, HTTPS termination, and a persistent mounted disk
at `/var/lib/task-labeler`. Ensure the service account can write the database's directory
and WAL/SHM sidecars. Do not use ephemeral container storage or multiple independent
SQLite replicas. SQLite enables foreign keys, WAL, and a 5-second busy timeout.
Import the real dataset before starting. Keep credentials in the hosting environment.

```sh
export STUDY_MODE=study
export STUDY_DATABASE=/var/lib/task-labeler/study.sqlite3
export COOKIE_SECURE=1
# Set SECRET_KEY and GEMINI_API_KEY privately in the service environment.
gunicorn --bind 127.0.0.1:8000 --workers 1 --worker-class gthread --threads 8 \
  --timeout 120 --graceful-timeout 120 wsgi:app
```

Proxy HTTPS to port 8000, preserving the public host; use an upstream read timeout
of at least 90 seconds for live AI requests. Eight threads allow ordinary answer
saves alongside modest classroom AI concurrency; pilot under the intended load.
Do not configure request-body logging. Gunicorn access logging is disabled by default;
never add email/chat bodies to it. Put health checks on `/` (no participant state).
Restart the same instance with the same mounted disk and configuration. An ephemeral
filesystem **does not preserve results**. Back up before releases; no automatic schema
migration or rolling multi-instance deployment is included in this small app.

## Verification and pilot checklist

```sh
python -m pytest tests -q
python -m compileall -q study wsgi.py
node --check study/static/study.js
# Requires PostgreSQL binaries; starts/stops a disposable server:
python scripts/check_postgres.py
```

Tests use disposable databases, synthetic identities, real question import where
relevant, deterministic fake AI, and bounded synchronization. No network or billable
calls. They cover both full conditions via HTTP, attention restarts, email reuse,
ownership/CSRF, immutable assignments, input validation, no answer-key leakage,
submission idempotency, frozen configuration, AI failures/retries/late responses,
concurrent submission during AI I/O, exports, and demo/practice exclusions.

Verification covers the SQLite regression suite and a disposable real Postgres server,
including both flows, role permissions, preview exclusion, swapped database identity,
concurrent AI/submission behavior, snapshot isolation, and backup/restore. See the
[deployment guide](docs/VERCEL_SUPABASE.md) for commands and remaining hosted checks.
The declared Psycopg 3.3.6 matches the existing `data_labeler` implementation. Local
verification reused its installed dependencies because package-registry DNS access
blocked a fresh install. Browser/mobile/keyboard, hosted Supabase pooler/Vercel, and
credentialed AI timing checks remain outstanding. No real AI calls or public deployment
were performed.

Before sharing links:

- Install dependencies in a clean environment; start the production WSGI command.
- Verify the 200-question import/hash and the exact model identifier with the real key.
- Time both conditions (instructions, practice, confidence, AI latency included).
  Aim for combined completion under ten minutes; adjust count **before** collection.
- Complete both direct links in each order; verify no auto-redirect to the second part.
- Use keyboard only; inspect desktop and widths 320, 375, 414, and 768 px. Check focus,
  radio labels, readable contrast, loading/errors, and no horizontal scrolling.
- Refresh an unanswered trial, switch conditions, sign out/in, and use another device
  with the same normalized email. Confirm assignments and saved progress match.
- Interrupt a save, retry it, and simulate a lost response after a successful save.
  Confirm no duplicate or changed answer. Submit while AI is still pending.
- Compare every displayed suggestion/chat/error against `ai_calls.displayed_text` and
  acknowledgments. Confirm late/hidden responses are not counted as exposed.
- Restart the service and confirm completed responses survive on the persistent disk.
  Export, inspect joins, run analysis, and restore a backup to a separate path.
- Recruit at least five valid completions per condition; do not substitute demo/test
  sessions or fabricate missing data. Record the final timing pilot and any protocol
  decisions before collection.
