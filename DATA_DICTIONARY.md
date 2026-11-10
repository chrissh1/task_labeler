# Data dictionary and measurement definitions

All server timestamps are UTC ISO-8601 strings with milliseconds. IDs are opaque UUIDs
except source question IDs and integer attention-event IDs. Answer indices are **0–3**
(A–D), confidence **1–5**. Empty CSV cells mean missing/not yet recorded, never zero.
Booleans are 0/1. JSON fields remain JSON text in CSV. Dataset/configuration are immutable.

| Table / export | Fields and meaning |
| --- | --- |
| `datasets` (database only) | `id`, `version`, `sha256` of exact source bytes, `source_name`, `source_bytes` (original CSV/JSON), `results_bytes`, `results_sha256`, `demo`, `imported_at`. Preserves evaluated artifacts without serving them. |
| `questions` (database only) | Stable `id`, `dataset_id`, `subject`, exact `question`, ordered JSON `choices`, `correct_index`, `source`, `split`, `original_answer` (nullable), `original_output`, `original_model`, `content_hash` of normalized question text. |
| `study_config` | Singleton JSON `config`, frozen on first allocation. Contains version, trial count, model, generation settings, prompt version, and follow-up/retry limits. |
| `participants` | UUID `id`, unique normalized `email`, `created_at`. Export renames ID to `participant_id` and omits email. This mapping is identification, not verified authentication. |
| `sessions.csv` | `id`, `participant_id`, locked `condition` (`baseline`/`ai`), `dataset_id`, `config`, `demo`, `created_at`, `started_at`, `completed_at`, `condition_order` (1/2, nullable until started), `stage`, `attention_failures` (current cycle), optional `comment`. Export adds dataset version/hash, participant creation time, `completed`, `valid`. |
| `trials.csv` | Only scored assignments: `id`, `session_id`, `question_id`, `position` (1-based), `practice` (always 0 here), `first_visible_at`, `selected_index`, `confidence`, `submitted_at`, server-scored `correct`, `elapsed_ms`, `visible_ms`, `timing_approximate`. Joins participant, subject, condition/order, dataset version/hash, correct/original answers/model, `demo`, `session_completed`, `session_valid`. Unanswered assignments remain with missing outcomes. |
| Derived trial AI fields | `initial_ai_answer`, `initial_ai_correct`, `agreement_initial_ai`: first successful initial suggestion, not follow-up revisions. `suggestion_exposed` requires acknowledged suggestion before submission. `first_suggestion_displayed_at`, `before_suggestion_ms`, `ai_failed_attempts`, `ai_pending_attempts`, `followup_turns` (unique slots), `ai_statuses` (all attempts). |
| `ai_interactions.csv` | `id` is persisted request ID; `trial_id` joins trials, including database-only practice trials. Also joins `question_id`, `participant_id`, `condition`, `practice`, `demo`. `slot` 0=suggestion, 1+ follow-up; `attempt` 1/2; `kind`, `status`, exact `model`, `prompt_version`, JSON `settings`, exact `context`, exact `user_text`, `started_at`, `finished_at`, `latency_ms`, `raw_response`, JSON `provider_metadata` (model version/token usage if available), parsed `answer_index`, `displayed_text`, sanitized `error` type, `acknowledged_at`. No API keys. |
| `attention_checks.csv` | Integer `id`, `session_id`, `selected_index`, `passed`, `restart`, `created_at`; joins participant/condition/demo. All attempts retained. |
| `events` (database only) | Unique client event `id`, `trial_id`, `page_id`, `kind` (`visible`, `hidden`, `checkpoint`, `answer_change`, `confidence_change`, `leave`), bounded cumulative `visible_ms`, server `created_at`, JSON `payload` holding only changed option value when applicable. No keystroke or mouse tracking. |
| `checkpoints` (database only) | Latest cumulative `visible_ms` per trial/page ID and `updated_at`. Refresh creates a new page ID while keeping old checkpoints. |

`stage`: instructions → attention → trials (practice first) → comment → complete.
Two attention failures return to instructions. `completed` means the participant
pressed Finish; `valid` additionally requires non-demo, passed attention, submitted
practice, and all configured scored responses with confidence. Attention failure
counts do not exclude a completed session. See the pre-specified policy in README.

AI `status`: pending, ok, error (provider or validation failure), stale (90-second
interrupted-request threshold), late_ok/late_error (provider eventually returned after
a stale notice). The last two preserve the already-issued notice in `displayed_text`
and the eventual raw output separately. `displayed_text` is the exact text prepared
for delivery, **not** proof of exposure. Only `acknowledged_at` indicates a browser
render receipt, deduplicated and accepted only while the question is active.
No receipt is treated conservatively as unknown/not observed, not proof of no viewing.
The browser does not display new late responses after successful submission.

## Timing and confidence

- **Total elapsed trial time**: server `submitted_at − first_visible_at` in milliseconds.
  First visibility is the first accepted browser event, preserved across refreshes.
  Includes reading, confidence interaction, provider waiting, and away time between visits.
- **Page-visible time**: browser `performance.now()` intervals while the page is visible,
  checkpointed every 10 seconds, on visibility changes, answer/confidence changes,
  and before submission. Server bounds growth by wall-clock interval plus a 1-second
  tolerance. Stored totals are capped by total elapsed time on submission.
- **Approximation**: `timing_approximate=1` for every trial. Browser measurements,
  network delivery, sudden closes, lost final checkpoints, and multiple tabs cannot
  establish exact attention time. Multiple tabs can overlap; the elapsed cap limits
  but does not fully correct overlap. Last uncheckpointed time may be lost. There is
  no claim that visible time is active thinking time.
- **AI latency**: monotonic server duration from provider call through response parsing,
  separately stored per attempt. It is not subtracted from elapsed time: a participant
  may read or decide while waiting.
- **Before first suggestion**: server interval from first visibility to first successful
  suggestion's render receipt; missing if no acknowledged suggestion. This includes
  network/display acknowledgment delay, not an exact eye-tracking measurement.
- **Confidence**: final required response to “How confident are you that your selected
  answer is correct?” Values 1 Not at all confident, 2 Slightly confident, 3 Moderately
  confident, 4 Confident, 5 Very confident. This ordinal measure is summarized with
  the requested participant mean; no model confidence or calibration claim is made.

## Analysis denominators

Primary metrics use valid completed **participant × condition** sessions, regardless
of AI exposure/failure. Practice, attention, demo, and partial sessions never contribute.
Partial/assigned counts are reported separately. Subject summaries first compute
participant × condition × subject accuracy, then average across participants.

Live AI accuracy and agreement are descriptive across available initial successful
suggestions on valid AI scored trials; missing/failed suggestions are reported rather
than imputed. Original benchmark accuracy includes saved invalid predictions as wrong,
but not absent original outputs. Sampled original accuracy uses distinct question IDs
across valid sessions, separately from the full evaluated original benchmark. These
AI descriptives are not tests of human participant effects.

CSV cells beginning with formula-like characters (including after whitespace), or
leading tab/newline/carriage return, receive an apostrophe. Do not treat that apostrophe
as original participant/model text; the database preserves exact values. Keep exports
private even though the default export excludes emails.

## Supabase deployment metadata and preview exclusion

The Postgres `study.deployment_metadata` singleton stores `environment` (local,
preview, production), a generated `database_id` UUID string, and `schema_version=1`.
The runtime must match all three before accessing study data. The runtime role cannot
change or delete this marker. It is an operational identity, not a participant ID.

On a preview deployment, every session and its joined exports have `demo=1` even when
using the real question dataset and live provider. This field therefore means
**excluded test/demo session**, not necessarily simulated AI. The visible preview
banner identifies these responses as testing. Default exports reject preview databases;
`--include-demo` explicitly enables a marked test export. Primary analysis still excludes
those sessions. Production requires study mode and a production-marked database.

Postgres holds the same record fields as SQLite in a private `study` schema. Question
and original artifact bytes are `bytea`; durations are double precision. Owner exports
read a repeatable-read, read-only snapshot, so all exported tables describe one state.
