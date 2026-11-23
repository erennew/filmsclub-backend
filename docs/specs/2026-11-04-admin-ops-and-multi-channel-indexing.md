# Admin ops expansion + multi-channel indexing

Status: approved design, pre-implementation plan
Date: 2026-11-04
Owner: FilmsClub backend (`filmsclub-backend/Backend`) + React admin (`frontend-main/src/admin`)

## 1. Scope

Nine capabilities, delivered as four independently deployable chunks:

1. Runtime queue/index tuning without a restart
2. Admin audit log
3. Stuck-claim visibility and force-release
4. Multi-channel concurrent indexing (bot + panel)
5. Audio-language curation view
6. Bulk metadata remediation
7. Orphan file report
8. Telegram alert push
9. Indexing control page in the web panel

Each chunk ships behind one documented switch whose default reproduces today's
behaviour exactly, and both default and enabled states are verified before merge.

## 2. Ground truth verified before designing

These facts were read out of the code, not assumed. Any implementation that
contradicts them is wrong.

**Single process, single event loop.** `heroku.yml` declares only a `web` docker
build; `Dockerfile` CMD is `bash start.sh`; `start.sh` is `exec python3 -m Backend`.
`Backend/__main__.py:73` starts uvicorn via `loop.create_task(server.serve())` and
then starts StreamBot, UserBot and `start_background_tasks()` in the same loop
(`__main__.py:77-93`). There is no worker dyno, so there is no cross-process
coordination problem. Panel handlers may call bot and queue functions directly.
Consequence: this app must not be scaled above one web dyno while the bot runs,
because a second dyno would start a second bot on the same session.

**Ops collections live on one relocatable cluster.** `Database._setup_ops_collections`
(`helper/database.py:824-842`) points every ops collection at a single `_Cluster`
and records `self.ops_cluster_index = cluster.index - 1` (`database.py:827`).
`_Cluster.index` is 1-based; collection pointers are the access path, never an
index. Ops relocation on quota pressure is handled by `_choose_ops_cluster`
(`database.py:857`), `_copy_ops_state` (`database.py:915-957`),
`ensure_ops_cluster_healthy` (`database.py:983`) and `ops_failover_watchdog`
(`database.py:1019`, started at `start.py:1156`).

**`app_settings` already exists** (`database.py:834`) and already participates in
failover copy (`database.py:949`). It holds exactly one document,
`_id: "queue_state"` (`set_queue_paused` `database.py:2534`, `is_queue_paused`
`database.py:2548`). Runtime config is additional documents in this collection.

**`deploy_config` is dead.** Declared at `database.py:828` and failover-copied at
`database.py:952`, but no code reads or writes it. This design neither uses nor
removes it.

**Queue worker count is startup-only.** `QueueConfig.QUEUE_WORKERS` is read once in
`start_background_tasks` (`start.py:1136`, `start.py:1145`). Live worker scaling
therefore needs a supervisor; it is not achieved by making the value mutable.

**Per-iteration config reads are genuinely live.** Verified read sites:
`FILE_QUEUE_DELAY` at `start.py:774` and `start.py:932`; `BATCH_SIZE` /
`BATCH_GAP_SECONDS` at `start.py:779-781`; `MAX_QUALITIES_PER_TITLE` at
`database.py:1323`; `MAX_QUEUE_BACKLOG` at `channel_index.py:459`;
`STORAGE_STOP_PERCENT` at `channel_index.py:487`; `SCAN_BATCH_SIZE` /
`SCAN_BATCH_DELAY` at `channel_index.py:281-282` and `channel_index.py:657`.

**`USE_CAPTION` / `USE_TMDB` are read per message but mutated in memory only.**
Read at `start.py:1202` and `start.py:1431`; the `/caption` and `/tmdb` commands
flip the class attribute directly (`start.py:2770`, `start.py:2785`) with no
persistence, so a toggle is lost on restart. This design makes the settings store
the single writer for both, and rewires the two Telegram commands to call it.

**Only one index scan can run today.** Module globals `_active_task`,
`_active_channel_id`, `_resume_task`, `_stop_event`, `_start_lock` at
`helper/channel_index.py:36-41`. `request_index_scan` raises `IndexBusyError` when
busy (`channel_index.py:134`), `_launch` re-checks under the lock
(`channel_index.py:221-224`), and resume serializes by waiting for each scan to
finish (`channel_index.py:189-207`). `_run_scan` already keeps a local `stop_event`
and only publishes it to the global (`channel_index.py:516-518`).

**Per-channel progress already works.** `status_message_id` is stored per scan doc
(`channel_index.py:153`, read at `channel_index.py:526`), so N concurrent scans
need no progress plumbing.

**Distinct audio languages are not computable today.** Files live in the
`telegram[]` array (`helper/modal.py:60-76` movie, `modal.py:38-56` series, with
episodes nested as `seasons[].episodes[].telegram[]`). `QualityDetail.audio_tracks[]`
and `subtitle_tracks[]` are `Optional` (`modal.py:5-15`) and `None` for any file
never probed. Probe output is `{index, language, codec, channels, title}` with
`language` as a display name from `get_language_name`
(`helper/media_probe.py:260-269`, `media_probe.py:64-69`). Persisted at
`database.py:1487` (movie) and `database.py:1528` (episode), sourced from
`start.py:1423`, backfilled lazily by the player via `helper/audio_tracks.py:161-226`
into `database.py:2129` / `database.py:2146`. Media-level `languages` comes from
filename/TMDB and only falls back to audio tracks when already empty
(`start.py:1417-1419`), so it is not the audio set. `_cap_qualities` silently drops
file entries above the cap (`database.py:1317-1332`). No cross-file language
computation exists anywhere in the repo.

**Media documents carry no raw Telegram source identity.** `QualityDetail.id` is a
base62 blob of `{chat_id, msg_id, hash}` (`database.py:1460-1461`, codec in
`helper/encrypt.py:56-59`, decoded at `start.py:423`). Raw `channel`/`msg_id`/hash
exist only on ops docs: `file_queue` (`database.py:2335-2354`), `processed_files`
with `channel_id, message_id, processed_at` (`database.py:2463-2466`),
`queue_checkpoints` (`database.py:2561-2575`), `index_scans` (`database.py:1091-1093`).
`file_unique_id` is the dedupe key (`modal.py:11`, `is_file_unique_id_known`
`database.py:2290`).

**Missing-metadata detection already exists as filter flags.** `ADMIN_LIST_FLAGS`
(`database.py:135-146`) accepts `no_files, has_files, no_subs, has_subs, no_poster,
no_backdrop, no_description, no_genres, no_cast, no_episodes`, computed by
`_admin_count_stages` (`database.py:97-132`), exposed through
`GET /admin/api/media?flag=` (`fastapi/main.py:631-659`). There is no
"metadata not fetched" sentinel on media documents.

**Existing auth surface.** `verify_admin_auth` (`fastapi/auth.py:183-194`) accepts a
signed JWT cookie or `X-Admin-Session` header, plus a legacy username/password
header pair. Sessions are single-credential with `role: "admin"` hardcoded
(`auth.py:145-154`). Middleware provides rate limiting, an optional admin IP
allowlist, CSRF origin check, body-size limits and an edge shared-secret lock
(`fastapi/security_middleware.py:17-23`, `129-158`).

## 3. Non-goals

- Multi-user admin accounts, roles or a viewer permission. Sessions remain one
  shared credential. Audit entries record the acting credential's username and
  the Telegram user id, but no per-user login exists.
- Re-sharding media documents or changing which cluster a title lives on.
- Any change to streaming playback, the public homepage, or the CDN path.
- Removing `deploy_config`.
- Automatic wipe-and-recrawl tooling; existing commands stay manual and
  confirmation-gated.

## 4. Ops-collection registration checklist

Applies to every new collection in this spec. A collection that misses step 2 is
silently lost when the ops cluster relocates.

1. Assign the pointer in `_setup_ops_collections` (`database.py:824-842`).
2. Add a batched copy step in `_copy_ops_state` (`database.py:915-957`), keyed on
   `_id`, using `_OPS_COPY_BATCH` (`database.py:822`).
3. Create indexes in `_init_indexes` (`database.py:1066-1175`) inside the existing
   per-index try/except that logs a warning on failure. TTL indexes use
   `expireAfterSeconds` inside `IndexModel`, matching `ttl_failed_files`
   (`database.py:1120`) and `ttl_queue_expire_at` (`database.py:1132`).
4. Add a count line to the `ops_cols` map in `get_storage_report`
   (`database.py:2735-2748`) so the new collection appears on the Health page.

New collections introduced here: `admin_audit`, `admin_jobs`, `orphan_files`.
`app_settings` and `index_scans` are extended, not added.

## 5. Chunk 1 — runtime config, audit log, stuck claims

### 5.1 Runtime config

New module `Backend/helper/runtime_config.py`:

- Document `app_settings._id = "runtime_config"`:
  `{_id, values: {<key>: <scalar>}, updated_by, updated_at}`.
  Only whitelisted keys are stored; an unknown key is rejected, not written.
- `KNOBS`: a table of key → type, min, max, default source, live semantics.
  Values are validated against the range before write.

| key | type | range | today's default | live semantics |
|---|---|---|---|---|
| `file_queue_delay` | int s | 0-60 | `QueueConfig.FILE_QUEUE_DELAY` | live, next file |
| `batch_size` | int | 5-1000 | `QueueConfig.BATCH_SIZE` | live |
| `batch_gap_seconds` | int s | 0-600 | `QueueConfig.BATCH_GAP_SECONDS` | live |
| `queue_workers` | int | 1-16 | `QueueConfig.QUEUE_WORKERS` | via supervisor, ≤1 poll |
| `max_qualities_per_title` | int | 1-12 | `QueueConfig.MAX_QUALITIES_PER_TITLE` | live, next insert |
| `scan_batch_size` | int | 20-500 | `IndexConfig.SCAN_BATCH_SIZE` | live, next batch |
| `scan_batch_delay` | float s | 0-60 | `IndexConfig.SCAN_BATCH_DELAY` | live |
| `max_queue_backlog` | int | 100-50000 | `IndexConfig.MAX_QUEUE_BACKLOG` | live, next poll |
| `storage_stop_percent` | int | 50-99 | `IndexConfig.STORAGE_STOP_PERCENT` | live, next check |
| `index_max_concurrent_scans` | int | 1-8 | new, default `1` | live, next launch |
| `use_caption` | bool | — | `Telegram.USE_CAPTION` | live, next message |
| `use_tmdb` | bool | — | `Telegram.USE_TMDB` | live, next message |

- Effective value = stored override if present, else the existing env-derived
  class attribute. With no override document written, every value is today's, so
  the default state is byte-identical behaviour.
- Cache: a single in-process snapshot refreshed by a 5-second background poll, not
  a Mongo read per iteration. Workers read the cache. `runtime_config.get(key)`
  is synchronous and returns the cached effective value.
- Failure policy: if the read fails, keep the previous snapshot and log. If there
  is no previous snapshot, return the env default. Config storage must never
  stall a worker.

Read-site changes: replace the direct attribute uses enumerated in §2 with
`runtime_config.get(...)`. Keep `QueueConfig`/`IndexConfig` as the default source
so nothing else that reads them changes.

`USE_CAPTION` / `USE_TMDB` single-writer rule: `/caption` (`start.py:2766-2779`)
and `/tmdb` (`start.py:2781-2794`) stop mutating `Telegram.*` in place and instead
call `runtime_config.set_bool(...)`, which persists and applies. `/status`
(`start.py:2091-2092`) reads the effective value. This closes the existing
toggle-lost-on-restart gap without adding a new switch.

Worker supervisor: `start_background_tasks` (`start.py:1129-1147`) keeps its
startup behaviour, then hands the MongoDB engine to a supervisor task that polls
`queue_workers` and spawns or retires `process_mongodb_queue(worker_id)` tasks to
match. Rules:

- Worker ids are allocated monotonically and never reused while their task lives.
- Scale-down retires the highest-numbered workers first. `worker_id == 1` is never
  retired, because it owns the `reset_failed_messages` housekeeping branch
  (`start.py:919-921`).
- Retirement is cooperative, not a cancel: a marked worker finishes its current
  item, skips the next `claim_next_item` call, and exits. Nothing is ever cancelled
  mid-claim, so scaling down cannot create an orphaned `processing` item.
- The in-memory engine branch (`ENABLE_MONGODB_QUEUE=false`) is not supervised; the
  page shows `queue_workers` as restart-only in that configuration.
- The supervisor reports `{configured, desired, running: [id], retiring: [id]}` so
  the Queue page can show what is actually live versus what was asked for.

### 5.2 Audit log

New collection `admin_audit` on the ops cluster, one document per admin mutation:

```
{_id, ts, actor, actor_kind, action, target, detail, result, ip}
```

`actor_kind` is `web` or `telegram`; `actor` is the admin username or the Telegram
user id; `target` is one of `media:<media_type>:<tmdb_id>`,
`media_file:<tmdb_id>:<file_id>`, `subtitle:<tmdb_id>:<subtitle_id>`,
`episode:<tmdb_id>:<season>:<episode>`, `queue:<op>`, `cache:<target>`,
`index:<channel_id>`, `job:<kind>`, `db:<op>`; `detail` is a bounded summary
(≤1000 chars) that must not contain secrets or full document dumps; `result` is
`ok` or `error:<text>`.

Writer `Backend/helper/audit.py`: `audit_event(...)` creates a task and swallows
every exception with a warning log. A failed audit write never changes the outcome
of the action it records. Indexes: `ts` descending, TTL 90 days, plus `actor` and
`action`.

Instrumented actions — every admin route that mutates (`main.py:662`, `717`, `768`,
`832`, `878`, `940`, `1011`, `1069`, `1129`, `1206`, `1257`, `1298`, `1355`, `1406`,
`1424`, `1456`, `1467`, `1648`, `1814`, `1828`, `1842`, `1874`, `2028`, `2085`,
`2103`, `2116`, `2157`, `2751`) and the destructive Telegram commands `/resetdb`
+ `/confirmreset` (`start.py:2708`, `start.py:2733`), `/clearqueue` +
`/confirmclear` (`start.py:2176`, `start.py:2204`), `/clearmongodb` +
`/confirmmongodb` (`start.py:2262`, `start.py:2287`), `/delete` + `/confirmdelete`
(`start.py:2842`, `start.py:2947`), `/mergeduplicates` (`start.py:2023`),
`/rescan` (`start.py:2337`), index start/stop callbacks (`start.py:2604`,
`start.py:2677`).

Read API `GET /admin/api/audit?actor=&action=&from=&to=&page=&page_size=` returning
newest first. Panel page "Audit" lists entries with actor/action/time filters.
No delete endpoint; retention is TTL only.

### 5.3 Stuck claims

- `GET /admin/api/queue` (`main.py:1803`) gains a `claims` array: for each item in
  status `processing`, `{id, channel, msg_id, title, claimed_by, claimed_at,
  age_seconds}`. Status names come from `_LIVE_QUEUE_STATUSES` (`database.py:821`).
- `POST /admin/api/queue/claims/release` with either `{ids: [...]}` or
  `{older_than_seconds: n}` returns items to `pending`, clears `claimed_by`,
  increments a `claim_releases` counter on the doc, and audits. The existing
  `QUEUE_CLAIM_TIMEOUT` recovery (`config.py:196`, 7200 s) stays as the automatic
  path; this is the manual "do not wait two hours" button.
- Queue page shows claim age and one-click release.

Verification for chunk 1: unit tests for knob validation, effective-value
resolution with and without an override, and audit writer exception-swallowing.
Integration on a local API-only server: save an override, observe a worker log
line change pacing within one poll interval; kill a worker mid-item and confirm
release; restart the server and confirm overrides survive while an unsaved value
still comes from env. Both states of `queue_workers` (default, and scaled 2→4)
proven on a real local run.

## 6. Chunk 2 — multi-channel concurrent indexing

### 6.1 Registry replacing the globals

`_ScanHandle` holds `{channel_id, task, stop_event, requester_id, status_message_id}`.
Module state becomes `_scans: Dict[int, _ScanHandle]` plus the existing
`_start_lock` and `_resume_task`. `is_scan_running()` becomes
`running_count()`; a per-channel `is_scan_running(channel_id)` answers the single
case. `_run_scan` takes its handle's `stop_event` and no longer assigns a global.
`_on_task_done` removes only its own entry.

`IndexBusyError` is raised only when `running_count() >= cap`, where
`cap = runtime_config.get("index_max_concurrent_scans")`. Default `1` reproduces
today's single-scan behaviour exactly, including the current error text path at
`start.py:2533-2535`.

### 6.2 Shared read governor

All scans read channel history through one user session, so N scans multiply
`get_messages` pressure and Telegram FloodWait would stall the whole bot, not just
indexing. A module-level token bucket in `channel_index.py` caps aggregate batch
throughput:

- Tokens represent messages read. Refill rate is
  `scan_batch_size / scan_batch_delay` messages per second — with defaults
  `200 / 2.0` that is 100 msg/s — and burst capacity is `scan_batch_size` (200).
  Both are recomputed from effective runtime-config values on each refill, so the
  governor follows the knobs.
- Consequence: one scan sees exactly today's envelope, so `cap=1` is unchanged
  behaviour; N scans share the same aggregate 100 msg/s instead of N times it.
- `_fetch_batch` (`channel_index.py:361`) acquires tokens for the batch it is about
  to request and waits for the bucket rather than overshooting it.
- On `FloodWait`, the scan reports `penalty(seconds)` and the bucket blocks every
  scan until it clears, so one channel's limit backs off all channels.
- The governor is independent from the queue FloodWait gate (`start.py:119`);
  neither reads the other.

### 6.3 Statuses and resume

`index_scans.status` gains `waiting`, meaning confirmed but slot-limited. Resume
(`channel_index.py:179-210`) launches up to `cap` concurrently instead of the
serial loop, leaving the remainder `waiting`. When a scan finishes, `_on_task_done`
picks the oldest `waiting` scan and launches it. A scan whose channel is no longer
in `AUTH_CHANNEL` is marked `error` with that reason rather than retried
(`is_auth_channel`, `channel_index.py:65`).

### 6.4 Bot commands

- Link-in-DM trigger (`start.py:2527`) accepts multiple links, one per line,
  resolving and de-duplicating them, then one confirmation listing every channel
  with the concurrency cap and which will start now versus `waiting`. The existing
  per-link guardrails stay: `AUTH_CHANNEL` membership, `USER_SESSION_STRING`
  presence (`start.py:2565`), access probe (`start.py:2571-2581`).
- `/indexstatus` renders running, waiting and recently finished scans with cursor,
  checked/queued/existing/errors and elapsed.
- `/indexstop` without arguments stops all running scans, reporting each channel;
  `/indexstop <channel-id>` stops one. Today it takes no argument
  (`start.py:2688`). Confirmation-gated destructive commands elsewhere keep their
  existing two-step pattern; stopping a scan does not need one because the scan is
  resumable.
- All three audited per §5.2.

### 6.5 Panel page

New "Indexing" page: table of scans, start form accepting pasted links, stop
one/all, concurrency knob bound to `index_max_concurrent_scans`, and the same
`/indexstatus` data. New routes:
`GET /admin/api/index-scans`, `POST /admin/api/index-scans/start`,
`POST /admin/api/index-scans/stop`. The start route calls the same
`request_index_scan` path the bot uses, so panel and bot cannot diverge.

Verification for chunk 2: with `cap=1` an in-flight scan is proven to behave as
today (same busy message, same resume order). With `cap=3` and three real channels
on a local user session, aggregate batch rate is measured against the single-scan
baseline and must not exceed it; FloodWait injected on one channel is observed to
pause all three. Resume with two interrupted scans proves they run together. A
`waiting` scan promotes when a slot frees.

## 7. Chunk 3 — curation and remediation

### 7.1 `admin_jobs`: one resumable job runner

`app_settings` documents are for small durable config; long fan-out work gets its
own collection. `admin_jobs` documents:

```
{_id, kind, status, params, cursor, counts, error, started_at, updated_at, finished_at}
```

`kind` is `audio_lang_backfill`, `metadata_remediation`, or `orphan_scan`.
`status` ∈ `pending, running, paused, completed, error`. Cursor is per-kind and
opaque. TTL 14 days once `finished_at` is set, matching the retention idiom at
`database.py:1132`. One job per `kind` may run at a time. Jobs are started only by
an explicit admin action — no scheduler, nothing that runs because a value was
saved.

### 7.2 Audio-language denormalization

New fields on movie and tv documents: `audio_languages: [str]` (sorted,
case-normalized display names) and `audio_language_count: int`.

Backfill job walks every cluster and both collections, projecting only
`telegram.audio_tracks.language` plus `seasons.episodes.telegram.audio_tracks.language`,
computes the distinct set, and writes with `$set` only when the value actually
changes.

Work units are enumerated as the ordered cross product of `db.clusters` (cluster
`_Cluster.index`, 1-based per §2) and the two collections `movie`, `tv`. The job
cursor is `{cluster_index, collection, last_id}`: within a unit it pages by `_id`
ascending, and when a unit exhausts it advances to the next unit and resets
`last_id`. A restart resumes at the stored unit and id, so a mid-cluster restart
never rescans completed units.

Language normalization is casefold-and-collapse-whitespace on the display name
already produced by `get_language_name` (`media_probe.py:64-69`); `None`, empty and
the map's unknown sentinel are excluded, so a title with no known audio language
gets `audio_languages: []` and count `0` rather than a phantom entry.

Counts it must report, because they define how much the cheap half covers:
titles scanned, titles updated, titles whose `audio_tracks` is `None` on every
file (unprobeable without re-fetch), and file entries dropped by `_cap_qualities`
(`database.py:1317-1332`) that would have added a language. The last two are
coverage gaps surfaced as numbers, not silently ignored.

Ingest wiring: `parse_and_queue_file` writes the two fields alongside the existing
`audio_tracks` persist at `start.py:1423`, so new files carry them without a
backfill.

`GET /admin/api/audio-curation?min_count=&sort=&page=&page_size=` aggregates across
clusters, defaulting to `audio_language_count` descending. Panel view ranks titles
so a title whose audio was replaced by a thinner one is findable, with per-file
language breakdown from the detail endpoint `main.py:743`. Switch
`AUDIO_LANG_BACKFILL=true` is required before the job can start; default off.

### 7.3 Bulk metadata remediation

Job `metadata_remediation` takes one `ADMIN_LIST_FLAGS` value (`database.py:135-146`),
pages `admin_list_media` for it, and calls the existing per-title refresh
(`main.py:717`) through a bounded-concurrency pool with TMDB 429 backoff, writing
progress to the job cursor. Completed titles are not re-run. Per-title errors
increment `counts.errors` and continue the job.

No new "needs metadata" field is invented; the seven existing flags are the
selection language. Switch `METADATA_REMEDIATION=true`, default off.

Verification for chunk 3: backfill on a seeded cluster proves unchanged docs are
not rewritten, cursor survives a restart, and reported coverage numbers match a
hand-computed count. Curation sort verified against documents with `None`,
partial and multi-language tracks. Remediation verified end-to-end against live
TMDB for one title per flag, plus the 429 path.

## 8. Chunk 4 — orphan report and alert push

### 8.1 Orphan report as a scanner mode

`_run_scan` gains `mode: "index" | "audit"`. In `audit` mode it performs the same
walk and the same classification via `_process_message`
(`channel_index.py:397`) but never enqueues, and records video messages with no
database match to `orphan_files`:

```
{_id, channel, message_id, date, file_unique_id, size, title, scanned_at, job}
```

`_id` is `"<channel>:<message_id>"`, so re-auditing a channel refreshes existing
rows instead of duplicating them; `job` names the scan that last found the post.
Rows are upserted per message. A row older than the newest completed scan of that
channel is treated as resolved and is excluded from the panel's default view.

This reuses the existing scanner rather than adding a second history walk, because
the scan already distinguishes queued, existing, non-video and deleted. Diffing
against `processed_files` (`database.py:2463-2466`) and `is_file_unique_id_known`
(`database.py:2290`) is what makes it possible at all, since media documents hold
no raw channel/message ids (§2).

Panel page lists orphans newest-first with channel filter, and a "queue these"
action that feeds the selected posts into the existing bulk linker
`queue_message_links` (`start.py:1566`) — the recovery path is the link path the
panel already has. `ORPHAN_SCAN=true`, default off.

### 8.2 Alert push

`alert_watchdog()` task started from `start_background_tasks` when enabled. Every
`ADMIN_ALERT_INTERVAL` seconds (default 300) it evaluates the same data the Health
route builds (`main.py:1896-1954`), producing an alert-key set rather than prose:
cluster down, queue backlog over threshold, FloodWait active, storage over
`storage_stop_percent`, a scan in `error`, ffmpeg missing.

State persisted at `app_settings._id = "alerts"`: `{last_keys, last_sent_at,
per_key: {key, first_seen, last_message_id}}`. A key that is new sends one message
to `ADMIN_ALERT_CHAT_ID` (default `Telegram.OWNER_ID`) through StreamBot; a key
that disappears sends one recovery line; unchanged keys send nothing. Because the
panel and the bot share the loop (§2), this is a direct call, not a queue.

A send failure logs and retries on the next tick without marking the key sent, so
an outage cannot be silently swallowed. `ADMIN_ALERT_ENABLED=true` is required;
default off means no message is ever sent.

Verification for chunk 4: orphan mode on a real channel reports known-missing posts
and enqueues none (queue count asserted unchanged before and after); alert watchdog
unit-tested on key-set transitions with a fake client, then proven live by stopping
one Mongo URI to force a cluster-down alert. Both default and enabled states proven.

## 9. Cross-cutting constraints

- **No new behaviour on default config.** Every switch left unset reproduces today,
  and both states are exercised on a real run before merge.
- **Config storage cannot break ingestion.** Runtime-config cache misses, audit
  write failures and job-runner errors are all logged and swallowed at the
  boundary; only admin-initiated reads surface errors.
- **Every mutation is audited**, from both the web panel and Telegram.
- **One ops cluster assumption.** New collections follow §4 or they vanish on ops
  relocation.
- **Session single-dyno assumption stays.** Nothing here makes multi-dyno safe.
- **No secret in logs or audit detail.** Login credentials and session strings
  never enter `detail`.
- Rate limits for `/admin` stay as configured (`security_middleware.py:22`);
  panel polling on Queue and Indexing pages must stay under 120 requests/60 s per
  IP, which bounds refresh intervals to ≥2 s.

## 10. Deliverables per chunk

1. `helper/runtime_config.py`, `helper/audit.py`, three `admin_audit` registrations,
   `queue_workers` supervisor, read-site swaps, `POST /admin/api/queue/claims/release`,
   claims in the queue snapshot, Settings + Audit pages, claim release on the Queue
   page.
2. `channel_index.py` registry refactor + governor + `waiting` status,
   multi-link `/index`, `/indexstatus`, `/indexstop <arg>`, three panel index routes,
   Indexing page.
3. `admin_jobs` collection + runner, `audio_languages` fields, backfill job,
   ingest wiring, two curation endpoints, curation + remediation UI.
4. Scanner `audit` mode + `orphan_files`, orphan UI with queue-these, `alert_watchdog`,
   alerts state doc.

Order rationale: chunk 1 changes no crawling behaviour and immediately makes the
remaining three observable and tunable; chunk 2 is the capability you asked to add
to the bot and sits on chunk 1's concurrency knob; chunks 3 and 4 are read-mostly
analysis that can land independently after the foundation.

## 11. What can and cannot be proven locally

The local harness runs the API only, against an empty database, and has no live
bot or user session attached. Each chunk therefore gets two verdicts, and the
untestable half is named rather than assumed good.

Provable locally, per chunk:

1. Knob validation and effective-value resolution with and without an override;
   audit writer swallowing failures; the claims query and release update against
   seeded `file_queue` documents; supervisor spawn/retire accounting with the
   MongoDB engine pointed at a seeded queue.
2. Registry bookkeeping — cap enforcement, per-channel stop events, `_on_task_done`
   removing only its own entry, `waiting` promotion — driven by a stubbed
   `history_client` that returns synthetic message batches. Governor arithmetic,
   including that N concurrent scans do not exceed the single-scan envelope.
3. Backfill correctness on seeded documents, including the "unchanged documents are
   not rewritten" assertion, cursor advance across cluster/collection units, and
   language normalization. Remediation pool logic against recorded TMDB responses.
4. Alert key-set transitions and dedup state with a fake notify client; orphan
   `_id` upsert and resolved-row exclusion on seeded `processed_files`.

Not provable locally, requiring a real run on the staging app before any crawl:

1. Whether `queue_workers` scaling actually raises sustained throughput, and by how
   much — needs real Telegram file fetches.
2. That the shared governor prevents a real `FloodWait`, and that a penalty applied
   by one channel genuinely pauses the others. Only real MTProto exposes this.
3. Multi-channel resume after a real restart with an interrupted scan in each state.
4. Backfill coverage on your actual corpus — the count of titles with no
   `audio_tracks` at all determines whether the curation view is useful or mostly
   empty, and that number is unknown until the job runs against real data.
5. Orphan accuracy against a live channel, and whether the reported set matches
   posts you know are missing.
6. Alert delivery to your Telegram account, including whether the bot can initiate
   the DM at all.

Chunk 3's fourth item is a genuine risk to flag now: if most files were never
probed, the denormalization produces zeros and the curation ranking is worthless
without the expensive re-probe sweep. The backfill reports its own coverage before
any UI is judged on it.

