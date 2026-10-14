"""Typed, explicit per-occurrence corrections shared by services and the UI."""

from dataclasses import dataclass

from app.nutrition import NutrientValues


@dataclass(frozen=True, slots=True)
class IntakeSnapshotEdit:
    """Absolute totals for the final portion, never per-100 or catalog values.

    Each nutrient is either a known nonnegative value or explicitly unknown.
    Passing no snapshot edit requests ordinary proportional scaling instead.
    """

    energy_kj: float
    nutrients: NutrientValues


@dataclass(frozen=True, slots=True)
class CustomIntakeEdit:
    """CUSTOM-only saved description; does not create a catalog dependency."""

    name: str
    unit: str
