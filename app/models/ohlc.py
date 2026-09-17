"""Pure daily weight OHLC generation and intraday theoretical trajectory.

Calibration δ is additional daily expenditure. A positive δ is distributed
continuously across the day and lowers the theoretical trajectory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from math import isfinite

from .weight_model import SimpleEnergyWeightModel, WeightModel


class MissingWeightAnchorError(ValueError):
    """Raised when a day has neither an actual weight nor a previous close."""


class WeightAnchor(str, Enum):
    AUTO = "AUTO"
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class EnergyEventType(str, Enum):
    INTAKE = "intake"
    EXERCISE = "exercise"


class ValueSource(str, Enum):
    ACTUAL = "actual"
    PREVIOUS_CLOSE = "previous_close"
    THEORETICAL = "theoretical"
    THEORETICAL_REVERSE = "theoretical_reverse"


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _coerce_anchor(value: WeightAnchor | str) -> WeightAnchor:
    if isinstance(value, WeightAnchor):
        return value
    try:
        return WeightAnchor(str(value).strip().upper())
    except ValueError as exc:
        raise ValueError(f"unsupported weight anchor: {value!r}") from exc


def _coerce_event_type(value: EnergyEventType | str) -> EnergyEventType:
    if isinstance(value, EnergyEventType):
        return value
    try:
        return EnergyEventType(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported energy event type: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class WeightMeasurement:
    occurred_at: datetime
    weight_kg: float
    anchor: WeightAnchor | str = WeightAnchor.AUTO
    note: str = ""

    def __post_init__(self) -> None:
        weight = _finite("weight_kg", self.weight_kg)
        if weight <= 0:
            raise ValueError("weight_kg must be greater than zero")
        object.__setattr__(self, "weight_kg", weight)
        object.__setattr__(self, "anchor", _coerce_anchor(self.anchor))

    @property
    def resolved_anchor(self) -> WeightAnchor:
        if self.anchor is not WeightAnchor.AUTO:
            return self.anchor
        return (
            WeightAnchor.OPEN
            if self.occurred_at.time() < time(12, 0)
            else WeightAnchor.CLOSE
        )


@dataclass(frozen=True, slots=True)
class EnergyEvent:
    """A positive-sized intake or active-exercise event."""

    occurred_at: datetime
    kj: float
    event_type: EnergyEventType | str
    name: str = ""

    def __post_init__(self) -> None:
        kj = _finite("kj", self.kj)
        if kj < 0:
            raise ValueError("kj must be non-negative; use event_type for sign")
        object.__setattr__(self, "kj", kj)
        object.__setattr__(self, "event_type", _coerce_event_type(self.event_type))

    @property
    def signed_kj(self) -> float:
        return self.kj if self.event_type is EnergyEventType.INTAKE else -self.kj


@dataclass(frozen=True, slots=True)
class OHLCInput:
    day: date
    previous_close_kg: float | None = None
    measurements: tuple[WeightMeasurement, ...] = ()
    events: tuple[EnergyEvent, ...] = ()
    baseline_kj: float = 0.0
    calibration_kj_day: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "measurements", tuple(self.measurements))
        object.__setattr__(self, "events", tuple(self.events))
        if self.previous_close_kg is not None:
            previous = _finite("previous_close_kg", self.previous_close_kg)
            if previous <= 0:
                raise ValueError("previous_close_kg must be greater than zero")
            object.__setattr__(self, "previous_close_kg", previous)
        baseline = _finite("baseline_kj", self.baseline_kj)
        if baseline < 0:
            raise ValueError("baseline_kj cannot be negative")
        object.__setattr__(self, "baseline_kj", baseline)
        object.__setattr__(
            self,
            "calibration_kj_day",
            _finite("calibration_kj_day", self.calibration_kj_day),
        )


@dataclass(frozen=True, slots=True)
class TrajectoryPoint:
    fraction_of_day: float
    weight_kg: float
    label: str = ""


@dataclass(frozen=True, slots=True)
class OHLCResult:
    day: date
    open_kg: float
    high_kg: float
    low_kg: float
    close_kg: float
    predicted_close_kg: float
    open_source: ValueSource
    close_source: ValueSource
    actual_weight_count: int
    model_energy_balance_kj: float
    trajectory: tuple[TrajectoryPoint, ...]

    @property
    def open(self) -> float:
        return self.open_kg

    @property
    def high(self) -> float:
        return self.high_kg

    @property
    def low(self) -> float:
        return self.low_kg

    @property
    def close(self) -> float:
        return self.close_kg


@dataclass(slots=True)
class OHLCGenerator:
    """Generate actual-weight candles while keeping predictions separate."""

    weight_model: WeightModel = SimpleEnergyWeightModel()

    def generate_day(self, inputs: OHLCInput) -> OHLCResult:
        measurements = sorted(inputs.measurements, key=lambda item: item.occurred_at)
        events = sorted(inputs.events, key=lambda item: item.occurred_at)
        for measurement in measurements:
            if measurement.occurred_at.date() != inputs.day:
                raise ValueError("all measurements must belong to input day")
        for event in events:
            if event.occurred_at.date() != inputs.day:
                raise ValueError("all energy events must belong to input day")

        event_energy = sum(event.signed_kj for event in events)
        # δ is extra expenditure, so a positive value is subtracted.
        model_energy = (
            event_energy - inputs.baseline_kj - inputs.calibration_kj_day
        )
        model_delta = self.weight_model.energy_to_weight_delta(model_energy)
        count = len(measurements)

        if count == 0:
            if inputs.previous_close_kg is None:
                raise MissingWeightAnchorError(
                    "cannot generate the first candle without a previous close "
                    "or an actual measurement"
                )
            actual = inputs.previous_close_kg
            predicted = actual + model_delta
            trajectory = (
                TrajectoryPoint(0.0, actual, "previous close"),
                TrajectoryPoint(1.0, actual, "flat: no actual measurement"),
            )
            return OHLCResult(
                day=inputs.day,
                open_kg=actual,
                high_kg=actual,
                low_kg=actual,
                close_kg=actual,
                predicted_close_kg=predicted,
                open_source=ValueSource.PREVIOUS_CLOSE,
                close_source=ValueSource.PREVIOUS_CLOSE,
                actual_weight_count=0,
                model_energy_balance_kj=model_energy,
                trajectory=trajectory,
            )

        if count == 1:
            only = measurements[0]
            if only.resolved_anchor is WeightAnchor.OPEN:
                open_kg = only.weight_kg
                close_kg = open_kg + model_delta
                predicted_close = close_kg
                open_source = ValueSource.ACTUAL
                close_source = ValueSource.THEORETICAL
            else:
                close_kg = only.weight_kg
                open_kg = close_kg - model_delta
                predicted_close = close_kg
                open_source = ValueSource.THEORETICAL_REVERSE
                close_source = ValueSource.ACTUAL
        else:
            open_kg = measurements[0].weight_kg
            close_kg = measurements[-1].weight_kg
            predicted_close = open_kg + model_delta
            open_source = ValueSource.ACTUAL
            close_source = ValueSource.ACTUAL

        trajectory = self._build_trajectory(
            open_kg=open_kg,
            target_close_kg=close_kg,
            events=events,
            baseline_kj=inputs.baseline_kj,
            calibration_kj_day=inputs.calibration_kj_day,
            day=inputs.day,
        )
        high = max(point.weight_kg for point in trajectory)
        low = min(point.weight_kg for point in trajectory)
        high = max(high, open_kg, close_kg)
        low = min(low, open_kg, close_kg)
        return OHLCResult(
            day=inputs.day,
            open_kg=open_kg,
            high_kg=high,
            low_kg=low,
            close_kg=close_kg,
            predicted_close_kg=predicted_close,
            open_source=open_source,
            close_source=close_source,
            actual_weight_count=count,
            model_energy_balance_kj=model_energy,
            trajectory=trajectory,
        )

    def _build_trajectory(
        self,
        *,
        open_kg: float,
        target_close_kg: float,
        events: list[EnergyEvent],
        baseline_kj: float,
        calibration_kj_day: float,
        day: date,
    ) -> tuple[TrajectoryPoint, ...]:
        start = datetime.combine(day, time.min, tzinfo=(events[0].occurred_at.tzinfo if events else None))
        continuous_energy = -baseline_kj - calibration_kj_day
        fraction = 0.0
        weight = open_kg
        raw_points: list[TrajectoryPoint] = [TrajectoryPoint(0.0, weight, "open")]

        for event in events:
            event_fraction = (event.occurred_at - start).total_seconds() / 86400.0
            if not 0.0 <= event_fraction < 1.0:
                raise ValueError("energy event must occur inside the natural day")
            weight += self.weight_model.energy_to_weight_delta(
                continuous_energy * (event_fraction - fraction)
            )
            raw_points.append(TrajectoryPoint(event_fraction, weight, "before event"))
            weight += self.weight_model.energy_to_weight_delta(event.signed_kj)
            raw_points.append(
                TrajectoryPoint(event_fraction, weight, event.name or event.event_type.value)
            )
            fraction = event_fraction

        weight += self.weight_model.energy_to_weight_delta(
            continuous_energy * (1.0 - fraction)
        )
        raw_points.append(TrajectoryPoint(1.0, weight, "model close"))

        closing_error = target_close_kg - raw_points[-1].weight_kg
        adjusted = tuple(
            TrajectoryPoint(
                point.fraction_of_day,
                point.weight_kg + closing_error * point.fraction_of_day,
                point.label,
            )
            for point in raw_points
        )
        # Avoid tiny floating drift in the two mandated anchors.
        return (
            TrajectoryPoint(0.0, open_kg, adjusted[0].label),
            *adjusted[1:-1],
            TrajectoryPoint(1.0, target_close_kg, adjusted[-1].label),
        )
