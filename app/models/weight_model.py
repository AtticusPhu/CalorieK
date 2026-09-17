"""Replaceable kJ-to-weight models.

``calibration_kj_day`` (δ) always means *additional daily expenditure*.
Consequently, a positive δ reduces net energy and lowers predicted weight.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Protocol, runtime_checkable

from app.energy_units import DEFAULT_KJ_PER_KG


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

    intake_kj: float = 0.0
    baseline_kj: float = 0.0
    exercise_kj: float = 0.0
    calibration_kj: float = 0.0

    def __post_init__(self) -> None:
        for name in ("intake_kj", "baseline_kj", "exercise_kj"):
            value = _finite(name, getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        _finite("calibration_kj", self.calibration_kj)

    @property
    def net_kj(self) -> float:
        return (
            float(self.intake_kj)
            - float(self.baseline_kj)
            - float(self.exercise_kj)
            - float(self.calibration_kj)
        )


@runtime_checkable
class WeightModel(Protocol):
    """Replaceable interface for energy-to-weight conversion."""

    def energy_to_weight_delta(self, energy_kj: float) -> float:
        """Convert signed net energy to a weight change in kg."""

    def project_weight(
        self,
        start_weight_kg: float,
        *,
        intake_kj: float = 0.0,
        baseline_kj: float = 0.0,
        exercise_kj: float = 0.0,
        calibration_kj_day: float = 0.0,
        days: float = 1.0,
    ) -> float:
        """Project weight, treating positive δ as extra expenditure."""


@dataclass(frozen=True, slots=True)
class SimpleEnergyWeightModel:
    """Linear energy-equivalent model with configurable kJ per kg."""

    kj_per_kg: float = DEFAULT_KJ_PER_KG
    model_version: str = "simple-energy-v1"

    def __post_init__(self) -> None:
        ratio = _finite("kj_per_kg", self.kj_per_kg)
        if ratio <= 0:
            raise ValueError("kj_per_kg must be greater than zero")

    def energy_to_weight_delta(self, energy_kj: float) -> float:
        return _finite("energy_kj", energy_kj) / float(self.kj_per_kg)

    def project_from_balance(
        self,
        start_weight_kg: float,
        balance: EnergyBalance,
    ) -> float:
        start = _finite("start_weight_kg", start_weight_kg)
        if start <= 0:
            raise ValueError("start_weight_kg must be greater than zero")
        return start + self.energy_to_weight_delta(balance.net_kj)

    def project_weight(
        self,
        start_weight_kg: float,
        *,
        intake_kj: float = 0.0,
        baseline_kj: float = 0.0,
        exercise_kj: float = 0.0,
        calibration_kj_day: float = 0.0,
        days: float = 1.0,
    ) -> float:
        """Project from an energy balance.

        δ is ``calibration_kj_day`` and is multiplied by ``days`` before it
        is subtracted. Thus positive δ always lowers the returned prediction.
        """

        duration_days = _finite("days", days)
        if duration_days < 0:
            raise ValueError("days cannot be negative")
        balance = EnergyBalance(
            intake_kj=intake_kj,
            baseline_kj=baseline_kj,
            exercise_kj=exercise_kj,
            calibration_kj=_finite(
                "calibration_kj_day", calibration_kj_day
            )
            * duration_days,
        )
        return self.project_from_balance(start_weight_kg, balance)
