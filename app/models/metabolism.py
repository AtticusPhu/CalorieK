"""Resting metabolism and clock-aware baseline energy expenditure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from math import isfinite
from typing import Protocol, runtime_checkable

from app.energy_units import kcal_to_kj


class Sex(str, Enum):
    """Biological sex values supported by the Mifflin-St Jeor equation."""

    MALE = "male"
    FEMALE = "female"


@dataclass(frozen=True, slots=True)
class BaselineBurnResult:
    """Integrated baseline burn over a requested datetime interval."""

    total_kj: float
    awake_kj: float
    sleep_kj: float
    awake_hours: float
    sleep_hours: float


@runtime_checkable
class MetabolismModel(Protocol):
    """Replaceable interface for resting and baseline expenditure models."""

    def calculate_rmr(
        self,
        *,
        sex: Sex | str,
        weight_kg: float,
        height_cm: float,
        age_years: float,
    ) -> float:
        """Return resting metabolic rate in kJ/day."""

    def calculate_baseline_burn(
        self,
        *,
        rmr_kj_day: float,
        start_at: datetime,
        end_at: datetime,
        wake_time: time,
        sleep_time: time,
        awake_multiplier: float = 1.20,
        sleep_multiplier: float = 0.95,
    ) -> BaselineBurnResult:
        """Integrate clock-dependent baseline burn over ``[start, end)``."""


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _positive(name: str, value: float) -> float:
    number = _finite(name, value)
    if number <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return number


def calculate_age(birth_date: date, on_date: date) -> int:
    """Calculate completed years without persisting a stale age value."""

    if birth_date > on_date:
        raise ValueError("birth_date cannot be after on_date")
    birthday_passed = (on_date.month, on_date.day) >= (
        birth_date.month,
        birth_date.day,
    )
    return on_date.year - birth_date.year - (0 if birthday_passed else 1)


def _normalise_sex(value: Sex | str) -> Sex:
    if isinstance(value, Sex):
        return value
    normalised = str(value).strip().lower()
    aliases = {
        "m": Sex.MALE,
        "male": Sex.MALE,
        "man": Sex.MALE,
        "男": Sex.MALE,
        "f": Sex.FEMALE,
        "female": Sex.FEMALE,
        "woman": Sex.FEMALE,
        "女": Sex.FEMALE,
    }
    try:
        return aliases[normalised]
    except KeyError as exc:
        raise ValueError(f"unsupported sex value: {value!r}") from exc


def _is_awake(value: time, wake_time: time, sleep_time: time) -> bool:
    current = value.replace(tzinfo=None)
    wake = wake_time.replace(tzinfo=None)
    sleep = sleep_time.replace(tzinfo=None)
    if wake == sleep:
        raise ValueError("wake_time and sleep_time must differ")
    if wake < sleep:
        return wake <= current < sleep
    # Night-shift schedule: the awake interval itself crosses midnight.
    return current >= wake or current < sleep


@dataclass(frozen=True, slots=True)
class MifflinStJeorModel:
    """Mifflin-St Jeor RMR plus awake/sleep multiplier integration."""

    def calculate_rmr(
        self,
        *,
        sex: Sex | str,
        weight_kg: float,
        height_cm: float,
        age_years: float,
    ) -> float:
        weight = _positive("weight_kg", weight_kg)
        height = _positive("height_cm", height_cm)
        age = _finite("age_years", age_years)
        if age < 0:
            raise ValueError("age_years cannot be negative")
        constant = 5.0 if _normalise_sex(sex) is Sex.MALE else -161.0
        # The published equation returns kcal/day; cross the unit boundary here.
        return kcal_to_kj(10.0 * weight + 6.25 * height - 5.0 * age + constant)

    def calculate_baseline_burn(
        self,
        *,
        rmr_kj_day: float,
        start_at: datetime,
        end_at: datetime,
        wake_time: time,
        sleep_time: time,
        awake_multiplier: float = 1.20,
        sleep_multiplier: float = 0.95,
    ) -> BaselineBurnResult:
        rmr = _positive("rmr_kj_day", rmr_kj_day)
        awake_factor = _positive("awake_multiplier", awake_multiplier)
        sleep_factor = _positive("sleep_multiplier", sleep_multiplier)
        if wake_time.replace(tzinfo=None) == sleep_time.replace(tzinfo=None):
            raise ValueError("wake_time and sleep_time must differ")
        try:
            if end_at < start_at:
                raise ValueError("end_at cannot be before start_at")
        except TypeError as exc:
            raise ValueError("start_at and end_at must use compatible timezones") from exc
        if end_at == start_at:
            return BaselineBurnResult(0.0, 0.0, 0.0, 0.0, 0.0)

        boundaries: set[datetime] = {start_at, end_at}
        current_day = start_at.date()
        last_day = end_at.date()
        while current_day <= last_day:
            for clock_value in (wake_time, sleep_time):
                boundary = datetime.combine(
                    current_day,
                    clock_value.replace(tzinfo=None),
                    tzinfo=start_at.tzinfo,
                )
                if start_at < boundary < end_at:
                    boundaries.add(boundary)
            current_day += timedelta(days=1)

        ordered = sorted(boundaries)
        awake_hours = 0.0
        sleep_hours = 0.0
        for interval_start, interval_end in zip(ordered, ordered[1:]):
            midpoint = interval_start + (interval_end - interval_start) / 2
            hours = (interval_end - interval_start).total_seconds() / 3600.0
            if _is_awake(midpoint.timetz(), wake_time, sleep_time):
                awake_hours += hours
            else:
                sleep_hours += hours

        hourly_rmr = rmr / 24.0
        awake_kj = hourly_rmr * awake_factor * awake_hours
        sleep_kj = hourly_rmr * sleep_factor * sleep_hours
        return BaselineBurnResult(
            total_kj=awake_kj + sleep_kj,
            awake_kj=awake_kj,
            sleep_kj=sleep_kj,
            awake_hours=awake_hours,
            sleep_hours=sleep_hours,
        )

    def calculate_day_baseline(
        self,
        *,
        local_date: date,
        rmr_kj_day: float,
        wake_time: time,
        sleep_time: time,
        awake_multiplier: float = 1.20,
        sleep_multiplier: float = 0.95,
    ) -> BaselineBurnResult:
        """Convenience wrapper for one natural day, midnight to midnight."""

        start_at = datetime.combine(local_date, time.min)
        return self.calculate_baseline_burn(
            rmr_kj_day=rmr_kj_day,
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            wake_time=wake_time,
            sleep_time=sleep_time,
            awake_multiplier=awake_multiplier,
            sleep_multiplier=sleep_multiplier,
        )
