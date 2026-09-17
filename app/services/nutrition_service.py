"""Read daily nutrition only from immutable intake snapshots, never the library."""

from datetime import date

from app.db.database import Database, normalize_date
from app.nutrition import (
    DailyNutrition, NUTRIENT_NAMES, NutrientValues, NutritionContribution, aggregate_contributions,
)


class NutritionService:
    def __init__(self, database: Database) -> None:
        self.db = database

    def daily_totals(self, day: date | str) -> DailyNutrition:
        normalized = normalize_date(day)
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT protein_g, fiber_g, fat_g, carbs_g, nutrition_complete "
                "FROM intake_events WHERE active = 1 AND local_date = ? ORDER BY occurred_at, id",
                (normalized,),
            ).fetchall()
        snapshots = []
        for row in rows:
            values = NutrientValues(*(row[name] for name in NUTRIENT_NAMES))
            complete = row["nutrition_complete"] == 1 and all(value is not None for value in values.as_tuple())
            snapshots.append(NutritionContribution(values, complete))
        total = aggregate_contributions(snapshots)
        return DailyNutrition(
            date.fromisoformat(normalized), total.values, len(rows),
            sum(not snapshot.complete for snapshot in snapshots),
        )
