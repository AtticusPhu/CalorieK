"""Build kcal-composition data for the dashboard treemap."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Literal, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.database import Database


TreemapSide = Literal["intake", "burn"]

_MEAL_LABELS = {
    "BREAKFAST": "早餐",
    "LUNCH": "午餐",
    "DINNER": "晚餐",
    "SNACK": "零食",
    "OTHER": "其它摄入",
}


@dataclass(frozen=True, slots=True)
class TreemapItem:
    key: str
    name: str
    kcal: float
    side: TreemapSide
    category: str
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def area_weight(self) -> float:
        """Treemap area is always based on the absolute kcal magnitude."""

        return abs(float(self.kcal))


class TreemapDataService:
    """Translate immutable event snapshots into presentation-neutral blocks."""

    def __init__(self, database: Database | None = None) -> None:
        self.database = database

    def _load_events(
        self, day: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self.database is None:
            raise ValueError("database is required when events are not supplied")
        with self.database.connection() as connection:
            intake = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM intake_events "
                    "WHERE local_date = ? AND active = 1 ORDER BY occurred_at, id",
                    (day,),
                ).fetchall()
            ]
            exercise = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM exercise_events "
                    "WHERE local_date = ? AND active = 1 ORDER BY occurred_at, id",
                    (day,),
                ).fetchall()
            ]
        return intake, exercise

    def build_day(
        self,
        day: date | str,
        *,
        baseline_kcal: float,
        intake_events: Iterable[Mapping[str, Any]] | None = None,
        exercise_events: Iterable[Mapping[str, Any]] | None = None,
    ) -> tuple[TreemapItem, ...]:
        day_text = day.isoformat() if isinstance(day, date) else str(day)
        if intake_events is None or exercise_events is None:
            loaded_intake, loaded_exercise = self._load_events(day_text)
            if intake_events is None:
                intake_events = loaded_intake
            if exercise_events is None:
                exercise_events = loaded_exercise

        items: list[TreemapItem] = []
        for index, event in enumerate(intake_events):
            kcal = abs(float(event.get("kcal_snapshot", event.get("kcal", 0.0))))
            if kcal <= 0:
                continue
            meal = str(event.get("meal_type", "OTHER")).upper()
            items.append(
                TreemapItem(
                    key=f"intake:{event.get('id', index)}",
                    name=str(event.get("name_snapshot", event.get("name", "饮食"))),
                    kcal=kcal,
                    side="intake",
                    category=_MEAL_LABELS.get(meal, "其它摄入"),
                    details={
                        "protein_g": float(event.get("protein_snapshot", 0.0)),
                        "fat_g": float(event.get("fat_snapshot", 0.0)),
                        "carb_g": float(event.get("carb_snapshot", 0.0)),
                        "fiber_g": float(event.get("fiber_snapshot", 0.0)),
                    },
                )
            )

        if baseline_kcal > 0:
            items.append(
                TreemapItem(
                    key="burn:baseline",
                    name="基础及日常活动",
                    kcal=abs(float(baseline_kcal)),
                    side="burn",
                    category="基础及日常活动",
                )
            )

        for index, event in enumerate(exercise_events):
            kcal = abs(float(event.get("active_kcal", event.get("kcal", 0.0))))
            if kcal <= 0:
                continue
            name = str(event.get("name_snapshot", event.get("name", "运动")))
            items.append(
                TreemapItem(
                    key=f"burn:exercise:{event.get('id', index)}",
                    name=name,
                    kcal=kcal,
                    side="burn",
                    category=name,
                    details={"duration_min": float(event.get("duration_min", 0.0))},
                )
            )
        return tuple(items)

    @staticmethod
    def total_area_kcal(items: Iterable[TreemapItem]) -> float:
        return sum(item.area_weight for item in items)


__all__ = ["TreemapDataService", "TreemapItem", "TreemapSide"]

