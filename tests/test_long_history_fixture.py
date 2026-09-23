"""Synthetic fixture and calculation equivalence; no benchmark timings in tests."""

from datetime import datetime, time, timedelta
import unittest
from unittest.mock import patch

from app.services.daily_metrics_service import DailyMetricsService
from app.services.food_service import FoodService
from benchmarks.long_history import HistorySpec, PRESETS, synthetic_history, populate_history


class LongHistoryFixtureTests(unittest.TestCase):
    def test_all_presets_build_only_temporary_synthetic_rows_without_calculating(self):
        for years, days in PRESETS.items():
            with self.subTest(years=years):
                spec = HistorySpec.for_years(years)
                with (
                    patch("app.paths.default_data_dir", side_effect=AssertionError("live path lookup")),
                    patch("app.paths.location_config_path", side_effect=AssertionError("live preference lookup")),
                    synthetic_history(spec) as (database, counts),
                ):
                    path = database.path
                    self.assertTrue(path.parent.name.startswith("caloriek-cal16-synthetic-"))
                    self.assertEqual(counts["days"], days)
                    with database.connection() as connection:
                        for table, count in (
                            ("intake_events", days * 3), ("exercise_events", days),
                            ("weight_measurements", spec.weight_count),
                            ("daily_metrics_cache", 0), ("calibration_runs", 0),
                        ):
                            self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], count)
                        extent = connection.execute("SELECT MIN(local_date), MAX(local_date) FROM intake_events").fetchone()
                        self.assertEqual(tuple(extent), (str(spec.start), str(spec.end)))
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM intake_events WHERE nutrition_complete != 1").fetchone()[0], 0)
                        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                    self.assertEqual(database.get_dirty_from_date(), str(spec.start))
                self.assertFalse(path.parent.exists())

    def test_small_fixture_is_deterministic_and_incremental_equals_full_results(self):
        spec = HistorySpec(9)
        with synthetic_history(spec) as (first, counts), synthetic_history(spec) as (second, other_counts):
            self.assertEqual(counts, other_counts)
            for table in ("weight_measurements", "intake_events", "exercise_events"):
                def facts(database):
                    with database.connection() as connection:
                        return [{k: v for k, v in dict(row).items() if k not in ("created_at", "updated_at")}
                                for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]
                self.assertEqual(facts(first), facts(second))
            daily = DailyMetricsService(first)
            daily.rebuild_all(spec.end)
            initial = daily.list_metrics()
            self.assertEqual(len(initial), spec.days)
            self.assertEqual(initial[0]["intake_kj"], 3 * 2400.125)
            self.assertEqual(initial[0]["exercise_kj"], 600.25)
            self.assertEqual(initial[0]["open_kg"], 70)
            self.assertEqual(initial[0]["actual_weight_count"], 1)
            changed_day = spec.end - timedelta(days=2)
            food = FoodService(first)
            event = food.list_intake_events(local_date=changed_day)[0]
            food.update_intake_event(event["id"], amount=200)
            self.assertEqual(first.get_dirty_from_date(), str(changed_day))
            daily.ensure_calculated(spec.end)

            def comparable():
                return [{k: v for k, v in row.items() if k != "calculated_at"} for row in daily.list_metrics()]

            incremental = comparable()
            fit = daily.fit_calibration(spec.end)
            as_of = datetime.combine(spec.end, time(21))
            projection = daily.calculate_today_projection(as_of)
            daily.rebuild_all(spec.end)
            self.assertEqual(comparable(), incremental)
            self.assertEqual(daily.fit_calibration(spec.end), fit)
            self.assertEqual(daily.calculate_today_projection(as_of), projection)
            self.assertIsNone(first.get_dirty_from_date())
            self.assertEqual(food.get_intake_event(event["id"])["protein_g"], 40)

    def test_populate_refuses_existing_profile_without_deleting_facts(self):
        with synthetic_history(HistorySpec(1)) as (database, _counts):
            with database.connection() as connection:
                before = tuple(connection.iterdump())
            with self.assertRaises(ValueError):
                populate_history(database, HistorySpec(1))
            with database.connection() as connection:
                self.assertEqual(tuple(connection.iterdump()), before)
