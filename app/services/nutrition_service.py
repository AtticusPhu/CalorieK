"""Read daily nutrition only from immutable intake snapshots, never the library."""

from datetime import date

from app.db.database import Database, normalize_date
from app.nutrition import (
    DailyNutrition, aggregate_contributions, intake_contribution,
)


class NutritionService:
    def __init__(self, database: Database) -> None:
        self.db = database

    def daily_totals(self, day: date | str) -> DailyNutrition:
        normalized = normalize_date(day)
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT protein_g, fiber_g, fat_g, carbs_g, nutrition_complete, "
                "protein_snapshot, fiber_snapshot, fat_snapshot, carb_snapshot "
                "FROM intake_events WHERE active = 1 AND local_date = ? ORDER BY occurred_at COLLATE CALORIEK_LOCAL, id",
                (normalized,),
            ).fetchall()
        snapshots = [intake_contribution(dict(row)) for row in rows]
        total = aggregate_contributions(snapshots)
        return DailyNutrition(
            date.fromisoformat(normalized), total.values, len(rows),
            sum(not snapshot.complete for snapshot in snapshots),
        )
