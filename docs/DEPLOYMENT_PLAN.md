# Vercel / Supabase preparation plan

Scope: retain four scored questions per condition, current UI, email-only identification,
AI model/settings, and local SQLite. Add a Postgres backend for Vercel. No public deployment,
credential creation, account changes, or real participant collection in this work.

## Boundaries and requirements

- `study/db.py`: parameterized Postgres adapter; short write transactions preserve the
  existing serialization guarantee, including across serverless instances; no model I/O
  inside write transactions. Read exports use a consistent snapshot. Disable prepared
  statements for Supabase transaction pooling and bound connection/statement/lock waits.
- `study/postgres.sql`: explicit owner-run initialization in a private schema. Schema
  version plus immutable database identity/environment marker. Runtime role can read
  questions and write study responses but cannot initialize/change schema or markers.
- `study/deployment.py`: Vercel environment is authoritative; preview selects only its
  preview URL, production only its production URL. Reject missing URLs, SQLite on Vercel,
  and environment/identity mismatch before app data writes. Never fall back to production.
- `study/__init__.py`, import/export/analysis CLI: work with either backend; preview
  responses are marked non-study and excluded from primary analysis/export. Credentials
  never appear in browser responses or logs. No migrations run during application startup.
- `vercel.json`, static asset preparation, environment examples, deployment guide: deploy
  from task_labeler; serverless Flask entry point; static files in public; time budget above
  the provider timeout. Preview and production use separate Supabase projects and secrets.

## Acceptance criteria and tests

1. Start with existing SQLite regression suite; add failing isolation configuration tests.
2. Reject preview→production URL swaps and copied database IDs; tests must verify no writes.
3. Exercise real Postgres schema/constraints, import/provenance, both complete conditions,
   email recovery, frozen configuration, idempotent saves, and concurrent AI reservations.
4. Verify runtime role privileges, baseline AI rejection, immutable data, failed and late
   responses, consistent exports, and preview/demo exclusions.
5. Validate Vercel/static build and documented commands, then rerun final regression,
   formatting, static analysis, and syntax checks. Clearly mark unavailable hosted checks.

## Risks and recovery

- Postgres adds database network latency and a driver dependency. Keep connections short
  lived and transactions brief; no ORM or new service layer. Classroom load remains the
  intended scale; do not claim unmeasured latency or throughput.
- Preview/production separation uses distinct projects, environment-scoped secrets, and
  independent database markers. A preview build must never receive production credentials.
- A fresh real-study Postgres database is acceptable: the current real SQLite database
  has no participant responses. Leave local demo progress untouched; it is not migrated.
- Back up before future schema changes. Roll back app releases only to a schema-compatible
  version; recover database backups into a new project and verify before switching URLs.
- Psycopg 3.3.6 was approved and added, matching data_labeler. No remote resources are created automatically.
