# CAL-16: export, ownership, clocks and long-history profiling

Implementation baseline: `cb61da05abc69a816b1475a6496acfb543c24951` (`main`).
The older audit baseline in Linear is historical context. APP_VERSION remains
`0.0.2`, SCHEMA_VERSION remains `3`, and CALCULATION_VERSION remains
`v2-simple-energy-kj-1`. This is unvalidated development work for v0.0.3, not a
release or a claim that Windows validation passed.

## Investigation and decisions

| Path | Existing behavior / risk | CAL-16 decision |
| --- | --- | --- |
| `BackupService.export_json` | Separate version read; autocommit SELECTs across all non-internal tables; already stages and replaces output | One read-only connection and explicit deferred transaction covering version, integrity check, table names and all rows; retain output staging |
| `Database.connect`, `transaction`, `initialize` | WAL, short connections, BEGIN IMMEDIATE for writes; initialization takes recovery snapshots before migration/repair writes | Keep SQLite transaction/snapshot rules; add a longer-lived process lease before initialization or any service read |
| `main.main`, `ApplicationContext._bind_data_dir`, `app.paths` | CLI parses `--version` before DB/Qt startup; context binds services to resolved selected directory | No CLI change; the bound Database owns the lease; failed context startup releases it |
| Food/serving/catalogue edits | Existence, dimension and serving checks may precede a later transaction | Single owner process/thread covers both phases; CAL-13's immutable persisted unit remains unchanged |
| Profile, settings, weights, exercise defaults/edits, intake scaling | Several standalone paths read old fields before opening their write transaction | Same lease policy; no broad optimistic-concurrency retrofit or change to explicit field replacement semantics |
| Recipes and source intake | Recipe edits read current metadata first; component validation and food/recipe snapshots already share write transactions | Preserve existing transactional snapshot creation and historical nutrition fields |
| Dirty/cache/calibration | Multi-query derivation, cache commit, then dirty-state advancement | Lease spans the entire service lifetime, including intervals between transactions; preserve dirty/calculation semantics |
| Restore and relocation | Candidate validation/migration, safety backup, checkpoint, replace; relocation publishes preference after staging | Standalone restore holds the same lease; relocation reserves destination before existence check/copy; source lease remains held |
| Weight/event chronology and recent-item queries | SQL TEXT ordering differs from model's parsed local clocks | Use shared parsed-local collation; use recorded dates for date filters |
| Recalculation and charts | Daily loop invokes a rolling calibration, repeated facts/profile reads and grid search; dashboard builds 90-day candle DTOs | Add opt-in phase/query profiling; defer numerical algorithm optimizations until measurements exist |

No schema migration is required for these changes. Connection-local collations
are not embedded in schema or indexes, so external SQLite inspection, standalone
backups, schema signatures and migration validation remain portable.

Exact changed/added files (repository-relative):

```text
.gitignore
app/application.py
app/db/database.py
app/db/writer_guard.py                         (new)
app/timestamps.py                              (new)
app/nutrition.py
app/services/backup_service.py
app/services/daily_metrics_service.py
app/services/exercise_service.py
app/services/food_service.py
app/services/nutrition_service.py
app/services/recipe_service.py
app/services/treemap_service.py
app/services/weight_service.py
benchmarks/__init__.py                         (new)
benchmarks/long_history.py                     (new)
tests/test_export_consistency.py               (new)
tests/test_writer_guard.py                     (new)
tests/test_timestamp_policy.py                 (new)
tests/test_long_history_fixture.py             (new)
tests/test_services.py
docs/CAL16_HARDENING.md                         (new)
```

## JSON export consistency and failure boundary

`export_json` opens `mode=ro`, executes `BEGIN` (not `BEGIN IMMEDIATE`), and reads
the schema version, `quick_check`, `sqlite_master` table list and every exported
row on that same connection/snapshot. Tables still include all non-`sqlite_%`
tables, including inactive facts, settings, schema history, caches and calibration
runs. No table/column is filtered or added. Metadata keys and JSON number handling
are unchanged. `exported_at` is the export invocation's audit time, not a database
row commit timestamp.

WAL writers can commit between table reads. Those commits are not mixed into the
export's earlier snapshot. This guarantee also holds for an external SQL writer,
even though arbitrary external writes are outside the supported application
ownership policy. A long export can retain WAL frames until its read transaction
ends; it does not reserve the writer slot. Other journal modes may require normal
SQLite reader/writer waiting, not a weaker snapshot guarantee.

The read transaction ends before JSON file I/O. The existing same-directory
temporary file and `os.replace` publication remain: read/serialization/write/
rename failure preserves any previous successful destination and removes this
attempt's staging file. This is atomic publication, not an fsync-based guarantee
against power loss. Filesystem permission/cleanup failures remain reportable I/O
errors. Export does not acquire a CalorieK writer lease or initialize/migrate the
database. Backup also remains usable as read-only tooling while the app owns it.

## Supported concurrent-write policy

**One CalorieK writer process, one owner thread, per resolved database path.**
This is policy B from the task, not a claim that WAL prevents lost updates.

Every `Database` acquires `WriterLease` in its constructor, before any SQLite
connection, migration, repair, seeding, service read or write. The lease lives
with the Database, not with each transaction. This covers read/derive/write gaps
in service operations and recalculation. Each connection checks owner thread
and process. Cross-thread use and inherited post-fork ownership are rejected.
Keep service operations on the GUI/owner thread; parallel write workers are not
supported. Raw supplied connections must also be used only within that owner's
operation and transaction.

Windows uses a nonblocking, path-hashed `Global\\CalorieK.Writer.*` OS mutex.
`WAIT_ABANDONED` after an owner crash is accepted; ownership is not inferred from
a marker file, PID file or file existence. The global namespace covers separate
login sessions. Access/security failures fail closed rather than opening the DB
unprotected. Existing startup error handling shows the database-in-use message.
The non-Windows fallback uses `flock` on `<database>.writer.lock`; the marker is
never removed on normal release. A stale file without an OS lock is harmless.
The marker is ignored by Git and is not a recovery snapshot or database payload.

Multiple Database/ApplicationContext adapters on the same process/thread share
reference-counted ownership. Independent directories/databases have distinct
locks. Normal object release frees the last lease; tooling may explicitly call
`Database.close()` **after closing its connections and finishing all operations**.
Closed objects cannot open new connections. On Windows, finalization on the wrong
thread conservatively retains a zero-reference reservation until the owner thread
reuses/releases it or the process exits; it never unlocks another thread's mutex.
Use explicit owner-thread close for deterministic tooling lifecycles.

The desktop has one active context. Same-thread adapters are supported for
sequential tooling/tests, not independent GUI sessions with stale model settings;
after another adapter changes model settings, refresh/recreate a cached daily
service before using it. User-provided drafts still explicitly replace their
submitted fields; this is not row-version conflict detection.

Standalone `BackupService.restore_backup` reserves ownership before even reading
the archive and retains it through validation, migration staging, safety backup,
WAL handling and replacement. During relocation, source ownership remains held
and destination ownership is acquired before checking target existence, making
copy/check/replace safe against another CalorieK startup. The prepared context
shares that destination lease before preference publication. Startup/preference
failures release failed prepared-context leases and preserve CAL-14 rollback
boundaries. The already documented post-commit recalculation failure boundary is
unchanged: committed settings remain committed, with dirty state for retry.

Limits: this is a cooperative guard for CalorieK APIs, not protection against
SQLite shells, unmodified old releases or arbitrary raw `sqlite3` writers. Close
them before changing data. Read-only SQL observers/export/backup can coexist;
restore still performs SQLite checkpoint/exclusive-journal checks and fails if
another reader prevents safe replacement. Hard-link aliases, renamed live data
directories, network/sync drives and manually deleting lock files while processes
run are not supported ways to share a database. Use the same canonical local
path. Different databases may coexist, but their directory-preference UI edits
still share the existing bootstrap configuration; they are not a multi-user
configuration service.

## Timestamp inventory and canonical policy

`app.timestamps` centralizes parsing, event normalization, audit creation and the
`CALORIEK_LOCAL` collation. `app.db.database` re-exports the existing write helper
names, and the daily service re-exports its previous parser name for compatibility.

There are two **semantic domains**, deliberately not forced into one UTC clock:

| Fields | New-write representation | Read/comparison policy |
| --- | --- | --- |
| Weight/intake/exercise `occurred_at`, including initial profile weight | Local `YYYY-MM-DDTHH:MM:SS`, no offset; explicit aware input keeps its written calendar/clock, not UTC; absent input uses current local clock | Parse ISO, remove offset without conversion for model ordering/intervals; ID breaks same-clock ties |
| Entity `created_at`/`updated_at` (profile, foods, servings, recipes/items, exercise types and all facts) | Existing local ISO seconds **with known current offset** via `now_iso()` | Audit/provenance data, not model event time; retain offsets |
| `schema_version.applied_at`, `calibration_runs.created_at`, cache `calculated_at`, settings/dirty `updated_at` | Same offset-aware audit helper | Versions and model dates drive selection; never use audit strings as an event timeline |
| Backup `created_at`, export `exported_at` | Same audit helper | Descriptive metadata, not a snapshot commit/version identifier |
| `birth_date`, `effective_from`, `local_date`, cache `date`, calibration windows, dirty date | `YYYY-MM-DD` | Natural dates, not instants; date indexes/order/ranges are retained |
| Wake/sleep clocks | Existing minute precision clock representation | Existing model-local schedule/noon/24-hour-day rules unchanged |
| Backup/migration/repair filenames | Existing high-resolution local filename stamp | Uniqueness/label only, not chronological fact comparisons |

Second precision for new fact writes is unchanged; historical fractional seconds
remain intact and participate in comparisons, including `as_of` cutoffs. The
new event representation makes the existing local model explicit. It intentionally
does not retain a supplied offset on a **new or explicitly retimed** fact. It does
not rewrite an existing offset merely because amount/note/active status changes.
For intake, exercise and weight update APIs, `preserve_or_normalize_event_update`
compares the supplied and stored recorded-local clocks **before** second-resolution
normalization. An omitted timestamp or the same clock resubmitted as a datetime
or differently spelled ISO string keeps the exact stored `occurred_at` and
`local_date`, including offsets, Z, space separators and fractional-second spelling.
Only a different local date/time takes the canonical new-write path. Invalidation
still starts at the earlier of the old and new stored local dates.

Audit metadata retains its offset because legacy nutrition provenance compares
created/applied timestamps. Dropping those offsets or inventing offsets for old
naive metadata could change nutrition availability. Its existing conservative
rule remains: two aware values compare as instants, two naive values compare as
written, mixed/invalid/equal values do not prove pre-v3 origin. General
`nutrition_complete=0` behavior and CAL-12 repair predicates are unchanged.

Historical event timestamps with `T` or space separators, naive values, offsets,
`Z`, and fractions parse at read boundaries. Stored strings and exported strings
are not modified. Naive history is a recorded wall clock with **unknown timezone**,
not assumed UTC or assumed current machine timezone. Two offset representations
of the same instant can belong to different recorded local days and are therefore
different positions in this application's local-day model. Conversely equal wall
clocks with different offsets tie and use row ID. This is intentional, not global
instant ordering. Malformed ISO values fail chronology queries rather than being
silently assigned a timestamp. A global/DST-aware travel timeline would require
a separately approved model/provenance design, not just a string rewrite.

Chronological paths changed:

- Weight list/ascending/descending/limit, latest and timestamp `as_of` queries.
- Daily reference weight/projection anchor; day event/measurement ordering;
  projection event ranges and future-event exclusion.
- Rolling calibration's representative last weight per natural day, ID tie-break.
- Intake/exercise lists, latest exercise defaults, recent foods/recipes/exercises
  and combined recent intake sources.
- Nutrition/treemap input ordering and cross-kind daily-record timeline parsing.

Date filters continue to use persisted `local_date`; same-instant offsets near
midnight must not reassign intake, weight, calories or revisions to another day.
No historical facts or already-clean caches are rewritten at startup. If an
existing clean cache was built from misordered mixed-format history, the corrected
queries take effect when the normal dirty/rebuild path next recomputes it. An
explicit derived-cache rebuild for such a database should be considered in the
reviewed Windows workflow; CAL-16 does not silently force a potentially long full
rebuild, change the calculation version or inspect the user's database.

## Synthetic long-history profiling (not executed here)

From the repository root, in the normal Python environment:

```text
python -m benchmarks.long_history --years 1
python -m benchmarks.long_history --years 1 3 10
```

Default is all three presets. Presets deliberately mean 365/1095/3650 consecutive
natural days, ending 2025-12-31, not calendar-year subtraction. Facts are deterministic:
three meals (FOOD/RECIPE/CUSTOM), one exercise per day, one initial weight and
weekly later weights, explicit complete nutrition snapshots, fixed small cycles
of energy and weight. Catalogue/profile creation uses services, then batch fact
insertion keeps dataset construction separate from recalculation measurements.

`synthetic_history` always creates and cleans a fresh OS temporary directory.
There is no data-directory/backup-path argument, no lookup of live preferences,
no GUI and no `main.main` entry-point invocation. `populate_history` refuses an existing profile;
the script never clears a user database. No code runs at import time.

Output is JSON lines: per-phase `years`, `phase`, `seconds`, `items`, `statements`,
`selects`, followed by a full report containing phase records, setup time, row
counts, date boundaries, Python/SQLite/platform and unchanged version constants.
Query tracing is benchmark-only and excludes connection setup PRAGMAs; timings
include that instrumentation overhead. Reports are diagnostic, not latency SLAs.

Measured phases when the user explicitly runs it:

1. Initial production recalculation across all days.
2. Dirty-from rebuild of the final 90 days after explicit invalidation.
3. Full daily-cache/calibration rebuild.
4. Rolling calibration fit for every day (no persistence).
5. OHLC preparation across all days with real facts and persisted fitted deltas.
6. Full-range chart-row preparation, then the normal 90-day dashboard DTO/treemap/
   nutrition preparation.
7. Projection at a fixed end-day 21:00 clock.

GUI painting is intentionally excluded. The normal dashboard's historical path
is used, so system-clock changes cannot substitute today's partial projection.
The extra projection phase passes an explicit clock. Long presets may take a long
time: the unchanged calibration engine repeats a grid search for each window.

No numerical algorithm optimization was made or measured. Two query adjustments
needed for chronology also remove obvious source-level waste: latest weight uses
an SQL cutoff and `LIMIT 1` rather than transferring all historical weights; last
calibration weights are selected from the requested at-most-30-day window rather
than a full-history `MAX(TEXT)` grouping/join. SQLite still may sort scanned rows,
especially because existing indexes use default TEXT collation. No latency gain
is claimed. No model constants, formulas, EWMA/grid/tie-score rules, OHLC rules,
snapshot nutrients or invalidation transactions were changed for performance.

Post-validation performance optimization candidate (2026-09-23): the first two
benchmark-dependent candidates are now implemented without changing model
interfaces or numerical semantics. Historical recalculation builds one bounded
calculation context covering the requested range plus the preceding 29 natural
days needed by calibration. That context bulk-loads active intake, exercise and
weight facts plus effective profile revisions through one read connection, then
reuses materialized ``DailyFacts``/``CalibrationDay`` values across overlapping
windows. The standalone single-day APIs remain available.

The calibration grid search now precomputes each observation's cumulative
uncalibrated energy once, preserving the original chronological floating-point
summation order, and reuses those scalar inputs for every δ candidate.
``WeightModel`` and ``WeightTrendModel`` remain replaceable; no NumPy fast path,
model-type check, new vector interface, formula, grid, EWMA, tie-score, schema,
index, CALCULATION_VERSION or invalidation rule is introduced. The long-history
benchmark's range-wide calibration and OHLC phases also build one bounded
context per phase so the benchmark does not intentionally reintroduce per-day
raw-fact queries.

Local source verification of this candidate showed byte-for-byte identical
normalized 90-day synthetic recalculation/calibration output versus the pre-change
implementation. A 365-day ``initial_rebuild`` diagnostic dropped from about
32.72 s / 66,015 SELECTs to about 0.43 s / 10 SELECTs in the Linux handoff
environment. These are diagnostic measurements, not Windows latency claims.
Windows validation and the formal 1y/3y/10y benchmark remain required after the
change is applied. Remaining candidates: measure parsed-collation sorting cost
and revisit bounded loading only if profiling shows a need. Any new index/schema
change still needs separate approval and schema-signature/migration work.

## Authored regression coverage and handoff

- `tests/test_export_consistency.py`: actual independent WAL writer commits
  between table reads, version/table coherence, precision, read-only connection,
  checkpoint release and read/encode/partial-write/rename failure safety.
- `tests/test_writer_guard.py`: real subprocess conflict/coexistence, process
  death, stale marker, same-thread adapters, cross-thread rejection, startup,
  standalone restore/read tooling and relocation ownership boundaries.
- `tests/test_timestamp_policy.py`: naive/aware/offset/fraction/space parsing,
  same instant versus local-day semantics, midnight, first/last/range/as-of,
  event timeline/recents/defaults/calibration, unchanged historical rows,
  conservative nutrition provenance and equivalent-format numerical results.
  Review-repair cases exercise all three real update services for ordinary numeric
  edits, equivalent-clock resubmission, actual retiming/date moves, date-only
  input and unchanged incomplete-nutrition semantics, using synthetic rows only.
- `tests/test_long_history_fixture.py`: all preset datasets without live path
  access, counts/boundaries/foreign keys, deterministic smaller facts, refusal to
  overwrite a profile, dirty/full rebuild equivalence including OHLC/prediction/
  calibration/nutrition. Does not invoke the benchmark timing runner.
- `tests/test_services.py`: initial-weight expectation updated for the canonical
  new-event representation (same local clock/date).

Tests, benchmarks, the application, GUI smoke, builds, packaging and Git writes
were **not run** during implementation. Only source/static inspection is allowed
in this task. Existing CAL-13/14 and migration/repair suites remain part of the
independent review/Windows validation workflow, along with the new cases. Named
mutex behavior/security across Windows sessions, crash recovery, reader-blocked
replacement, and actual 1y/3y/10y timing/query profiles still need that validation.
The user runs the CAL-15 handoff packer after code changes; no release action or
Linear Done transition is implied by this document.
