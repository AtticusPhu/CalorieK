"""Personal energy calibration over at most 30 natural days.

The fitted parameter δ is *additional daily expenditure*. Positive δ lowers
predicted weight; negative δ raises it. This sign convention is shared with
the weight, OHLC, and daily-projection models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite, sqrt
from typing import Protocol, Sequence, runtime_checkable

from app.energy_units import KJ_PER_KCAL

from .weight_model import SimpleEnergyWeightModel, WeightModel


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


@dataclass(frozen=True, slots=True)
class CalibrationDay:
    """Daily raw inputs with an optional representative actual weight."""

    day: date
    intake_kj: float = 0.0
    baseline_kj: float = 0.0
    exercise_kj: float = 0.0
    actual_weight_kg: float | None = None

    def __post_init__(self) -> None:
        for name in ("intake_kj", "baseline_kj", "exercise_kj"):
            value = _finite(name, getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
            object.__setattr__(self, name, value)
        if self.actual_weight_kg is not None:
            weight = _finite("actual_weight_kg", self.actual_weight_kg)
            if weight <= 0:
                raise ValueError("actual_weight_kg must be greater than zero")
            object.__setattr__(self, "actual_weight_kg", weight)

    @property
    def uncalibrated_balance_kj(self) -> float:
        return self.intake_kj - self.baseline_kj - self.exercise_kj


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    window_start: date | None
    window_end: date | None
    weight_sample_count: int
    days_span: int
    calibration_kj_day: float
    rmse_kg: float | None
    model_version: str
    trend_model_version: str
    fitted: bool
    used_day_count: int
    reason: str = ""


@runtime_checkable
class CalibrationEngine(Protocol):
    """Replaceable interface for personal energy calibration."""

    def fit(
        self,
        days: Sequence[CalibrationDay],
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> CalibrationResult:
        """Fit positive-as-extra-burn δ from raw daily inputs."""


@runtime_checkable
class WeightTrendModel(Protocol):
    """Versioned, replaceable transformation from actual weights to trend."""

    @property
    def model_version(self) -> str:
        """Stable identifier persisted alongside a calibration run."""

    def smooth(self, weights_kg: Sequence[float]) -> tuple[float, ...]:
        """Return one trend value for each raw observation."""


@dataclass(frozen=True, slots=True)
class EWMAWeightTrend:
    """Observation-based EWMA with the conventional ``2 / (span + 1)`` α."""

    span: int = 7

    def __post_init__(self) -> None:
        if self.span < 1:
            raise ValueError("span must be at least one")

    @property
    def alpha(self) -> float:
        return 2.0 / (self.span + 1.0)

    @property
    def model_version(self) -> str:
        return f"ewma-observation-span-{self.span}-v1"

    def smooth(self, weights_kg: Sequence[float]) -> tuple[float, ...]:
        if not weights_kg:
            return ()
        values = tuple(_finite("weight_kg", value) for value in weights_kg)
        smoothed: list[float] = [values[0]]
        for value in values[1:]:
            smoothed.append(self.alpha * value + (1.0 - self.alpha) * smoothed[-1])
        return tuple(smoothed)


@dataclass(slots=True)
class GridSearchCalibrationEngine:
    """Deterministic one-dimensional grid search for δ."""

    weight_model: WeightModel = SimpleEnergyWeightModel()
    trend_model: WeightTrendModel = EWMAWeightTrend()
    minimum_kj_day: float = -2510.4
    maximum_kj_day: float = 2510.4
    step_kj_day: float = KJ_PER_KCAL
    maximum_window_days: int = 30
    model_version: str = "grid-calibration-v2"

    def __post_init__(self) -> None:
        self.minimum_kj_day = _finite("minimum_kj_day", self.minimum_kj_day)
        self.maximum_kj_day = _finite("maximum_kj_day", self.maximum_kj_day)
        self.step_kj_day = _finite("step_kj_day", self.step_kj_day)
        if self.minimum_kj_day > self.maximum_kj_day:
            raise ValueError("minimum_kj_day cannot exceed maximum_kj_day")
        if self.step_kj_day <= 0:
            raise ValueError("step_kj_day must be greater than zero")
        if self.maximum_window_days < 1 or self.maximum_window_days > 30:
            raise ValueError("maximum_window_days must be between 1 and 30")

    def fit(
        self,
        days: Sequence[CalibrationDay],
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> CalibrationResult:
        ordered = sorted(days, key=lambda item: item.day)
        if len({item.day for item in ordered}) != len(ordered):
            raise ValueError("CalibrationDay values must have unique dates")
        if not ordered:
            return self._not_fitted(None, None, 0, 0, 0, "no history")

        requested_end = end_date or ordered[-1].day
        requested_start = start_date or ordered[0].day
        if requested_start > requested_end:
            raise ValueError("start_date cannot be after end_date")
        natural_window_start = requested_end - timedelta(
            days=self.maximum_window_days - 1
        )
        effective_start = max(requested_start, natural_window_start)
        selected = [
            item
            for item in ordered
            if effective_start <= item.day <= requested_end
        ]
        if not selected:
            return self._not_fitted(
                effective_start, requested_end, 0, 0, 0, "no history in window"
            )

        window_start = selected[0].day
        window_end = selected[-1].day
        observations = [item for item in selected if item.actual_weight_kg is not None]
        sample_count = len(observations)
        if sample_count < 2:
            return self._not_fitted(
                window_start,
                window_end,
                sample_count,
                0,
                len(selected),
                "at least two actual weights are required",
            )

        first_observation = observations[0]
        last_observation = observations[-1]
        days_span = (last_observation.day - first_observation.day).days
        if days_span < 1:
            return self._not_fitted(
                window_start,
                window_end,
                sample_count,
                days_span,
                len(selected),
                "actual weights must occur on distinct natural days",
            )

        by_date = {item.day: item for item in selected}
        raw_observation_weights = tuple(
            item.actual_weight_kg for item in observations if item.actual_weight_kg is not None
        )
        trend_weights = self.trend_model.smooth(raw_observation_weights)
        if len(trend_weights) != sample_count:
            raise ValueError("trend model must return one value per observation")
        anchor_weight = trend_weights[0]

        candidates: list[float] = []
        count = int(
            round(
                (self.maximum_kj_day - self.minimum_kj_day)
                / self.step_kj_day
            )
        )
        for index in range(count + 1):
            value = self.minimum_kj_day + index * self.step_kj_day
            if value <= self.maximum_kj_day + 1e-9:
                candidates.append(value)
        if not any(abs(value) < 1e-12 for value in candidates):
            candidates.append(0.0)

        best_delta = 0.0
        best_rmse = float("inf")
        for delta in candidates:
            squared_errors: list[float] = []
            for observation, target_weight in zip(observations[1:], trend_weights[1:]):
                elapsed_days = (observation.day - first_observation.day).days
                cumulative_energy = 0.0
                cursor = first_observation.day + timedelta(days=1)
                while cursor <= observation.day:
                    daily = by_date.get(cursor)
                    if daily is not None:
                        cumulative_energy += daily.uncalibrated_balance_kj
                    cursor += timedelta(days=1)
                # Positive δ is extra burn on every elapsed natural day.
                predicted = anchor_weight + self.weight_model.energy_to_weight_delta(
                    cumulative_energy - delta * elapsed_days
                )
                squared_errors.append((target_weight - predicted) ** 2)
            rmse = sqrt(sum(squared_errors) / len(squared_errors))
            score = (rmse, abs(delta), delta)
            best_score = (best_rmse, abs(best_delta), best_delta)
            if score < best_score:
                best_delta = delta
                best_rmse = rmse

        return CalibrationResult(
            window_start=window_start,
            window_end=window_end,
            weight_sample_count=sample_count,
            days_span=days_span,
            calibration_kj_day=best_delta,
            rmse_kg=best_rmse,
            model_version=self.model_version,
            trend_model_version=self.trend_model.model_version,
            fitted=True,
            used_day_count=len(selected),
        )

    def _not_fitted(
        self,
        window_start: date | None,
        window_end: date | None,
        sample_count: int,
        days_span: int,
        used_day_count: int,
        reason: str,
    ) -> CalibrationResult:
        return CalibrationResult(
            window_start=window_start,
            window_end=window_end,
            weight_sample_count=sample_count,
            days_span=days_span,
            calibration_kj_day=0.0,
            rmse_kg=None,
            model_version=self.model_version,
            trend_model_version=self.trend_model.model_version,
            fitted=False,
            used_day_count=used_day_count,
            reason=reason,
        )
