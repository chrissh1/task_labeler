# Deploy with Vercel and Supabase

The implementation is prepared for Flask on Vercel with Supabase Postgres. No remote
project has been created or deployed automatically. The study remains **four scored
questions per condition**. Local SQLite demo support is retained.

Use **SQLite locally and one Supabase project for production**. No cloud preview
project is needed. Local test data stays in local SQLite files; it is not migrated
or uploaded. Import the real Assignment 2 dataset into the production project.

Vercel preview URLs remain blocked without separate preview configuration. They never
fall back to production credentials or attempt to persist SQLite inside Vercel. Do
manual testing locally and deploy the production branch when ready. Production form
submissions count as real study records; avoid submitting test answers there.

## 1. Install and verify locally

From `task_labeler`, with Python 3.12:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python scripts/check_postgres.py
```

The Postgres runner requires `initdb`, `pg_ctl`, `createdb`, `dropdb`, `psql`, `pg_dump`,
and `pg_restore` on PATH. It creates a disposable server with a private Unix socket,
synthetic identities, and fake AI, and cleans up on success or failure. It never uses
Supabase credentials. Routine tests also clear deployment variables so sourcing a
production `.env` cannot redirect tests into that database.

Psycopg is pinned to **3.3.6**, matching `../data_labeler/requirements.txt` and its
connection implementation: TLS by default, a 10-second connection timeout, and
`prepare_threshold=None` for Supabase transaction-pooler compatibility. The app uses
short-lived request connections, transaction-local search paths/timeouts, and a
transaction advisory lock to retain SQLite's single-writer semantics across serverless
instances. Provider requests occur outside write transactions. This prioritizes the
existing classroom concurrency/correctness contract; no throughput gains are claimed.

## 2. Create and provision one production Supabase project

Generate its database identity:

```sh
python -c 'import uuid; print(uuid.uuid4())'
```

Use Supabase's Connect dialog for the project owner's **direct or session-pooler**
connection details. For owner provisioning with psql, set `PGHOST`, `PGPORT`, `PGUSER`,
`PGDATABASE=postgres`, and `PGSSLMODE=require` to those details. Keep passwords out of
shell arguments and source files; `-W` prompts securely.

For production (replace UUID below with your generated value):

```sh
psql -W -v deployment_environment=production \
  -v database_id=YOUR_PRODUCTION_UUID -f scripts/provision_postgres.sql
psql -W
```

In the second interactive psql session, run `\password study_runtime` and set a fresh
runtime password. Exit with `\q`.

Provisioning creates a private `study` schema, an immutable environment/UUID/version
marker, tables, constraints, immutable question/dataset/session-assignment triggers,
and a restricted `study_runtime` login. It is transactional and refuses an existing
schema/role. Do not rerun it to update an existing study or delete tables to fix a
version mismatch. No migrations run during app startup or Vercel builds.

The runtime can read the dataset and write participant/session/trial/AI records. It
cannot import/alter answer keys, change the deployment marker, delete records, create
schema objects, or act as the project owner. The schema is not exposed through Supabase's
Data API; keep `study` out of the API's exposed schema list. No anonymous/authenticated
API-role grants or Supabase Auth integration are required.

## 3. Fill local owner configuration and import the dataset

Your git-ignored `.env` has blank fields for the following. Quote URL values in a shell
file, and percent-encode reserved characters in the URL password.

| Variable | Use |
| --- | --- |
| `PRODUCTION_DATABASE_URL` | Production **runtime-role transaction-pooler** URL |
| `PRODUCTION_DATABASE_ID` | UUID used to provision production |
| `PRODUCTION_ADMIN_DATABASE_URL` | Production owner direct/session-pooler URL, local maintenance only |

In Supabase's Connect dialog, select **Direct / Connection string**, then choose
**Transaction pooler** for runtime URLs. The tab name “Direct” is not an instruction to
choose a non-pooled connection. The **Server** tab's `@supabase/server`, `SUPABASE_URL`,
and API-key setup is for a different integration; this Python app uses Psycopg and a
Postgres URL. Connecting alone does not create the app's tables: complete the one-time
provisioning and dataset import above/below first.

For a custom runtime role, the Supabase shared-pooler username is of the form
`study_runtime.PROJECT_REFERENCE`; use the corresponding pooler host/port from Connect.
Use the role password set above, not the project-owner password. Append
`?sslmode=require` (or `&sslmode=require` if the URL already has query parameters).
Verify the actual project connection details rather than copying example hostnames.

Keep `APP_ENV=local` for ordinary local SQLite development. The owner CLI selects its
target explicitly and ignores Vercel environment selection. Load the file and import:

```sh
set -a
source .env
set +a

python -m study --environment production --admin import questions.csv \
  --version assignment2-final-v1 --results ../task_model_eval/final_run/results.csv

# Verify the restricted runtime connections, marker, and dataset:
python -m study --environment production check
```

Expect 200 questions and zero participants in the production project. The importer validates
and preserves exact source/result bytes and hashes; no MMLU resampling or model calls.
Production rejects demo imports and demo app mode. Imports cannot replace an existing
dataset. The CLI never prints database URLs or provider credentials.

For local tests with real questions and live AI, keep `APP_ENV=local`, set
`STUDY_MODE=study` and `STUDY_DATABASE=instance/local-pilot.sqlite3`, and import the
real CSV into that local file using `--database instance/local-pilot.sqlite3`. This
requires a Gemini key but no Supabase connection. Keep pilot exports separate from
actual collection exports; only the production Supabase database is collection data.
For ordinary UI tests, keep the existing SQLite demo and fake provider.

## 4. Connect GitHub to Vercel and deploy production

Import the GitHub repository in Vercel. Set **Root Directory = task_labeler** if the
repository contains the parent directory; use the repository root if it contains only
this app. Accept automatic Flask detection and leave the Build Command override unset.
There is no `vercel.json` or custom asset-build script. If you previously configured
`python scripts/prepare_static.py` in Vercel's dashboard, clear that override.

Vercel recognizes `wsgi.py` as the Flask entry point. `.python-version` selects Python
3.12 and `requirements.txt` declares dependencies. It does not use the README's Gunicorn
command. Current Fluid compute defaults provide a 300-second maximum duration, above
the application's 60-second provider timeout; no duration override is needed. If an
existing project has a shorter dashboard override, remove it before testing live AI.

The CSS and JavaScript are committed directly in `public/static`, Vercel's standard
public-asset directory. Local Flask serves those same files at `/static`. There is no
copy/build step. `.vercelignore` excludes local databases, secrets, virtualenvs, tests,
exports, and backups. Nothing containing answer keys is placed in `public`.

Configure Vercel variables using **environment-specific scopes**:

| Vercel scope | Variables available in that scope only |
| --- | --- |
| Production | `PRODUCTION_DATABASE_URL`, `PRODUCTION_DATABASE_ID`, a production `SECRET_KEY`, `GEMINI_API_KEY` |

In Production, also set `STUDY_MODE=study`, `TRIALS_PER_CONDITION=4`, `MAX_FOLLOWUPS=3`,
and `COOKIE_SECURE=1`. Retain secret keys across subsequent deployments. Do not upload
`.env`, configure owner `*_ADMIN_DATABASE_URL` values in Vercel, or scope production
credentials to Preview/Development. `.env.vercel.example` is a reference only.

Vercel supplies `VERCEL_ENV`; do not set it manually. Do not set `APP_ENV` on Vercel.
A mismatching `APP_ENV` is rejected. Custom Vercel environments other than preview and
production are deliberately unsupported and fail closed rather than choosing a database.
The application never falls back to `PRODUCTION_DATABASE_URL`, generic `DATABASE_URL`,
or SQLite when preview configuration is missing.

Each connection checks the private database marker's environment, UUID, and supported
schema version before accessing study data. Accidentally placing a production URL into
`PREVIEW_DATABASE_URL` fails even if its UUID was also copied. Preview and production
have different signed-cookie names and should have different signing secrets.

Leave Preview database variables unset for this local-testing workflow. A preview
build may complete, but its application will refuse to start; this is intentional.
Only the production deployment should have working Supabase credentials. If you later
want usable cloud preview URLs, add a separate preview project and scoped credentials;
never point preview variables at the production database.

Before launching production, verify that the chosen participant URLs are publicly
accessible without Vercel-account sign-in. Keep owner/admin access private; there is
no public export route. Use the same deployment for `/`, `/study?condition=baseline`,
and `/study?condition=ai`.

## 5. Owner exports and backups

Run locally with the appropriate private environment variables loaded:

```sh
python -m study --environment production export exports/collection-01
python -m study --environment production analyze > exports/analysis.json
python -m study --environment production --admin backup backups/production.dump
```

Exports use one repeatable-read, read-only Postgres snapshot. The manifest and CSVs
contain dataset/configuration provenance and no email mapping. Free text can still
contain identifying details; keep exports private.

Postgres backups use `pg_dump` custom format for the `study` schema, without owner/ACL
restoration. Install a pg_dump version compatible with the server. Passwords are passed
in the child process environment, not command arguments or output. Failed backups are
removed; a preexisting destination is never overwritten. Store backups privately off
Supabase as appropriate, and verify your Supabase plan's managed backup/retention policy.

For recovery, restore into a **new** project with `pg_restore --no-owner --no-acl`,
then create the restricted `study_runtime` role and run `scripts/grant_runtime.sql` as
the schema owner. Set a new runtime password. The restored marker retains its original
UUID/environment. Check identities, counts, both flows, and exports before deliberately
switching the production URL. Do not run the initial provisioning script over a restore.
The automated local test restores a custom backup into a separate database and verifies
saved trials and the deployment marker. Do not infer that a backup is good merely from
its existence.

No automatic schema upgrades are included. Before future schema changes, back up and
test against a disposable local Postgres database. Roll back only to a schema-compatible app release; otherwise recover
into a new project and validate before switching credentials.

## Release checklist and verification limits

Completed locally: SQLite regressions; real Postgres provisioning/import; restricted-role
permissions; full preview and production flows; email recovery and idempotent saves;
preview exclusion; wrong-environment refusal before writes; concurrent AI deduplication
and submission; repeatable-read export snapshots; transaction rollback; custom backup
and restore; static-asset, Python lint/format/compile, and JavaScript syntax checks.
Tests use fake AI and synthetic participants on disposable databases. No real responses
or remote projects were created. Verification reused installed Flask/Psycopg packages
from `data_labeler` because registry DNS blocked a clean dependency installation.

Still required before participant collection:

- Install the full declared dependencies in a clean environment/build.
- Provision the production project, set Production-scoped secrets, and verify Supabase pooler/TLS access.
- Check local forms and verify deployed static assets, secure cookies, and function configuration without submitting test responses to production.
- Perform browser keyboard/mobile and resume checks against the local test application.
- Run a credentialed Gemini pilot; inspect exact displayed-output logs and late responses.
- Time both conditions together before claiming the ten-minute target.
- Confirm local testing leaves production participant/session counts unchanged and production starts clean.
- Verify backup/restore and exports against the hosted project, then collect at least five valid
  completed participants per condition. No significance or outcome results are fabricated.

References: [Vercel Flask](https://vercel.com/docs/frameworks/backend/flask),
[Vercel Python configuration](https://vercel.com/kb/guide/ship-a-flask-app-on-vercel),
[Supabase connections](https://supabase.com/docs/guides/database/connecting-to-postgres),
[Psycopg prepared statements](https://www.psycopg.org/psycopg3/docs/advanced/prepare.html).
