"""Optional gram-based nutrition; deliberately independent of energy/weight."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import fsum, isfinite
from typing import Iterable, Mapping, Any

from app.timestamps import parse_datetime


NUTRIENT_NAMES = ("protein_g", "fiber_g", "fat_g", "carbs_g")
FOOD_NUTRIENT_FIELDS = tuple(f"{name}_per_100g" for name in NUTRIENT_NAMES)
LEGACY_FOOD_FIELDS = ("protein_g", "fiber_g", "fat_g", "carb_g")
LEGACY_SNAPSHOT_FIELDS = ("protein_snapshot", "fiber_snapshot", "fat_snapshot", "carb_snapshot")
# Read-only provenance projection, not a schema column or historical backfill.
FOOD_PROVENANCE_SQL = "(SELECT applied_at FROM schema_version WHERE version = 3) AS v3_applied_at"
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


def legacy_food_available(food: Mapping[str, Any]) -> bool:
    if food.get("is_builtin") and food.get("builtin_key"):
        return True
    try:
        created = parse_datetime(str(food.get("created_at")))
        applied = parse_datetime(str(food.get("v3_applied_at")))
        # Mixed clock domains and equal (second-resolution) timestamps cannot
        # prove pre-migration origin. Do not guess that DEFAULT 0 is known.
        return created < applied
    except (ValueError, TypeError):
        return False


def food_contribution(food: Mapping[str, Any], consumed_base_amount: float) -> NutritionContribution:
    if not isfinite(consumed_base_amount) or consumed_base_amount < 0:
        raise ValueError("consumed amount must be finite and non-negative")
    legacy = legacy_food_available(food)
    values = []
    for current, old in zip(FOOD_NUTRIENT_FIELDS, LEGACY_FOOD_FIELDS, strict=True):
        value = optional_nutrient(food.get(current))
        if value is not None:
            # Explicit mass metadata cannot be applied to volume without density.
            value = value * (consumed_base_amount / 100.0) if food["basis_unit"] == "g" else None
        elif legacy:
            value = optional_nutrient(food.get(old))
            if value is not None:
                value *= consumed_base_amount / float(food["basis_amount"])
        values.append(value)
    return NutritionContribution(NutrientValues(*values), all(value is not None for value in values))


def intake_contribution(event: Mapping[str, Any]) -> NutritionContribution:
    legacy = event.get("nutrition_complete") is None
    values = []
    for current, old in zip(NUTRIENT_NAMES, LEGACY_SNAPSHOT_FIELDS, strict=True):
        value = event.get(current)
        if value is None and legacy:
            value = event.get(old)
        values.append(optional_nutrient(value))
    complete = (legacy or event.get("nutrition_complete") == 1) and all(value is not None for value in values)
    return NutritionContribution(NutrientValues(*values), complete)


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
