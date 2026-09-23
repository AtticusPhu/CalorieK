"""Recorded-local chronology, explicitly NOT a timezone-inferred UTC timeline."""

from datetime import date, datetime, timedelta, timezone
from functools import cmp_to_key
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.application import ApplicationContext
from app.db.database import normalize_datetime
from app.nutrition import legacy_food_available
from app.services.daily_metrics_service import DailyMetricsService
from app.services.profile_service import ProfileService
from app.services.weight_service import WeightService
from app.timestamps import compare_local_timestamps, now_iso, parse_datetime, parse_local_datetime


DAY = date(2025, 12, 30)
HISTORICAL_EDIT_TIMESTAMPS = (
    "2025-12-30T09:00:00+14:00",
    "2025-12-30 09:00:00-12:00",
    "2025-12-30T09:00:00.123456Z",
    "2025-12-30 09:00:00.1234+08:00",
)


class TimestampPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.context = ApplicationContext(Path(temporary.name))
        self.db = self.context.database
        self.addCleanup(self.db.close)
        self.weights = WeightService(self.db)

    def historical_weight(self, value, kg=70):
        identity = self.weights.record_weight(kg, occurred_at=value)
        # Simulate existing history, NOT the canonical new-write boundary.
        with self.db.transaction() as connection:
            connection.execute("UPDATE weight_measurements SET occurred_at=? WHERE id=?", (value, identity))
        return identity

    def historical_event(self, table, identity, value):
        self.assertIn(table, ("intake_events", "exercise_events"))
        with self.db.transaction() as connection:
            connection.execute(f"UPDATE {table} SET occurred_at=? WHERE id=?", (value, identity))

    def historical_edit_case(self, kind, stamp):
        """Build a real service row, then restore its synthetic historical text."""
        if kind == "intake":
            foods = self.context.foods
            food = foods.create_food(
                name="edit fixture", category="synthetic", basis_unit="g", kj=250.125,
                protein_g=2, fat_g=3, carb_g=4, fiber_g=1,
                protein_g_per_100g=12.5, fat_g_per_100g=6.25,
                carbs_g_per_100g=20, fiber_g_per_100g=3.5,
            )
            identity = foods.record_food_intake(food, amount=100, occurred_at=stamp, note="original")
            self.historical_event("intake_events", identity, stamp)
            return identity, foods.update_intake_event, foods.get_intake_event
        if kind == "exercise":
            exercises = self.context.exercises
            source = exercises.create_exercise_type(
                name="edit fixture", default_duration_min=30, default_active_kj=500.25,
            )
            identity = exercises.record_exercise(source, occurred_at=stamp, note="original")
            self.historical_event("exercise_events", identity, stamp)
            return identity, exercises.update_exercise_event, exercises.get_exercise_event
        identity = self.historical_weight(stamp, kg=74.125)
        return identity, self.weights.update_measurement, self.weights.get_measurement

    @staticmethod
    def numeric_edit(kind, before):
        if kind == "intake":
            changes = {"amount": before["amount"] * 2}
            expected = {**changes, **{
                field: None if before[field] is None else before[field] * 2
                for field in (
                    "kj_snapshot", "protein_snapshot", "fat_snapshot", "carb_snapshot", "fiber_snapshot",
                    "protein_g", "fat_g", "carbs_g", "fiber_g",
                )
            }}
            return changes, expected
        if kind == "exercise":
            changes = {"duration_min": before["duration_min"] + 5, "active_kj": before["active_kj"] + 100.25}
        else:
            changes = {"weight_kg": before["weight_kg"] + 0.5}
        return changes, dict(changes)

    def test_non_time_service_edits_preserve_exact_history_with_or_without_resubmission(self):
        for kind in ("intake", "exercise", "weight"):
            for stamp in HISTORICAL_EDIT_TIMESTAMPS:
                for resubmit in (False, True):
                    with self.subTest(kind=kind, stamp=stamp, resubmit=resubmit):
                        identity, update, get = self.historical_edit_case(kind, stamp)
                        before = get(identity)
                        changes, expected = self.numeric_edit(kind, before)
                        if resubmit:
                            # Same datetime that the unchanged UI control returns.
                            changes["occurred_at"] = parse_datetime(stamp)
                        self.db.clear_dirty()
                        update(identity, **changes, note="ordinary edit")
                        after = get(identity)
                        self.assertEqual(after["occurred_at"], stamp)
                        self.assertEqual(after["local_date"], before["local_date"])
                        self.assertEqual(after, {
                            **before, **expected, "note": "ordinary edit", "updated_at": after["updated_at"],
                        })
                        self.assertEqual(self.db.get_dirty_from_date(), before["local_date"])

    def test_equivalent_local_clock_resubmissions_preserve_original_text_and_all_values(self):
        for kind in ("intake", "exercise", "weight"):
            for stamp in HISTORICAL_EDIT_TIMESTAMPS:
                original = parse_datetime(stamp)
                for requested in (
                    original.replace(tzinfo=None),
                    original.replace(tzinfo=timezone(timedelta(hours=1))),
                    original.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds"),
                ):
                    with self.subTest(kind=kind, stamp=stamp, requested=requested):
                        identity, update, get = self.historical_edit_case(kind, stamp)
                        before = get(identity)
                        update(identity, occurred_at=requested, note="same clock")
                        after = get(identity)
                        # Only the submitted note and normal audit field change;
                        # timestamp text/date, source, snapshots and values do not.
                        self.assertEqual(after, {**before, "note": "same clock", "updated_at": after["updated_at"]})

    def test_actual_local_retimes_canonicalize_and_invalidate_old_and_new_dates(self):
        for kind in ("intake", "exercise", "weight"):
            for stamp in HISTORICAL_EDIT_TIMESTAMPS:
                original = parse_datetime(stamp)
                for requested in (
                    original + timedelta(minutes=1),
                    original - timedelta(days=1),
                    original + timedelta(days=1),
                    # A microsecond change must be detected BEFORE canonical
                    # writes drop fractions to existing second precision.
                    original + timedelta(microseconds=1),
                    # Same global instant, different recorded-local clock.
                    original.astimezone(timezone(timedelta(hours=1))),
                ):
                    with self.subTest(kind=kind, stamp=stamp, requested=requested):
                        identity, update, get = self.historical_edit_case(kind, stamp)
                        before = get(identity)
                        self.db.clear_dirty()
                        update(identity, occurred_at=requested)
                        after = get(identity)
                        new_date = requested.date().isoformat()
                        self.assertEqual(after, {
                            **before,
                            "occurred_at": requested.replace(tzinfo=None).isoformat(timespec="seconds"),
                            "local_date": new_date,
                            "updated_at": after["updated_at"],
                        })
                        self.assertEqual(self.db.get_dirty_from_date(), min(before["local_date"], new_date))

    def test_date_only_update_preserves_historical_midnight_or_retimes_to_new_midnight(self):
        stamp = "2025-12-30 00:00:00.000Z"
        for kind in ("intake", "exercise", "weight"):
            with self.subTest(kind=kind):
                identity, update, get = self.historical_edit_case(kind, stamp)
                before = get(identity)
                update(identity, occurred_at=DAY)
                after = get(identity)
                self.assertEqual(after, {**before, "updated_at": after["updated_at"]})
                self.db.clear_dirty()
                update(identity, occurred_at=DAY + timedelta(days=1))
                after = get(identity)
                self.assertEqual(after, {
                    **before, "occurred_at": "2025-12-31T00:00:00", "local_date": "2025-12-31",
                    "updated_at": after["updated_at"],
                })
                self.assertEqual(self.db.get_dirty_from_date(), str(DAY))

    def test_unchanged_clock_intake_edit_keeps_incomplete_nutrition_and_scales_known_values(self):
        for stamp in HISTORICAL_EDIT_TIMESTAMPS:
            with self.subTest(stamp=stamp):
                identity, update, get = self.historical_edit_case("intake", stamp)
                with self.db.transaction() as connection:
                    connection.execute(
                        "UPDATE intake_events SET nutrition_complete=0, fiber_g=NULL WHERE id=?", (identity,),
                    )
                before = get(identity)
                changes, expected = self.numeric_edit("intake", before)
                update(identity, **changes, occurred_at=parse_datetime(stamp))
                after = get(identity)
                self.assertEqual(after["nutrition_complete"], 0)
                self.assertIsNone(after["fiber_g"])
                self.assertEqual(after, {**before, **expected, "updated_at": after["updated_at"]})

    def test_new_event_clocks_canonicalize_without_changing_recorded_calendar(self):
        for value in (
            "2025-12-30 00:15:00+14:00", "2025-12-30T00:15:00-12:00",
            "2025-12-30T00:15:00Z", "2025-12-30T00:15:00",
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_datetime(value), ("2025-12-30T00:15:00", "2025-12-30"))
        self.assertEqual(normalize_datetime(DAY), ("2025-12-30T00:00:00", str(DAY)))
        self.assertIsNotNone(parse_datetime(now_iso()).utcoffset())

    def test_same_instant_at_different_offsets_has_distinct_recorded_local_clocks(self):
        # One physical instant, two different recorded local dates. CalorieK
        # intentionally does NOT merge them or guess a timezone for naive history.
        first = "2025-12-30T23:30:00-02:00"
        second = "2025-12-31T03:30:00+02:00"
        self.assertEqual(parse_datetime(first), parse_datetime(second))
        self.assertLess(parse_local_datetime(first), parse_local_datetime(second))
        self.assertEqual(compare_local_timestamps("2025-12-30T12:00:00+08:00", "2025-12-30 12:00:00"), 0)
        self.assertIsNone(parse_local_datetime("2025-12-30 12:00:00").tzinfo)

    def test_lexical_order_is_not_used_for_spaces_fractions_or_offsets(self):
        values = [
            "2025-12-30T09:00:00+14:00", "2025-12-30 12:00:00-12:00",
            "2025-12-30T12:00:00.500000", "2025-12-30T12:00:00.100000+08:00",
        ]
        expected = [values[0], values[1], values[3], values[2]]
        self.assertNotEqual(sorted(values), expected)
        self.assertEqual(sorted(values, key=cmp_to_key(compare_local_timestamps)), expected)

    def test_weight_first_last_as_of_ranges_and_midnight_preserve_history(self):
        before = self.historical_weight("2025-12-29T23:59:59-12:00")
        morning = self.historical_weight("2025-12-30T09:00:00+14:00")
        noon = self.historical_weight("2025-12-30 12:00:00-12:00")
        same_clock = self.historical_weight("2025-12-30T12:00:00+08:00")
        fractional = self.historical_weight("2025-12-30T12:00:00.100000")
        after = self.historical_weight("2025-12-31T00:00:00+14:00")
        with self.db.connection() as connection:
            before_rows = tuple(connection.execute("SELECT * FROM weight_measurements ORDER BY id"))
        expected = [before, morning, noon, same_clock, fractional, after]
        self.assertEqual([r["id"] for r in self.weights.list_measurements()], expected)
        self.assertEqual([r["id"] for r in self.weights.list_measurements(descending=True, limit=2)], expected[-1:-3:-1])
        self.assertEqual([r["id"] for r in self.weights.list_measurements(start_date=DAY, end_date=DAY)], expected[1:-1])
        self.assertEqual(self.weights.latest_measurement(as_of=DAY)["id"], fractional)
        self.assertEqual(self.weights.latest_measurement(as_of="2025-12-30T12:00:00+00:00")["id"], same_clock)
        self.assertEqual(self.weights.latest_measurement(as_of="2025-12-30T12:00:00.100000-10:00")["id"], fractional)
        daily = self.context.daily
        self.assertEqual(daily._first_actual_date(), date(2025, 12, 29))
        self.assertEqual(daily._latest_actual_row(on_or_before=DAY)["id"], fractional)
        self.assertEqual(daily._latest_actual_row(as_of=datetime(2025, 12, 30, 12))["id"], same_clock)
        with self.db.connection() as connection:
            self.assertEqual(tuple(connection.execute("SELECT * FROM weight_measurements ORDER BY id")), before_rows)

    def test_event_lists_recents_defaults_and_cross_kind_timeline_use_local_clock(self):
        foods, exercises, recipes = self.context.foods, self.context.exercises, self.context.recipes
        food = foods.create_food(name="test", category="test", basis_unit="g", kj=100)
        recipe = recipes.create_recipe(name="test recipe", items=[(food, 100, "g")])
        exercise = exercises.create_exercise_type(name="test exercise", default_duration_min=10, default_active_kj=10)
        early, late = "2025-12-30T09:00:00+14:00", "2025-12-30 12:00:00-12:00"
        f_early = foods.record_food_intake(food, amount=100, occurred_at=early)
        f_late = foods.record_food_intake(food, amount=100, occurred_at=late)
        r_early = recipes.record_recipe_intake(recipe, fraction=1, occurred_at=early)
        r_late = recipes.record_recipe_intake(recipe, fraction=1, occurred_at=late)
        e_early = exercises.record_exercise(exercise, active_kj=10, occurred_at=early)
        e_late = exercises.record_exercise(exercise, active_kj=20, occurred_at=late)
        for table, identity, stamp in (
            ("intake_events", f_early, early), ("intake_events", f_late, late),
            ("intake_events", r_early, early), ("intake_events", r_late, late),
            ("exercise_events", e_early, early), ("exercise_events", e_late, late),
        ):
            self.historical_event(table, identity, stamp)
        weight = self.historical_weight("2025-12-30T10:00:00")
        self.assertEqual([r["id"] for r in foods.list_intake_events(local_date=DAY)], [f_early, r_early, f_late, r_late])
        self.assertEqual([r["id"] for r in exercises.list_exercise_events(local_date=DAY)], [e_early, e_late])
        self.assertEqual(foods.list_recent_foods()[0]["last_used_at"], late)
        self.assertEqual(recipes.list_recent_recipes()[0]["last_used_at"], late)
        self.assertEqual(exercises.list_recent_exercise_types()[0]["last_used_at"], late)
        self.assertEqual(exercises.get_last_values(exercise)["active_kj"], 20)
        records = self.context.get_daily_records(DAY)
        self.assertEqual([(r.kind, r.record_id) for r in records], [
            ("exercise", e_early), ("intake", f_early), ("intake", r_early),
            ("weight", weight), ("exercise", e_late), ("intake", f_late), ("intake", r_late),
        ])
        # DTOs and source rows retain old offsets; only sorting parses locally.
        self.assertEqual(records[0].occurred_at.utcoffset(), parse_datetime(early).utcoffset())
        self.assertEqual(len(self.context.list_intake_sources("recent")), 2)

    def test_calibration_uses_last_local_weight_not_lexical_max_and_id_breaks_ties(self):
        ProfileService(self.db).create_profile(
            gender="male", birth_date="1990-01-01", height_cm=175,
            current_weight_kg=70, effective_from=DAY, measured_at=f"{DAY}T08:00:00",
        )
        self.historical_weight(f"{DAY}T09:00:00+14:00", 71)
        self.historical_weight(f"{DAY} 12:00:00-12:00", 72)
        self.historical_weight(f"{DAY}T12:00:00+08:00", 73)
        fit = self.context.daily.calibration.fit
        captured = []

        def capture(days, **kwargs):
            captured.extend(days)
            return fit(days, **kwargs)

        with patch.object(self.context.daily, "calibration") as engine:
            engine.fit.side_effect = capture
            self.context.daily.fit_calibration(DAY)
        self.assertEqual(captured[0].actual_weight_kg, 73)

    def test_audit_provenance_keeps_aware_instant_comparison_and_rejects_mixed_domains(self):
        self.assertTrue(legacy_food_available({
            "created_at": "2025-12-30T23:00:00+08:00",
            "v3_applied_at": "2025-12-30T16:00:00+00:00",
        }))
        self.assertFalse(legacy_food_available({
            "created_at": "2025-12-29T12:00:00", "v3_applied_at": "2025-12-30T12:00:00+08:00",
        }))

    def test_canonical_and_historical_representations_produce_same_calculations(self):
        ProfileService(self.db).create_profile(
            gender="male", birth_date="1990-01-01", height_cm=175,
            current_weight_kg=70, effective_from=DAY, measured_at=f"{DAY}T08:00:00",
        )
        self.historical_weight(f"{DAY} 20:00:00+14:00", 70.1)
        intake = self.context.foods.record_custom_intake(
            name="fixture", amount=1, unit="meal", kj=7000.125,
            protein_g=30, fiber_g=4, fat_g=20, carb_g=80, occurred_at=f"{DAY}T12:00:00",
        )
        self.historical_event("intake_events", intake, f"{DAY} 12:00:00-12:00")
        daily = DailyMetricsService(self.db)
        daily.rebuild_all(DAY)

        def comparable():
            return [{k: v for k, v in row.items() if k != "calculated_at"} for row in daily.list_metrics()]

        before = comparable()
        nutrition = self.context.get_daily_nutrition(DAY)
        calibration = daily.fit_calibration(DAY)
        projection = daily.calculate_today_projection(datetime(2025, 12, 30, 21))
        # Only the synthetic copy is rewritten to build the equivalent-format
        # oracle. Production must never perform this history conversion.
        with self.db.transaction() as connection:
            for table in ("weight_measurements", "intake_events"):
                rows = connection.execute(f"SELECT id, occurred_at FROM {table}").fetchall()
                for row in rows:
                    connection.execute(
                        f"UPDATE {table} SET occurred_at=? WHERE id=?",
                        (parse_local_datetime(row["occurred_at"]).isoformat(), row["id"]),
                    )
        daily.rebuild_all(DAY)
        self.assertEqual(comparable(), before)
        self.assertEqual(self.context.get_daily_nutrition(DAY), nutrition)
        self.assertEqual(daily.fit_calibration(DAY), calibration)
        self.assertEqual(daily.calculate_today_projection(datetime(2025, 12, 30, 21)), projection)
