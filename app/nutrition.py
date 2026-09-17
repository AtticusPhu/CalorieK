"""Optional gram-based nutrition; deliberately independent of energy/weight."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import fsum, isfinite
from typing import Iterable, Mapping, Any


NUTRIENT_NAMES = ("protein_g", "fiber_g", "fat_g", "carbs_g")
FOOD_NUTRIENT_FIELDS = tuple(f"{name}_per_100g" for name in NUTRIENT_NAMES)
INCOMPLETE_NUTRITION_TEXT = "部分记录无营养数据"


def optional_nutrient(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not isfinite(number) or number < 0:
        raise ValueError("营养值必须为空或大于等于 0 的有限数值")
    return number


@dataclass(frozen=True, slots=True)
class NutrientValues:
    protein_g: float | None = None
    fiber_g: float | None = None
    fat_g: float | None = None
    carbs_g: float | None = None

    def __post_init__(self) -> None:
        for name in NUTRIENT_NAMES:
            object.__setattr__(self, name, optional_nutrient(getattr(self, name)))

    def as_tuple(self) -> tuple[float | None, ...]:
        return tuple(getattr(self, name) for name in NUTRIENT_NAMES)

    def scaled(self, factor: float) -> NutrientValues:
        if not isfinite(factor) or factor < 0:
            raise ValueError("nutrition scale must be finite and non-negative")
        return NutrientValues(*(None if value is None else value * factor for value in self.as_tuple()))


@dataclass(frozen=True, slots=True)
class NutritionContribution:
    values: NutrientValues = field(default_factory=NutrientValues)
    complete: bool = False

    def scaled(self, factor: float) -> NutritionContribution:
        return NutritionContribution(self.values.scaled(factor), self.complete)

    def snapshot_values(self) -> tuple:
        return (*self.values.as_tuple(), int(self.complete))


def food_contribution(food: Mapping[str, Any], consumed_base_amount: float) -> NutritionContribution:
    # A volume is not a mass. No density exists in this release, so ml-based
    # foods/servings cannot yield a per-100g nutrient contribution.
    if food["basis_unit"] != "g":
        return NutritionContribution()
    per100 = NutrientValues(*(food.get(name) for name in FOOD_NUTRIENT_FIELDS))
    return NutritionContribution(
        per100.scaled(consumed_base_amount / 100.0),
        all(value is not None for value in per100.as_tuple()),
    )


def aggregate_contributions(items: Iterable[NutritionContribution]) -> NutritionContribution:
    contributions = tuple(items)
    if not contributions:
        return NutritionContribution(NutrientValues(0.0, 0.0, 0.0, 0.0), True)
    totals = []
    for name in NUTRIENT_NAMES:
        known = [getattr(item.values, name) for item in contributions if getattr(item.values, name) is not None]
        totals.append(fsum(known) if known else None)
    return NutritionContribution(NutrientValues(*totals), all(item.complete for item in contributions))


def format_nutrient(value: float | None) -> str:
    return "未知" if value is None else f"{value:.2f} g"


@dataclass(frozen=True, slots=True)
class DailyNutrition:
    local_date: date
    nutrients: NutrientValues
    intake_count: int
    incomplete_count: int

    @property
    def incomplete(self) -> bool:
        return self.incomplete_count > 0

    @property
    def indication(self) -> str:
        return INCOMPLETE_NUTRITION_TEXT if self.incomplete else ""
