from datetime import date, datetime, timedelta
import tempfile
import unittest
from pathlib import Path

from app.db.database import Database
from app.energy_units import kcal_to_kj
from app.services.food_service import FoodService


class CacheInvalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "caloriek.sqlite3"
        self.database = Database(self.database_path)
        self.database.initialize(seed_foods=False)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _insert_cache_range(self, start: date, count: int) -> None:
        with self.database.transaction() as connection:
            for offset in range(count):
                connection.execute(
                    "INSERT INTO daily_metrics_cache(" 
                    "date, calculation_version, calculated_at, is_dirty" 
                    ") VALUES (?, 'test-v1', '2026-08-25T00:00:00+08:00', 0)",
                    ((start + timedelta(days=offset)).isoformat(),),
                )

    def _dirty_by_date(self) -> dict[str, int]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT date, is_dirty FROM daily_metrics_cache ORDER BY date"
            ).fetchall()
        return {str(row["date"]): int(row["is_dirty"]) for row in rows}

    def test_mark_dirty_keeps_earliest_date_and_invalidates_only_that_date_forward(self) -> None:
        self._insert_cache_range(date(2026, 8, 18), 8)

        self.database.mark_dirty("2026-08-21")
        self.database.mark_dirty("2026-08-23")

        self.assertEqual(self.database.get_dirty_from_date(), "2026-08-21")
        self.assertEqual(
            self._dirty_by_date(),
            {
                "2026-08-18": 0,
                "2026-08-19": 0,
                "2026-08-20": 0,
                "2026-08-21": 1,
                "2026-08-22": 1,
                "2026-08-23": 1,
                "2026-08-24": 1,
                "2026-08-25": 1,
            },
        )

        self.database.mark_dirty("2026-08-19")
        self.assertEqual(self.database.get_dirty_from_date(), "2026-08-19")
        self.assertEqual(self._dirty_by_date()["2026-08-18"], 0)
        self.assertTrue(
            all(
                dirty == 1
                for day, dirty in self._dirty_by_date().items()
                if day >= "2026-08-19"
            )
        )

    def test_dirty_marker_rolls_back_with_failed_fact_transaction(self) -> None:
        self._insert_cache_range(date(2026, 8, 20), 3)

        with self.assertRaisesRegex(RuntimeError, "cancel fact write"):
            with self.database.transaction() as connection:
                self.database.mark_dirty("2026-08-20", connection=connection)
                raise RuntimeError("cancel fact write")

        self.assertIsNone(self.database.get_dirty_from_date())
        self.assertTrue(all(value == 0 for value in self._dirty_by_date().values()))

    def test_editing_five_day_old_intake_invalidates_it_and_later_cache(self) -> None:
        start = date(2026, 8, 18)
        self._insert_cache_range(start, 8)
        food_service = FoodService(self.database)
        event_id = food_service.record_custom_intake(
            name="旧午餐",
            amount=1,
            unit="份",
            kj=kcal_to_kj(500),
            occurred_at=datetime(2026, 8, 20, 12, 0),
            meal_type="LUNCH",
        )
        self.database.clear_dirty()

        food_service.update_intake_event(event_id, amount=1.5)

        self.assertEqual(self.database.get_dirty_from_date(), "2026-08-20")
        dirty = self._dirty_by_date()
        self.assertEqual(dirty["2026-08-18"], 0)
        self.assertEqual(dirty["2026-08-19"], 0)
        self.assertTrue(
            all(value == 1 for day, value in dirty.items() if day >= "2026-08-20")
        )

    def test_clear_dirty_marks_recalculation_complete(self) -> None:
        self._insert_cache_range(date(2026, 8, 20), 3)
        self.database.mark_dirty("2026-08-20")

        self.database.clear_dirty()

        self.assertIsNone(self.database.get_dirty_from_date())
        self.assertTrue(all(value == 0 for value in self._dirty_by_date().values()))


if __name__ == "__main__":
    unittest.main()
