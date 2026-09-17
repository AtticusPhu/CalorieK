"""Pure current-day weight projection.

Calibration δ is always additional daily expenditure. Positive δ is
subtracted from the model balance and lowers the predicted weight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import isfinite
from typing import Protocol, runtime_checkable

from .ohlc import EnergyEvent, EnergyEventType
from .weight_model import SimpleEnergyWeightModel, WeightModel


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


@dataclass(frozen=True, slots=True)
class DailyProjectionInput:
    """Facts and caller-computed baseline segments needed for a projection.

    ``events`` may cover the interval from the latest actual weight through
    ``as_of``; today's summary only counts events whose local date is ``day``.
    ``baseline_burn_since_anchor_kj`` covers anchor-to-as-of, while
    ``remaining_baseline_burn_kj`` covers as-of-to-midnight. Supplying these
    explicitly keeps wake/sleep integration out of this orchestration model.
    """

    day: date
    as_of: datetime
    latest_actual_weight_kg: float | None
    latest_actual_at: datetime | None
    events: tuple[EnergyEvent, ...] = ()
    baseline_burn_since_anchor_kj: float = 0.0
    remaining_baseline_burn_kj: float = 0.0
    today_baseline_burn_elapsed_kj: float = 0.0
    calibration_kj_day: float = 0.0
    calibration_days_from_anchor_to_end: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        if self.as_of.date() != self.day:
            raise ValueError("as_of must belong to day")
        if self.latest_actual_weight_kg is None:
            if self.latest_actual_at is not None:
                raise ValueError("latest_actual_at requires latest_actual_weight_kg")
        else:
            weight = _finite("latest_actual_weight_kg", self.latest_actual_weight_kg)
            if weight <= 0:
                raise ValueError("latest_actual_weight_kg must be greater than zero")
            if self.latest_actual_at is None:
                raise ValueError("latest_actual_weight_kg requires latest_actual_at")
            if self.latest_actual_at > self.as_of:
                raise ValueError("latest_actual_at cannot be after as_of")
            object.__setattr__(self, "latest_actual_weight_kg", weight)
        for name in (
            "baseline_burn_since_anchor_kj",
            "remaining_baseline_burn_kj",
            "today_baseline_burn_elapsed_kj",
        ):
            value = _finite(name, getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "calibration_kj_day",
            _finite("calibration_kj_day", self.calibration_kj_day),
        )
        if self.calibration_days_from_anchor_to_end is not None:
            fraction = _finite(
                "calibration_days_from_anchor_to_end",
                self.calibration_days_from_anchor_to_end,
            )
            if fraction < 0:
                raise ValueError("calibration_days_from_anchor_to_end cannot be negative")
            object.__setattr__(self, "calibration_days_from_anchor_to_end", fraction)
        for event in self.events:
            if event.occurred_at > self.as_of:
                raise ValueError("events must already have occurred by as_of")


@dataclass(frozen=True, slots=True)
class DailyProjectionResult:
    day: date
    latest_actual_weight_kg: float
    latest_actual_at: datetime
    predicted_close_kg: float
    predicted_change_from_actual_kg: float
    today_intake_kj: float
    today_exercise_kj: float
    today_baseline_projected_kj: float
    today_total_burn_projected_kj: float
    today_balance_kj: float
    today_model_adjusted_balance_kj: float
    calibration_kj_day: float
    calibration_applied_since_anchor_kj: float


@runtime_checkable
class DailyProjectionService(Protocol):
    def calculate_today_projection(
        self, inputs: DailyProjectionInput
    ) -> DailyProjectionResult | None:
        """Return ``None`` when no actual weight anchor exists."""


@dataclass(slots=True)
class DefaultDailyProjectionService:
    weight_model: WeightModel = SimpleEnergyWeightModel()

    def calculate_today_projection(
        self, inputs: DailyProjectionInput
    ) -> DailyProjectionResult | None:
        if inputs.latest_actual_weight_kg is None or inputs.latest_actual_at is None:
            return None

        anchor_at = inputs.latest_actual_at
        relevant_events = [
            event
            for event in inputs.events
            if anchor_at < event.occurred_at <= inputs.as_of
        ]
        energy_from_events = sum(event.signed_kj for event in relevant_events)

        if inputs.calibration_days_from_anchor_to_end is None:
            end_of_day = datetime.combine(
                inputs.day + timedelta(days=1),
                time.min,
                tzinfo=inputs.as_of.tzinfo,
            )
            try:
                calibration_days = max(
                    0.0, (end_of_day - anchor_at).total_seconds() / 86400.0
                )
            except TypeError as exc:
                raise ValueError(
                    "latest_actual_at and as_of must use compatible timezones"
                ) from exc
        else:
            calibration_days = inputs.calibration_days_from_anchor_to_end

        calibration_applied = inputs.calibration_kj_day * calibration_days
        model_energy = (
            energy_from_events
            - inputs.baseline_burn_since_anchor_kj
            - inputs.remaining_baseline_burn_kj
            - calibration_applied
        )
        predicted = inputs.latest_actual_weight_kg + self.weight_model.energy_to_weight_delta(
            model_energy
        )

        today_events = [
            event for event in inputs.events if event.occurred_at.date() == inputs.day
        ]
        today_intake = sum(
            event.kj
            for event in today_events
            if event.event_type is EnergyEventType.INTAKE
        )
        today_exercise = sum(
            event.kj
            for event in today_events
            if event.event_type is EnergyEventType.EXERCISE
        )
        today_baseline = (
            inputs.today_baseline_burn_elapsed_kj
            + inputs.remaining_baseline_burn_kj
        )
        today_total_burn = today_baseline + today_exercise
        today_balance = today_intake - today_total_burn

        return DailyProjectionResult(
            day=inputs.day,
            latest_actual_weight_kg=inputs.latest_actual_weight_kg,
            latest_actual_at=anchor_at,
            predicted_close_kg=predicted,
            predicted_change_from_actual_kg=(
                predicted - inputs.latest_actual_weight_kg
            ),
            today_intake_kj=today_intake,
            today_exercise_kj=today_exercise,
            today_baseline_projected_kj=today_baseline,
            today_total_burn_projected_kj=today_total_burn,
            today_balance_kj=today_balance,
            today_model_adjusted_balance_kj=(
                today_balance - inputs.calibration_kj_day
            ),
            calibration_kj_day=inputs.calibration_kj_day,
            calibration_applied_since_anchor_kj=calibration_applied,
        )
