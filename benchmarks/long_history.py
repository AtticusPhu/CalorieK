"""CAL-16 reproducible local profiling; no user database path is accepted.

Run explicitly: python -m benchmarks.long_history --years 1 3 10
This module never creates QApplication, renders widgets, or packages the app.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
import platform
import sqlite3
import tempfile
from time import perf_counter
from typing import Iterator
from unittest.mock import patch

from app.application import ApplicationContext
from app.db.database import Database
from app.models.ohlc import OHLCInput
from app.services.food_service import FoodService
from app.services.profile_service import ProfileService
from app.services.exercise_service import ExerciseService
from app.services.recipe_service import RecipeService
from app.version import APP_VERSION, CALCULATION_VERSION, SCHEMA_VERSION


PRESETS = {1: 365, 3: 1095, 10: 3650}
END_DAY = date(2025, 12, 31)


@dataclass(frozen=True)
class HistorySpec:
    days: int
    end: date = END_DAY

    def __post_init__(self):
        if not 1 <= self.days <= PRESETS[10]:
            raise ValueError("history must contain 1..3650 days")

    @classmethod
    def for_years(cls, years: int) -> HistorySpec:
        return cls(PRESETS[years])

    @property
    def start(self) -> date:
        return self.end - timedelta(days=self.days - 1)

    @property
    def weight_count(self) -> int:
        return 1 + (self.days - 1) // 7

    def dates(self) -> Iterator[date]:
        for offset in range(self.days):
            yield self.start + timedelta(days=offset)


def populate_history(database: Database, spec: HistorySpec) -> dict[str, int]:
    """Populate an empty synthetic DB; never clear/overwrite an existing profile.

    Catalogue/profile use production services. Large fact sets use one batch
    transaction with fixed, explicit schema-v3 snapshots, so setup overhead is
    separate from the real production calculation paths being measured.
    """
    ProfileService(database).create_profile(
        gender="male", birth_date="1980-01-01", height_cm=175,
        current_weight_kg=70, effective_from=spec.start,
        measured_at=datetime.combine(spec.start, time(7)),
    )
    food = FoodService(database).create_food(
        name="CAL16 synthetic food", category="benchmark", basis_unit="g",
        basis_amount=100, kj=2400.125, protein_g_per_100g=20,
        fiber_g_per_100g=4, fat_g_per_100g=10, carbs_g_per_100g=60,
    )
    recipe = RecipeService(database).create_recipe(
        name="CAL16 synthetic recipe", items=[(food, 100, "g")],
    )
    exercise = ExerciseService(database).create_exercise_type(
        name="CAL16 synthetic walk", default_duration_min=30, default_active_kj=600.25,
    )
    stamp = "2025-12-31T23:59:59+00:00"  # audit metadata only, not event clocks
    with database.transaction() as connection:
        for offset, day in enumerate(spec.dates()):
            if offset and offset % 7 == 0:
                connection.execute(
                    "INSERT INTO weight_measurements(occurred_at, local_date, weight_kg, anchor, "
                    "created_at, updated_at, active) VALUES (?, ?, ?, 'CLOSE', ?, ?, 1)",
                    (f"{day}T20:00:00", str(day), 70 + ((offset // 7) % 9 - 4) * 0.05, stamp, stamp),
                )
            for hour, source, source_id, meal in (
                (8, "FOOD", food, "BREAKFAST"),
                (12, "RECIPE", recipe, "LUNCH"),
                (18, "CUSTOM", None, "DINNER"),
            ):
                # Energy varies deterministically; nutrition is independent and
                # fully explicit, exercising history reads without legacy repair.
                connection.execute(
                    "INSERT INTO intake_events(occurred_at, local_date, source_type, source_id, "
                    "meal_type, name_snapshot, amount, unit, kj_snapshot, protein_snapshot, "
                    "fiber_snapshot, fat_snapshot, carb_snapshot, protein_g, fiber_g, fat_g, "
                    "carbs_g, nutrition_complete, created_at, updated_at, active) "
                    "VALUES (?, ?, ?, ?, ?, 'synthetic meal', 100, 'g', ?, "
                    "20, 4, 10, 60, 20, 4, 10, 60, 1, ?, ?, 1)",
                    (f"{day}T{hour:02}:00:00", str(day), source, source_id, meal,
                     2400.125 + (offset % 5) * 25, stamp, stamp),
                )
            connection.execute(
                "INSERT INTO exercise_events(occurred_at, local_date, exercise_type_id, "
                "name_snapshot, duration_min, active_kj, created_at, updated_at, active) "
                "VALUES (?, ?, ?, 'synthetic walk', 30, ?, ?, ?, 1)",
                (f"{day}T17:00:00", str(day), exercise, 600.25 + (offset % 3) * 10, stamp, stamp),
            )
        database.mark_dirty(spec.start, connection=connection)
    return {
        "days": spec.days, "weights": spec.weight_count,
        "intakes": spec.days * 3, "exercises": spec.days,
        "foods": 1, "recipes": 1,
    }


@contextmanager
def synthetic_history(spec: HistorySpec):
    """Only creates a brand-new OS temp directory; no live/default path lookup."""
    with tempfile.TemporaryDirectory(prefix="caloriek-cal16-synthetic-") as folder:
        database = Database(Path(folder) / "caloriek.sqlite3")
        try:
            database.initialize(seed_foods=False)
            counts = populate_history(database, spec)
            yield database, counts
        finally:
            database.close()


@contextmanager
def count_queries():
    """Benchmark-only instrumentation, excluding connection setup PRAGMAs."""
    counts = {"statements": 0, "selects": 0}
    connect = Database.connect

    def traced(database):
        connection = connect(database)

        def count(sql):
            counts["statements"] += 1
            if sql.lstrip().upper().startswith(("SELECT", "WITH")):
                counts["selects"] += 1

        # Connection setup PRAGMAs precede this hook and are excluded.
        connection.set_trace_callback(count)
        return connection

    with patch.object(Database, "connect", traced):
        yield counts


def benchmark(years: int) -> dict:
    spec = HistorySpec.for_years(years)
    report = {
        "years": years, "start": str(spec.start), "end": str(spec.end),
        "python": platform.python_version(), "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version, "app_version": APP_VERSION,
        "schema_version": SCHEMA_VERSION, "calculation_version": CALCULATION_VERSION,
        "phases": [],
    }

    def measure(name, action):
        with count_queries() as queries:
            started = perf_counter()
            items = action()
            elapsed = perf_counter() - started
        phase = {"years": years, "phase": name, "seconds": elapsed, "items": items, **queries}
        report["phases"].append(phase)
        print(json.dumps(phase, ensure_ascii=False), flush=True)

    setup_started = perf_counter()
    with synthetic_history(spec) as (database, counts):
        report["counts"] = counts
        report["setup_seconds"] = perf_counter() - setup_started
        context = ApplicationContext(database.path.parent)
        try:
            daily = context.daily
            # Initial cold calculation separated from explicit full cache rebuild.
            measure("initial_rebuild", lambda: daily.recalculate(spec.start, spec.end))
            dirty_day = spec.end - timedelta(days=89)
            database.mark_dirty(dirty_day)
            measure("dirty_last_90_days", lambda: daily.recalculate(dirty_day, spec.end))
            measure("full_cache_rebuild", lambda: daily.rebuild_all(spec.end))

            def rolling():
                # Historical range work must share one bounded raw-fact context;
                # repeatedly calling the single-day API would intentionally throw
                # away the production recalculation optimization being profiled.
                first_actual = daily._first_actual_date()
                if first_actual is None:
                    return 0
                calculation = daily._build_calculation_context(
                    first_actual, spec.end, first_actual=first_actual
                )
                for day in spec.dates():
                    daily._fit_calibration_from_context(day, calculation)
                return spec.days

            measure("rolling_calibration_all_days", rolling)

            def ohlc():
                previous = None
                rows = daily.list_metrics(start_day=spec.start, end_day=spec.end)
                first_actual = daily._first_actual_date()
                if first_actual is None:
                    return 0
                calculation = daily._build_calculation_context(
                    first_actual, spec.end, first_actual=first_actual
                )
                for row in rows:
                    day = date.fromisoformat(row["date"])
                    facts = calculation.facts(day)
                    candle = daily.ohlc.generate_day(OHLCInput(
                        day=day, previous_close_kg=previous,
                        measurements=facts.measurements, events=facts.events,
                        baseline_kj=facts.baseline_kj, calibration_kj_day=row["calibration_kj"],
                    ))
                    previous = candle.close_kg
                return len(rows)

            measure("ohlc_preparation_all_days", ohlc)
            measure("chart_rows_all_days", lambda: len(daily.list_metrics(start_day=spec.start, end_day=spec.end)))
            measure("dashboard_last_90_days", lambda: len(context.get_dashboard(spec.end).candles))
            # Model projection also measured at a fixed clock, independent of now.
            measure("projection_at_fixed_end", lambda: int(daily.calculate_today_projection(
                datetime.combine(spec.end, time(21))
            ) is not None))
        finally:
            context.database.close()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", choices=tuple(PRESETS), default=[1, 3, 10])
    arguments = parser.parse_args(argv)
    for years in arguments.years:
        print(json.dumps({"benchmark": benchmark(years)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
