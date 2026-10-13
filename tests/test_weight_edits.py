"""CAL-37 service/adapter regressions. All databases are temporary synthetic data."""

from datetime import date, datetime, time, timedelta, timezone
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.application import ApplicationContext
from app.services.weight_service import WeightService
from app.timestamps import parse_datetime
from app.ui.context import WeightEditDraft


class WeightEditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.context = ApplicationContext(temporary.name)
        self.db = self.context.database
        self.addCleanup(self.db.close)
        self.weights = WeightService(self.db)
        self.today = date.today()
        self.day = self.today - timedelta(days=4)
        self.measurement = self.context.profile.create_profile(
            gender="male", birth_date="1990-01-01", height_cm=175,
            current_weight_kg=79.5, effective_from=self.day,
            measured_at=datetime.combine(self.day, time(8)),
        )

    def rows(self, table):
        # Only fixed internal table names, never user input.
        with self.db.connection() as connection:
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]

    def editing_state(self):
        return {table: self.rows(table) for table in (
            "weight_measurements", "recalculation_state", "daily_metrics_cache",
            "calibration_runs", "profile", "profile_revisions",
        )}

    def history(self, *, anchor="OPEN", note=" original note ", weight=70.123456789):
        timestamp = f"{self.day.isoformat()} 08:09:17.123456+08:00"
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE weight_measurements SET weight_kg=?, occurred_at=?, anchor=?, note=?, "
                "created_at='2000-01-01T00:00:00Z', updated_at='2000-01-02T00:00:00Z' WHERE id=?",
                (weight, timestamp, anchor, note, self.measurement),
            )
        self.db.clear_dirty()
        return self.weights.get_measurement(self.measurement)

    def assert_unchanged_except(self, before, after, *fields):
        ignored = set(fields) | {"updated_at"}
        self.assertEqual(
            {key: value for key, value in before.items() if key not in ignored},
            {key: value for key, value in after.items() if key not in ignored},
        )

    def test_correction_keeps_id_and_audit_origin_and_rebuilds_dashboard(self):
        before = self.history(weight=79.5, anchor="CLOSE")
        profile = self.rows("profile")
        revisions = self.rows("profile_revisions")
        with patch("app.services.weight_service.now_iso", return_value="2000-02-01T00:00:00Z"):
            self.assertTrue(self.context.update_weight(self.measurement, WeightEditDraft(weight_kg=69.5)))
        after = self.weights.get_measurement(self.measurement)
        self.assert_unchanged_except(before, after, "weight_kg")
        self.assertEqual(after["weight_kg"], 69.5)
        self.assertNotEqual(after["updated_at"], before["updated_at"])
        self.assertEqual([row["id"] for row in self.weights.list_measurements()], [self.measurement])
        self.assertEqual(self.rows("profile"), profile)
        self.assertEqual(self.rows("profile_revisions"), revisions)
        dashboard = self.context.get_dashboard(self.day)
        self.assertEqual(dashboard.latest_actual_weight_kg, 69.5)
        self.assertEqual(dashboard.candles[-1].close_kg, 69.5)
        self.assertEqual(dashboard.candles[-1].close_source, "actual")
        self.assertEqual(dashboard.candles[-1].actual_weight_count, 1)
        self.assertIsNone(self.db.get_dirty_from_date())
        record = self.context.get_daily_records(self.day)[0]
        self.assertEqual(record.record_id, self.measurement)
        self.assertEqual(record.weight_anchor, "CLOSE")
        self.assertEqual(record.occurred_at, parse_datetime(before["occurred_at"]))

    def test_noop_keeps_exact_history_and_performs_no_write_or_recalculation(self):
        before = self.history()
        state = self.rows("recalculation_state")
        statements = []
        connect = self.db.connect

        def traced_connection():
            connection = connect()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(self.db, "connect", side_effect=traced_connection), \
                patch.object(self.db, "mark_dirty") as dirty, \
                patch.object(self.context.daily, "ensure_calculated") as calculate, \
                patch("app.services.weight_service.now_iso") as clock:
            self.assertFalse(self.context.update_weight(self.measurement, WeightEditDraft()))
            self.assertFalse(self.context.update_weight(self.measurement, WeightEditDraft(
                weight_kg=before["weight_kg"], occurred_at=parse_datetime(before["occurred_at"]),
                anchor="OPEN", note=before["note"],
            )))
        self.assertFalse(any(sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE", "BEGIN")) for sql in statements))
        dirty.assert_not_called()
        calculate.assert_not_called()
        clock.assert_not_called()
        self.assertEqual(self.weights.get_measurement(self.measurement), before)
        self.assertEqual(self.rows("recalculation_state"), state)

    def test_noop_normalized_anchor_and_offset_spelling_keep_original_storage(self):
        before = self.history()
        same_clock = parse_datetime(before["occurred_at"]).replace(tzinfo=None)
        with patch.object(self.db, "transaction") as transaction:
            self.assertFalse(self.weights.update_measurement(
                self.measurement, anchor=" open ", occurred_at=same_clock,
            ))
        transaction.assert_not_called()
        self.assertEqual(self.weights.get_measurement(self.measurement), before)

    def test_independent_fields_preserve_all_other_historical_values(self):
        cases = (
            (WeightEditDraft(note=" changed "), {"note": " changed "}),
            (WeightEditDraft(note=""), {"note": ""}),
            (WeightEditDraft(anchor="CLOSE"), {"anchor": "CLOSE"}),
            (WeightEditDraft(weight_kg=69.5), {"weight_kg": 69.5}),
            (WeightEditDraft(occurred_at=datetime.combine(self.day, time(12))),
             {"occurred_at": f"{self.day.isoformat()}T12:00:00"}),
        )
        for edit, changed in cases:
            with self.subTest(edit=edit):
                before = self.history()
                with patch.object(self.context.daily, "ensure_calculated") as calculate:
                    self.assertTrue(self.context.update_weight(self.measurement, edit))
                after = self.weights.get_measurement(self.measurement)
                self.assert_unchanged_except(before, after, *changed)
                for field, value in changed.items():
                    self.assertEqual(after[field], value)
                calculate.assert_called_once_with(self.today)
                self.assertEqual(self.db.get_dirty_from_date(), self.day.isoformat())

    def test_omitted_note_preserves_null_but_empty_note_is_explicit(self):
        before = self.history(note=None)
        with patch.object(self.context.daily, "ensure_calculated"):
            self.assertFalse(self.context.update_weight(self.measurement, WeightEditDraft()))
            self.assertEqual(self.weights.get_measurement(self.measurement), before)
            self.assertTrue(self.context.update_weight(self.measurement, WeightEditDraft(note="")))
        self.assertEqual(self.weights.get_measurement(self.measurement)["note"], "")

    def test_invalid_values_missing_and_inactive_ids_do_not_mutate_or_recalculate(self):
        before = self.history()
        with patch.object(self.context.daily, "ensure_calculated") as calculate:
            for value in (-1, 0, float("nan"), float("inf"), float("-inf")):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.context.update_weight(self.measurement, WeightEditDraft(weight_kg=value))
                self.assertEqual(self.weights.get_measurement(self.measurement), before)
                self.assertIsNone(self.db.get_dirty_from_date())
            with self.assertRaises(ValueError):
                self.weights.update_measurement(self.measurement, anchor="INVALID")
            with self.assertRaises(ValueError):
                self.weights.update_measurement(self.measurement, occurred_at="bad date")
            with self.assertRaises(LookupError):
                self.context.update_weight(999999, WeightEditDraft(weight_kg=69.5))
            with self.assertRaises(LookupError):
                self.weights.update_measurement(999999)
            self.weights.soft_delete_measurement(self.measurement)
            self.db.clear_dirty()
            inactive = self.weights.get_measurement(self.measurement)
            with self.assertRaises(LookupError):
                self.context.update_weight(self.measurement, WeightEditDraft(weight_kg=69.5))
            with self.assertRaises(LookupError):
                self.weights.update_measurement(self.measurement, note="not allowed")
            self.assertEqual(self.weights.get_measurement(self.measurement), inactive)
            self.assertIsNone(self.db.get_dirty_from_date())
        calculate.assert_not_called()
        self.weights.restore_measurement(self.measurement)
        self.assertIsNotNone(self.weights.get_measurement(self.measurement, include_inactive=False))

    def test_failure_after_dirty_write_rolls_back_fact_state_and_cache(self):
        self.context.daily.ensure_calculated(self.today)
        before = self.weights.get_measurement(self.measurement)
        cache = self.rows("daily_metrics_cache")
        state = self.rows("recalculation_state")
        mark_dirty = self.db.mark_dirty

        def fail_after_mark(*args, **kwargs):
            mark_dirty(*args, **kwargs)
            raise RuntimeError("injected after invalidation")

        with patch.object(self.db, "mark_dirty", side_effect=fail_after_mark), \
                patch.object(self.context.daily, "ensure_calculated") as calculate:
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.context.update_weight(self.measurement, WeightEditDraft(
                    weight_kg=69.5, occurred_at=datetime.combine(self.day + timedelta(days=1), time(8)),
                ))
        calculate.assert_not_called()
        self.assertEqual(self.weights.get_measurement(self.measurement), before)
        self.assertEqual(self.rows("daily_metrics_cache"), cache)
        self.assertEqual(self.rows("recalculation_state"), state)

    def test_only_weight_retime_before_profile_is_rejected_without_any_state_change(self):
        original = self.history()
        self.context.daily.ensure_calculated(self.today)
        earlier = self.day - timedelta(days=1)
        for preexisting_dirty in (False, True):
            with self.subTest(preexisting_dirty=preexisting_dirty):
                if preexisting_dirty:
                    self.db.mark_dirty(self.day + timedelta(days=1))
                before = self.editing_state()
                self.assertTrue(before["daily_metrics_cache"])
                self.assertTrue(before["calibration_runs"])
                with patch.object(WeightService, "update_measurement") as update, \
                        patch.object(self.db, "transaction", wraps=self.db.transaction) as transaction, \
                        patch.object(self.context.daily, "ensure_calculated") as calculate:
                    with self.assertRaisesRegex(ValueError, f"最早支持的称重日期为 {self.day.isoformat()}"):
                        self.context.update_weight(self.measurement, WeightEditDraft(
                            occurred_at=datetime.combine(earlier, time(8)), weight_kg=69.5,
                            anchor="CLOSE", note="must not be committed",
                        ))
                update.assert_not_called()
                transaction.assert_not_called()
                calculate.assert_not_called()
                self.assertEqual(self.editing_state(), before)
                self.assertEqual(self.weights.get_measurement(self.measurement), original)
                self.assertEqual([row.record_id for row in self.context.get_daily_records(self.day)], [self.measurement])
                self.assertEqual(self.context.get_daily_records(earlier), ())

    def test_later_weight_also_cannot_become_an_anchor_before_profile(self):
        later = self.weights.record_weight(
            70, occurred_at=datetime.combine(self.day + timedelta(days=1), time(8)),
        )
        self.context.daily.ensure_calculated(self.today)
        before = self.editing_state()
        with self.assertRaisesRegex(ValueError, self.day.isoformat()):
            self.context.update_weight(later, WeightEditDraft(
                # Compare the recorded-local date, not the next UTC date.
                occurred_at=datetime.combine(self.day - timedelta(days=1), time(23, 59),
                                             tzinfo=timezone(timedelta(hours=-12))),
            ))
        self.assertEqual(self.editing_state(), before)

    def test_retime_uses_earliest_not_latest_revision_and_accepts_boundary_date(self):
        self.context.profile.update_profile(effective_from=self.day + timedelta(days=2), height_cm=176)
        self.context.daily.ensure_calculated(self.today)
        profile = self.rows("profile")
        revisions = self.rows("profile_revisions")
        original = self.weights.get_measurement(self.measurement)
        # Valid historical date before the latest revision must still work.
        self.assertTrue(self.context.update_weight(self.measurement, WeightEditDraft(
            occurred_at=datetime.combine(self.day + timedelta(days=1), time(8)), weight_kg=69.5,
        )))
        # UTC would be the previous day; the profile boundary is recorded-local.
        self.assertTrue(self.context.update_weight(self.measurement, WeightEditDraft(
            occurred_at=datetime.combine(self.day, time.min, tzinfo=timezone(timedelta(hours=14))),
        )))
        after = self.weights.get_measurement(self.measurement)
        self.assertEqual(after["id"], self.measurement)
        self.assertEqual(after["created_at"], original["created_at"])
        self.assertEqual(after["local_date"], self.day.isoformat())
        self.assertEqual(after["occurred_at"], f"{self.day.isoformat()}T00:00:00")
        self.assertEqual(after["weight_kg"], 69.5)
        self.assertEqual(len(self.weights.list_measurements()), 1)
        self.assertEqual(self.rows("profile"), profile)
        self.assertEqual(self.rows("profile_revisions"), revisions)
        self.assertIsNone(self.db.get_dirty_from_date())
        self.assertEqual(self.context.get_dashboard(self.day).candles[-1].open_kg, 69.5)

    def test_missing_profile_revisions_reject_retime_before_any_write(self):
        self.context.daily.ensure_calculated(self.today)
        # Synthetic damaged-profile fixture, not a supported profile operation.
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM profile_revisions WHERE profile_id = 1")
        before = self.editing_state()
        with patch.object(WeightService, "update_measurement") as update, \
                patch.object(self.context.daily, "ensure_calculated") as calculate:
            with self.assertRaisesRegex(ValueError, "没有可用的用户资料版本"):
                self.context.update_weight(self.measurement, WeightEditDraft(
                    occurred_at=datetime.combine(self.day + timedelta(days=1), time(8)),
                ))
        update.assert_not_called()
        calculate.assert_not_called()
        self.assertEqual(self.editing_state(), before)

    def test_unrepresentable_last_calendar_day_is_rejected_before_any_write(self):
        self.context.daily.ensure_calculated(self.today)
        before = self.editing_state()
        with patch.object(WeightService, "update_measurement") as update, \
                patch.object(self.context.daily, "ensure_calculated") as calculate:
            with self.assertRaisesRegex(ValueError, "9999-12-31"):
                self.context.update_weight(self.measurement, WeightEditDraft(
                    occurred_at=datetime.combine(date.max, time(8)),
                ))
        update.assert_not_called()
        calculate.assert_not_called()
        self.assertEqual(self.editing_state(), before)

    def test_sql_update_failure_does_not_invalidate_or_lose_history(self):
        before = self.history()
        with self.db.transaction() as connection:
            connection.execute(
                "CREATE TRIGGER reject_weight_edit BEFORE UPDATE ON weight_measurements "
                "BEGIN SELECT RAISE(ABORT, 'injected SQL failure'); END"
            )
        with patch.object(self.context.daily, "ensure_calculated") as calculate:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected SQL failure"):
                self.context.update_weight(self.measurement, WeightEditDraft(weight_kg=69.5))
        calculate.assert_not_called()
        self.assertEqual(self.weights.get_measurement(self.measurement), before)
        self.assertIsNone(self.db.get_dirty_from_date())

    def test_rebuild_failure_retains_committed_correction_and_retryable_dirty_range(self):
        self.context.daily.ensure_calculated(self.today)
        before = self.weights.get_measurement(self.measurement)
        with patch.object(self.context.daily, "_build_calculation_context", side_effect=RuntimeError("injected rebuild")):
            with self.assertRaisesRegex(RuntimeError, "称重记录已保存") as caught:
                self.context.update_weight(self.measurement, WeightEditDraft(weight_kg=69.5))
        self.assertIn("injected rebuild", str(caught.exception))
        saved = self.weights.get_measurement(self.measurement)
        self.assert_unchanged_except(before, saved, "weight_kg")
        self.assertEqual(saved["weight_kg"], 69.5)
        self.assertEqual(self.db.get_dirty_from_date(), self.day.isoformat())
        self.assertTrue(all(row["is_dirty"] for row in self.rows("daily_metrics_cache")))
        self.context.daily.ensure_calculated(self.today)
        self.assertIsNone(self.db.get_dirty_from_date())
        self.assertEqual(self.context.get_dashboard(self.day).candles[-1].open_kg, 69.5)

    def test_cross_day_move_rebuilds_both_days_and_existing_future_display(self):
        old_day = self.day + timedelta(days=1)
        new_day = self.day + timedelta(days=2)
        future = self.today + timedelta(days=2)
        record_id = self.weights.record_weight(69.5, occurred_at=datetime.combine(old_day, time(18)))
        self.context.daily.ensure_calculated(future)
        before = self.weights.get_measurement(record_id)
        with patch.object(self.context.daily, "ensure_calculated", wraps=self.context.daily.ensure_calculated) as calculate:
            self.context.update_weight(record_id, WeightEditDraft(occurred_at=datetime.combine(new_day, time(18))))
        calculate.assert_called_once_with(future)
        after = self.weights.get_measurement(record_id)
        self.assert_unchanged_except(before, after, "occurred_at", "local_date")
        self.assertEqual(after["local_date"], new_day.isoformat())
        metrics = {row["date"]: row for row in self.context.daily.list_metrics()}
        self.assertEqual(metrics[old_day.isoformat()]["actual_weight_count"], 0)
        self.assertEqual(metrics[new_day.isoformat()]["actual_weight_count"], 1)
        self.assertEqual(metrics[new_day.isoformat()]["close_kg"], 69.5)
        self.assertEqual(metrics[future.isoformat()]["is_dirty"], 0)
        self.assertIsNone(self.db.get_dirty_from_date())
        self.assertEqual(len(self.weights.list_measurements()), 2)
        self.assertEqual(self.context.get_dashboard(old_day).latest_actual_weight_kg, 79.5)
        self.assertEqual(self.context.get_dashboard(new_day).latest_actual_weight_kg, 69.5)

    def test_first_anchor_move_rebuild_failure_keeps_new_retryable_boundary(self):
        self.context.daily.ensure_calculated(self.today)
        later = self.day + timedelta(days=1)
        with patch.object(self.context.daily, "_build_calculation_context", side_effect=RuntimeError("injected rebuild")):
            with self.assertRaisesRegex(RuntimeError, "称重记录已保存"):
                self.context.update_weight(self.measurement, WeightEditDraft(occurred_at=datetime.combine(later, time(8))))
        self.assertEqual(self.weights.get_measurement(self.measurement)["local_date"], later.isoformat())
        self.assertEqual(self.db.get_dirty_from_date(), later.isoformat())
        self.context.daily.ensure_calculated(self.today)
        self.assertIsNone(self.db.get_dirty_from_date())
        self.assertEqual(self.context.daily.list_metrics()[0]["date"], later.isoformat())

    def test_moving_first_anchor_removes_unrebuildable_day_and_can_move_back(self):
        self.context.daily.ensure_calculated(self.today)
        revisions = self.rows("profile_revisions")
        later = self.day + timedelta(days=1)
        self.context.update_weight(self.measurement, WeightEditDraft(occurred_at=datetime.combine(later, time(8))))
        self.assertEqual(self.context.get_daily_records(self.day), ())
        self.assertEqual(self.context.daily.list_metrics()[0]["date"], later.isoformat())
        self.assertTrue(all(row["window_end"] >= later.isoformat() for row in self.rows("calibration_runs")))
        self.assertIsNone(self.db.get_dirty_from_date())
        self.context.update_weight(self.measurement, WeightEditDraft(occurred_at=datetime.combine(self.day, time(8))))
        self.assertEqual(self.context.daily.list_metrics()[0]["date"], self.day.isoformat())
        self.assertEqual(self.rows("profile_revisions"), revisions)
        self.assertEqual(len(self.weights.list_measurements()), 1)

    def test_noon_boundary_and_explicit_overrides_rebuild_single_measurement_ohlc(self):
        cases = (
            (time(11, 59), "AUTO", "actual", "theoretical", "open_kg"),
            (time(12), "AUTO", "theoretical_reverse", "actual", "close_kg"),
            (time(18), "OPEN", "actual", "theoretical", "open_kg"),
            (time(8), "CLOSE", "theoretical_reverse", "actual", "close_kg"),
        )
        for clock, anchor, opening, closing, actual_field in cases:
            with self.subTest(clock=clock, anchor=anchor):
                self.context.update_weight(self.measurement, WeightEditDraft(
                    occurred_at=datetime.combine(self.day, clock), anchor=anchor,
                ))
                candle = self.context.get_dashboard(self.day).candles[-1]
                self.assertEqual((candle.open_source, candle.close_source), (opening, closing))
                self.assertEqual(getattr(candle, actual_field), 79.5)
                self.assertGreaterEqual(candle.high_kg, max(candle.open_kg, candle.close_kg))
                self.assertLessEqual(candle.low_kg, min(candle.open_kg, candle.close_kg))

    def test_multiple_measurements_reorder_original_ids_and_rebuild_extrema(self):
        middle = self.weights.record_weight(72, occurred_at=datetime.combine(self.day, time(12)))
        last = self.weights.record_weight(70, occurred_at=datetime.combine(self.day, time(18)))
        self.context.daily.ensure_calculated(self.today)
        before = self.context.get_dashboard(self.day).candles[-1]
        self.assertEqual((before.open_kg, before.close_kg, before.high_kg, before.low_kg), (79.5, 70, 79.5, 70))
        self.context.update_weight(last, WeightEditDraft(occurred_at=datetime.combine(self.day, time(7))))
        records = self.context.get_daily_records(self.day)
        self.assertEqual([row.record_id for row in records], [last, self.measurement, middle])
        candle = self.context.get_dashboard(self.day).candles[-1]
        # Existing model extrema follow the endpoint-constrained trajectory;
        # CAL-37 does not change how intermediate measurements are modelled.
        self.assertEqual((candle.open_kg, candle.close_kg, candle.high_kg, candle.low_kg), (70, 72, 72, 70))
        self.assertEqual(candle.actual_weight_count, 3)
        self.assertEqual(self.context.get_dashboard(self.day).latest_actual_weight_kg, 72)

    def test_latest_correction_refreshes_prediction_and_rolling_calibration_inputs(self):
        second_day = self.day + timedelta(days=1)
        last_day = self.day + timedelta(days=2)
        self.weights.record_weight(79.2, occurred_at=datetime.combine(second_day, time(18)))
        last = self.weights.record_weight(78.9, occurred_at=datetime.combine(last_day, time(18)))
        self.context.daily.ensure_calculated(self.today)
        before_fit = self.context.daily.fit_calibration(self.today, persist=False)
        as_of = datetime.combine(self.today, time(12))
        before_projection = self.context.daily.calculate_today_projection(as_of)
        self.context.update_weight(last, WeightEditDraft(weight_kg=69.5))
        after_fit = self.context.daily.fit_calibration(self.today, persist=False)
        after_projection = self.context.daily.calculate_today_projection(as_of)
        self.assertNotEqual(before_fit.rmse_kg, after_fit.rmse_kg)
        self.assertNotEqual(before_projection.predicted_close_kg, after_projection.predicted_close_kg)
        self.assertEqual(self.context.get_dashboard(self.today).latest_actual_weight_kg, 69.5)
        cached = self.context.daily.list_metrics(end_day=self.today)[-1]
        self.assertAlmostEqual(cached["calibration_kj"], after_fit.calibration_kj_day)
        persisted = [row for row in self.rows("calibration_runs") if row["window_end"] == self.today.isoformat()]
        self.assertEqual(len(persisted), 1)
        self.assertAlmostEqual(persisted[0]["rmse"], after_fit.rmse_kg)
        self.assertEqual(persisted[0]["weight_sample_count"], 3)
