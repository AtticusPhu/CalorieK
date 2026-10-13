# CAL-37: existing actual weight editing

Initial independent static review found a pre-profile retime blocker. The focused
fix below is implemented, pending new handoff review and Windows validation.
Baseline: released v0.0.3, `25c5f387da3e72ec6cfed64a2578e7f62b6c3b5b`.
Development milestone metadata in AGENTS.md is now v0.0.4 with user approval.
APP_VERSION remains 0.0.3, SCHEMA_VERSION remains 3, and
CALCULATION_VERSION remains `v2-simple-energy-kj-1`.

## Flow and preservation

- Each actual weight row in the daily timeline opens WeightDialog in edit mode,
  retaining its measurement ID. Intake/exercise dispatch remains separate.
- DailyRecordDTO appends a defaulted weight_anchor field. WeightEditDraft is a
  patch: None means unchanged; note="" explicitly clears a note. Unchanged SQL
  NULL notes stay NULL. No UI SQL or profile/onboarding metadata writes are added.
- WeightDialog exposes the original weight and datetime alongside the limited
  controls. A two-decimal display or HH:mm display is not used as the original
  fact. Unchanged (including changed-then-restored) display values are omitted
  from the patch. The service preserves the exact stored timestamp text,
  seconds/fractions/offset and full weight precision. Deliberate retiming uses
  the selected local minute; no timezone conversion is introduced.
- Advanced fields stay enabled in edit mode, retaining OPEN/CLOSE and notes.
  The edit range expands to encompass existing weights outside 20–500 kg.
  New-entry mode retains its existing range, advanced options and create API.
- WeightService rejects missing/inactive rows. Final normalized storage values
  are compared before opening a write transaction. A no-op does not UPDATE,
  change updated_at, mark dirty, recalculate, refresh or emit data_changed.
- A changed row and earliest old/new dirty date commit together using the
  existing transaction and single-writer guard. created_at/ID are preserved.
  ApplicationContext rebuilds through the maximum of old/new day, today and the
  latest existing cache date, including previously displayed future dates.
- Before a calendar-date change reaches WeightService, ApplicationContext reads
  MIN(effective_from) for profile_id=1 (the same profile used by calculation).
  There is no active flag on revisions. A target before that date, or a missing
  revision history, is rejected without opening a write transaction. The message
  states the earliest supported local date and asks the user to select that date
  or later. No fact, audit field, dirty state, cache, calibration or profile row
  changes; the existing dialog remains editable for retry/cancel. The guard uses
  recorded-local dates, never UTC or the most recent profile revision.
- Recalculation happens after fact commit. A failure propagates an explicit
  “record saved, chart rebuild failed” message; dirty state remains retryable.
  The dialog stays editable and does not emit success. Close the editor and
  refresh the dashboard to retry the existing calculation path. An unchanged
  resubmission itself is still a no-op, not an implicit rebuild request.
  A typed WeightRecalculationError tells the form to rebase its edit comparison
  values after a committed correction, so further edits/undo are not compared
  against stale pre-save values. Ordinary write errors do not rebase the form.
- Changed successful saves refresh the timeline and emit data_changed. Existing
  dashboard subscriptions refresh candles, latest actual and derived views.

## Regression code authored (not executed)

- `tests/test_weight_edits.py`: in-place 79.50→69.50 correction, onboarding
  profile/revision preservation, exact timestamp/weight no-op and SQL tracing,
  independent field changes, nullable/cleared notes, invalid and inactive rows,
  SQL failure and invalidation rollback, rebuild failure/retry, first-anchor
  movement, cross-day/future-cache rebuild, noon/explicit anchors, ordering,
  endpoint-trajectory extrema, latest weight, prediction and calibration.
  Focused review cases add exact six-table state comparisons after an invalid
  pre-profile retime with populated caches (clean and pre-dirty), later-weight
  protection, valid earliest-boundary/older-revision edits with UTC offsets,
  missing revision history, and the unrepresentable final calendar day. The
  injected rollback test now uses an allowed date so it still reaches the
  post-invalidation failure rather than stopping at the new input guard.
- `tests/test_weight_edit_ui.py`: original field display, high precision and
  minute/offset preservation through the real adapter with synthetic SQLite,
  each independently edited field, revert/no-op/cancel, out-of-range history,
  editable failure, real timeline→editor→update dispatch and notifications,
  and unchanged add-mode behavior.
  A real-adapter timeline integration case also rejects the pre-profile date,
  verifies the original row remains accessible without a success signal, then
  saves a correction after a valid-date retry in the same editor.
- `tests/test_daily_timeline_ui.py`: weight buttons and per-row dispatch,
  successful refresh/notifications, no-op/cancel suppression, exercise routing.
  Existing intake/exercise regression cases are retained.

All fixture databases are temporary and synthetic. Codex has not executed
tests, GUI smoke, benchmarks, build/packaging or the handoff script. No real
database/configuration was opened. No commit/push/tag/release or Linear mutation.

Static inspection performed: all eight changed/new Python source/test files
parsed successfully with ast.parse, without importing the application or tests;
git diff --check reported no whitespace errors. The version module has no diff.
For this focused review fix, the three modified Python files were parsed again
with ast.parse; git diff --check again reported no errors. Six regression methods
were added, but none were executed.
These source-level checks are not test execution or Windows validation evidence.

## Review boundaries and remaining Windows checks

- Verify real Qt keyboard/calendar interactions, cancel, save-error messages,
  multiple row IDs and dashboard refresh after old/new-date corrections.
- Existing OHLC mathematics are unchanged: with multiple weights, the first
  and last ordered measurements define OPEN/CLOSE; HIGH/LOW follow the
  endpoint-constrained energy trajectory, not necessarily intermediate actual
  extrema. Single-weight days respect AUTO/noon or explicit OPEN/CLOSE. Treat a
  request to include intermediate actual extremes as a separate model change.
- The previously reported pre-profile retime is now rejected before any write;
  it is not treated as a committed/retryable calculation failure. Retiming on
  or after the earliest profile date still uses the original correction path.
- Additional date review found date.max (9999-12-31) has no successor for the
  inclusive daily iterator; retiming to it is also rejected before writing.
  Other future dates and dates outside the homepage's current 90-day candle
  window remain subject to existing navigation limitations. This focused fix
  does not introduce an arbitrary historical cutoff, forbid all future dates,
  change the new-entry API, or add a calendar/history navigation feature. Any
  broader date-entry/navigation policy needs a separate decision.
- No new delete/restore UI, schema, formula, dependencies or version changes.
- User next step: run `package_caloriek_handoff.bat` at the repository root in
  default mode, then send the ZIP to ChatGPT for independent review and a
  CAL-37-specific Windows validation BAT. Tests are not claimed to pass, and
  CAL-37 must not be marked Done until that review/validation succeeds.
