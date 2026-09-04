from datetime import date
import unittest

from app.services.treemap_service import TreemapDataService


class TreemapDataServiceTests(unittest.TestCase):
    def test_standard_case_uses_absolute_kcal_and_totals_3500(self) -> None:
        service = TreemapDataService()

        items = service.build_day(
            date(2026, 8, 25),
            baseline_kcal=1800,
            intake_events=(
                {
                    "id": 1,
                    "name_snapshot": "午餐",
                    "meal_type": "LUNCH",
                    "kcal_snapshot": 700,
                },
                {
                    "id": 2,
                    "name_snapshot": "晚餐",
                    "meal_type": "DINNER",
                    "kcal_snapshot": 600,
                },
            ),
            exercise_events=(
                {
                    "id": 3,
                    "name_snapshot": "跑步",
                    # The task-book writes expenditure as -400.  Area must use
                    # its magnitude and must never use the day's net balance.
                    "active_kcal": -400,
                    "duration_min": 30,
                },
            ),
        )

        self.assertEqual(service.total_area_kcal(items), 3500)
        self.assertEqual(
            sum(item.area_weight for item in items if item.side == "intake"),
            1300,
        )
        self.assertEqual(
            sum(item.area_weight for item in items if item.side == "burn"),
            2200,
        )
        self.assertEqual(
            {item.name: item.area_weight for item in items},
            {"午餐": 700, "晚餐": 600, "基础及日常活动": 1800, "跑步": 400},
        )


if __name__ == "__main__":
    unittest.main()
