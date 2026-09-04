"""Replaceable kcal-to-weight models.

``calibration_kcal_day`` (δ) always means *additional daily expenditure*.
Consequently, a positive δ reduces net energy and lowers predicted weight.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Protocol, runtime_checkable


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


@dataclass(frozen=True, slots=True)
class EnergyBalance:
    """Energy components for a time span.

    Calibration is expressed as the total extra expenditure for this balance,
    not as a signed intake. Positive calibration therefore decreases ``net``.
    """

    intake_kcal: float = 0.0
    baseline_kcal: float = 0.0
    exercise_kcal: float = 0.0
    calibration_kcal: float = 0.0

    def __post_init__(self) -> None:
        for name in ("intake_kcal", "baseline_kcal", "exercise_kcal"):
            value = _finite(name, getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        _finite("calibration_kcal", self.calibration_kcal)

    @property
    def net_kcal(self) -> float:
        return (
            float(self.intake_kcal)
            - float(self.baseline_kcal)
            - float(self.exercise_kcal)
            - float(self.calibration_kcal)
        )


@runtime_checkable
class WeightModel(Protocol):
    """Replaceable interface for energy-to-weight conversion."""

    def energy_to_weight_delta(self, energy_kcal: float) -> float:
        """Convert signed net energy to a weight change in kg."""

    def project_weight(
        self,
        start_weight_kg: float,
        *,
        intake_kcal: float = 0.0,
        baseline_kcal: float = 0.0,
        exercise_kcal: float = 0.0,
        calibration_kcal_day: float = 0.0,
        days: float = 1.0,
    ) -> float:
        """Project weight, treating positive δ as extra expenditure."""


@dataclass(frozen=True, slots=True)
class SimpleEnergyWeightModel:
    """Linear energy-equivalent model with configurable kcal per kg."""

    kcal_per_kg: float = 7700.0
    model_version: str = "simple-energy-v1"

    def __post_init__(self) -> None:
        ratio = _finite("kcal_per_kg", self.kcal_per_kg)
        if ratio <= 0:
            raise ValueError("kcal_per_kg must be greater than zero")

    def energy_to_weight_delta(self, energy_kcal: float) -> float:
        return _finite("energy_kcal", energy_kcal) / float(self.kcal_per_kg)

    def project_from_balance(
        self,
        start_weight_kg: float,
        balance: EnergyBalance,
    ) -> float:
        start = _finite("start_weight_kg", start_weight_kg)
        if start <= 0:
            raise ValueError("start_weight_kg must be greater than zero")
        return start + self.energy_to_weight_delta(balance.net_kcal)

    def project_weight(
        self,
        start_weight_kg: float,
        *,
        intake_kcal: float = 0.0,
        baseline_kcal: float = 0.0,
        exercise_kcal: float = 0.0,
        calibration_kcal_day: float = 0.0,
        days: float = 1.0,
    ) -> float:
        """Project from an energy balance.

        δ is ``calibration_kcal_day`` and is multiplied by ``days`` before it
        is subtracted. Thus positive δ always lowers the returned prediction.
        """

        duration_days = _finite("days", days)
        if duration_days < 0:
            raise ValueError("days cannot be negative")
        balance = EnergyBalance(
            intake_kcal=intake_kcal,
            baseline_kcal=baseline_kcal,
            exercise_kcal=exercise_kcal,
            calibration_kcal=_finite(
                "calibration_kcal_day", calibration_kcal_day
            )
            * duration_days,
        )
        return self.project_from_balance(start_weight_kg, balance)
