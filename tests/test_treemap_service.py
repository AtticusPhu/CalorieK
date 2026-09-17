from datetime import date
import unittest

from app.energy_units import kcal_to_kj
from app.services.treemap_service import TreemapDataService


class TreemapDataServiceTests(unittest.TestCase):
    def test_standard_case_uses_absolute_kj_and_preserves_composition(self) -> None:
        service = TreemapDataService()

        items = service.build_day(
            date(2026, 8, 25),
            baseline_kj=kcal_to_kj(1800),
            intake_events=(
                {
                    "id": 1,
                    "name_snapshot": "午餐",
                    "meal_type": "LUNCH",
                    "kj_snapshot": kcal_to_kj(700),
                },
                {
                    "id": 2,
                    "name_snapshot": "晚餐",
                    "meal_type": "DINNER",
                    "kj_snapshot": kcal_to_kj(600),
                },
            ),
            exercise_events=(
                {
                    "id": 3,
                    "name_snapshot": "跑步",
                    # A negative expenditure still contributes positive area.
                    # Area must never use the day's net balance.
                    "active_kj": kcal_to_kj(-400),
                    "duration_min": 30,
                },
            ),
        )

        self.assertAlmostEqual(service.total_area_kj(items), kcal_to_kj(3500))
        self.assertAlmostEqual(
            sum(item.area_weight for item in items if item.side == "intake"),
            kcal_to_kj(1300),
        )
        self.assertEqual(
            sum(item.area_weight for item in items if item.side == "burn"),
            kcal_to_kj(2200),
        )
        self.assertEqual(
            {item.name: item.area_weight for item in items},
            {"午餐": kcal_to_kj(700), "晚餐": kcal_to_kj(600),
             "基础及日常活动": kcal_to_kj(1800), "跑步": kcal_to_kj(400)},
        )


if __name__ == "__main__":
    unittest.main()
