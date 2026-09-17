"""Energy-unit boundaries; runtime energy values are always in kJ.

Conversion retains Python float precision. Only the text-formatting helper
rounds for presentation; numeric conversions never round stored/model values.
"""

from __future__ import annotations

from typing import Literal


KJ_PER_KCAL = 4.184
DEFAULT_KJ_PER_KG = 32216.8
EnergyUnit = Literal["kj", "kcal"]
DEFAULT_ENERGY_DISPLAY_UNIT: EnergyUnit = "kj"


def normalize_energy_unit(value: object) -> EnergyUnit:
    """Unknown/missing preferences display kJ without repairing stored data."""

    return "kcal" if value == "kcal" else DEFAULT_ENERGY_DISPLAY_UNIT


def energy_unit_label(unit: object = DEFAULT_ENERGY_DISPLAY_UNIT) -> str:
    return "kcal" if normalize_energy_unit(unit) == "kcal" else "kJ"


def kcal_to_kj(value: float) -> float:
    """Convert an input or formula result from kcal to canonical kJ."""

    return float(value) * KJ_PER_KCAL


def kj_to_kcal(value: float) -> float:
    """Convert canonical kJ to kcal at a presentation boundary."""

    return float(value) / KJ_PER_KCAL


def energy_to_display(kj: float, unit: object = DEFAULT_ENERGY_DISPLAY_UNIT) -> float:
    """Convert canonical energy for presentation only, with no rounding."""

    return kj_to_kcal(kj) if normalize_energy_unit(unit) == "kcal" else float(kj)


def energy_from_input(value: float, unit: object = DEFAULT_ENERGY_DISPLAY_UNIT) -> float:
    """Cross the input boundary once; kJ input is stored directly."""

    return kcal_to_kj(value) if normalize_energy_unit(unit) == "kcal" else float(value)


def format_energy(
    kj: float,
    unit: object = DEFAULT_ENERGY_DISPLAY_UNIT,
    *,
    decimals: int = 2,
    signed: bool = False,
) -> str:
    """Two-decimal presentation by default; callers must never persist this formatted value."""

    sign = "+" if signed else ""
    return f"{energy_to_display(kj, unit):{sign}.{decimals}f} {energy_unit_label(unit)}"
