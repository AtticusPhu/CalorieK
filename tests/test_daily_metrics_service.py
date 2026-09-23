from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.db.database import Database
from app.energy_units import DEFAULT_KJ_PER_KG, kcal_to_kj
from app.services.daily_metrics_service import DailyMetricsService
from app.services.food_service import FoodService
from app.services.profile_service import ProfileService
from app.services.weight_service import WeightService


class DailyMetricsIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name) / "caloriek.sqlite3")
        self.db.initialize()
        self.today = date.today()
        self.first_day = self.today - timedelta(days=5)
        ProfileService(self.db).create_profile(
            gender="male",
            birth_date="1990-01-01",
            height_cm=175,
            current_weight_kg=70.0,
            wake_time="07:00",
            sleep_time="23:00",
            measured_at=datetime.combine(self.first_day, time(22, 30)),
            effective_from=self.first_day,
        )
        self.foods = FoodService(self.db)
        self.metrics = DailyMetricsService(self.db)

    def tearDown(self) -> None:
        self.temporary.cleanup()


    def test_recalculate_uses_bounded_context_instead_of_per_day_fact_queries(self) -> None:
        # The historical rebuild must use the bulk calculation context. The
        # standalone single-day helper remains available for other call paths,
        # but invoking it from the rebuild would reintroduce N x rolling-window
        # database queries.
        with patch.object(
            self.metrics,
            "_daily_facts",
            side_effect=AssertionError("recalculate must not query facts day-by-day"),
        ):
            rebuilt = self.metrics.rebuild_all(self.today)

        self.assertEqual(rebuilt, (self.today - self.first_day).days + 1)
        self.assertEqual(
            len(self.metrics.list_metrics(start_day=self.first_day, end_day=self.today)),
            rebuilt,
        )

    def test_bulk_context_facts_match_standalone_daily_facts(self) -> None:
        event_day = self.first_day + timedelta(days=2)
        self.foods.record_custom_intake(
            name="上下文等价测试",
            amount=1,
            unit="份",
            kj=kcal_to_kj(750),
            occurred_at=datetime.combine(event_day, time(12, 30)),
        )
        ProfileService(self.db).update_profile(
            effective_from=event_day,
            height_cm=176,
        )
        WeightService(self.db).record_weight(
            69.8,
            occurred_at=datetime.combine(event_day, time(20, 0)),
        )

        first_actual = self.metrics._first_actual_date()
        self.assertIsNotNone(first_actual)
        assert first_actual is not None
        context = self.metrics._build_calculation_context(
            first_actual, self.today, first_actual=first_actual
        )

        for day_value in (self.first_day, event_day, self.today):
            with self.subTest(day=day_value):
                self.assertEqual(context.facts(day_value), self.metrics._daily_facts(day_value))

    def test_invalid_stored_energy_equivalence_uses_default(self) -> None:
        for value in ("inf", "-inf", "nan", "0", "-1", "invalid"):
            with self.subTest(value=value):
                self.db.set_setting("kj_per_kg", value)
                created = DailyMetricsService(self.db)
                self.assertEqual(created.weight_model.kj_per_kg, DEFAULT_KJ_PER_KG)
                self.metrics.refresh_model_settings()
                self.assertEqual(self.metrics.weight_model.kj_per_kg, DEFAULT_KJ_PER_KG)

        valid_kj_per_kg = 31234.56789012345
        self.db.set_setting("kj_per_kg", repr(valid_kj_per_kg))
        self.metrics.refresh_model_settings()
        self.assertEqual(self.metrics.weight_model.kj_per_kg, valid_kj_per_kg)

    def test_no_measurement_candle_stays_flat_but_prediction_can_rise(self) -> None:
        event_day = self.first_day + timedelta(days=1)
        self.foods.record_custom_intake(
            name="模拟高热量摄入",
            amount=1,
            unit="份",
            kj=kcal_to_kj(3000),
            occurred_at=datetime.combine(event_day, time(12, 0)),
        )
        self.metrics.rebuild_all(event_day)
        row = self.metrics.list_metrics(start_day=event_day, end_day=event_day)[0]
        self.assertEqual(row["actual_weight_count"], 0)
        self.assertAlmostEqual(row["open_kg"], 70.0)
        self.assertEqual(row["open_kg"], row["high_kg"])
        self.assertEqual(row["open_kg"], row["low_kg"])
        self.assertEqual(row["open_kg"], row["close_kg"])
        self.assertGreater(row["predicted_close_kg"], row["close_kg"])

    def test_historical_edit_rebuilds_from_dirty_date_and_matches_full_rebuild(self) -> None:
        changed_day = self.today - timedelta(days=3)
        event_id = self.foods.record_custom_intake(
            name="历史摄入",
            amount=1,
            unit="份",
            kj=kcal_to_kj(500),
            occurred_at=datetime.combine(changed_day, time(12, 0)),
        )
        self.metrics.rebuild_all(self.today)
        before = self.metrics.list_metrics(end_day=changed_day - timedelta(days=1))

        self.foods.update_intake_event(event_id, amount=2)
        self.assertEqual(self.db.get_dirty_from_date(), changed_day.isoformat())
        self.metrics.ensure_calculated(self.today)
        incremental = self.metrics.list_metrics(end_day=self.today)
        self.assertEqual(
            before,
            [row for row in incremental if row["date"] < changed_day.isoformat()],
        )
        comparable_incremental = [
            {
                key: value
                for key, value in row.items()
                if key not in {"calculated_at", "is_dirty"}
            }
            for row in incremental
        ]

        self.metrics.rebuild_all(self.today)
        full = [
            {
                key: value
                for key, value in row.items()
                if key not in {"calculated_at", "is_dirty"}
            }
            for row in self.metrics.list_metrics(end_day=self.today)
        ]
        self.assertEqual(comparable_incremental, full)
        self.assertIsNone(self.db.get_dirty_from_date())

    def test_projection_is_derived_and_never_creates_weight_facts(self) -> None:
        self.metrics.ensure_calculated(self.today)
        with self.db.connection() as connection:
            before = connection.execute(
                "SELECT COUNT(*) FROM weight_measurements"
            ).fetchone()[0]
        projection = self.metrics.calculate_today_projection(
            datetime.combine(self.today, time(10, 0))
        )
        self.assertIsNotNone(projection)
        with self.db.connection() as connection:
            after = connection.execute(
                "SELECT COUNT(*) FROM weight_measurements"
            ).fetchone()[0]
        self.assertEqual(before, after)

    def test_partial_rebuild_keeps_later_stale_cache_marked_dirty(self) -> None:
        changed_day = self.today - timedelta(days=3)
        self.metrics.rebuild_all(self.today)
        self.foods.record_custom_intake(
            name="迟到的历史记录",
            amount=1,
            unit="份",
            kj=kcal_to_kj(1000),
            occurred_at=datetime.combine(changed_day, time(12, 0)),
        )
        self.metrics.recalculate(changed_day, changed_day)
        self.assertEqual(
            self.db.get_dirty_from_date(),
            (changed_day + timedelta(days=1)).isoformat(),
        )
        with self.db.connection() as connection:
            flags = connection.execute(
                "SELECT date, is_dirty FROM daily_metrics_cache WHERE date > ? ORDER BY date",
                (changed_day.isoformat(),),
            ).fetchall()
        self.assertTrue(flags)
        self.assertTrue(all(int(row["is_dirty"]) == 1 for row in flags))

    def test_deleting_earliest_anchor_discards_unrebuildable_cache_and_converges(self) -> None:
        later_day = self.today
        weights = WeightService(self.db)
        later_id = weights.record_weight(
            69.5,
            occurred_at=datetime.combine(later_day, time(8, 0)),
        )
        self.metrics.rebuild_all(self.today)
        with self.db.connection() as connection:
            earliest_id = int(
                connection.execute(
                    "SELECT id FROM weight_measurements WHERE active = 1 "
                    "ORDER BY occurred_at, id LIMIT 1"
                ).fetchone()[0]
            )
        self.assertNotEqual(earliest_id, later_id)

        weights.soft_delete_measurement(earliest_id)
        self.metrics.ensure_calculated(self.today)
        self.assertIsNone(self.db.get_dirty_from_date())
        self.metrics.ensure_calculated(self.today)
        self.assertIsNone(self.db.get_dirty_from_date())

        rows = self.metrics.list_metrics(end_day=self.today)
        self.assertEqual([row["date"] for row in rows], [later_day.isoformat()])
        with self.db.connection() as connection:
            stale_count = connection.execute(
                "SELECT COUNT(*) FROM daily_metrics_cache WHERE date < ?",
                (later_day.isoformat(),),
            ).fetchone()[0]
        self.assertEqual(stale_count, 0)

    def test_deleting_only_anchor_clears_all_derived_rows(self) -> None:
        self.metrics.rebuild_all(self.today)
        weights = WeightService(self.db)
        with self.db.connection() as connection:
            only_id = int(
                connection.execute(
                    "SELECT id FROM weight_measurements WHERE active = 1"
                ).fetchone()[0]
            )
        weights.soft_delete_measurement(only_id)
        self.metrics.ensure_calculated(self.today)

        self.assertIsNone(self.db.get_dirty_from_date())
        self.assertEqual(self.metrics.list_metrics(end_day=self.today), [])
        with self.db.connection() as connection:
            calibration_count = connection.execute(
                "SELECT COUNT(*) FROM calibration_runs"
            ).fetchone()[0]
        self.assertEqual(calibration_count, 0)


if __name__ == "__main__":
    unittest.main()
