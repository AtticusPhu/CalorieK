"""Database-backed calculation orchestration and deterministic cache rebuilds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from app.db.database import Database, normalize_date, now_iso
from app.models.calibration import (
    CalibrationDay,
    CalibrationResult,
    GridSearchCalibrationEngine,
)
from app.models.daily_projection import (
    DailyProjectionInput,
    DailyProjectionResult,
    DefaultDailyProjectionService,
)
from app.models.metabolism import MifflinStJeorModel, calculate_age
from app.models.ohlc import (
    EnergyEvent,
    EnergyEventType,
    MissingWeightAnchorError,
    OHLCGenerator,
    OHLCInput,
    WeightMeasurement,
)
from app.models.weight_model import SimpleEnergyWeightModel
from app.services.profile_service import ProfileService
from app.version import CALCULATION_VERSION


def parse_local_datetime(value: str | datetime) -> datetime:
    """Interpret stored timestamps by their recorded local wall-clock value."""

    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=None)


def iter_days(start: date, end: date) -> Iterable[date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


@dataclass(frozen=True, slots=True)
class DailyFacts:
    day: date
    intake_kcal: float
    exercise_kcal: float
    baseline_kcal: float
    measurements: tuple[WeightMeasurement, ...]
    events: tuple[EnergyEvent, ...]


class DailyMetricsService:
    """Keep all derived values reproducible from immutable raw facts.

    Calibration δ is positive-as-extra-expenditure.  A separate fit is computed
    for each candle's historical window, which makes a full cache rebuild match
    an incremental ``dirty_from_date`` rebuild: edits can never change dates that
    precede the edited fact.
    """

    def __init__(self, database: Database) -> None:
        self.db = database
        self.profile_service = ProfileService(database)
        self.metabolism = MifflinStJeorModel()
        kcal_per_kg = self._setting_float("kcal_per_kg", 7700.0)
        self.weight_model = SimpleEnergyWeightModel(kcal_per_kg=kcal_per_kg)
        self.ohlc = OHLCGenerator(weight_model=self.weight_model)
        self.calibration = GridSearchCalibrationEngine(weight_model=self.weight_model)
        self.projection = DefaultDailyProjectionService(weight_model=self.weight_model)

    def refresh_model_settings(self) -> None:
        kcal_per_kg = self._setting_float("kcal_per_kg", 7700.0)
        self.weight_model = SimpleEnergyWeightModel(kcal_per_kg=kcal_per_kg)
        self.ohlc = OHLCGenerator(weight_model=self.weight_model)
        self.calibration = GridSearchCalibrationEngine(weight_model=self.weight_model)
        self.projection = DefaultDailyProjectionService(weight_model=self.weight_model)

    def _setting_float(self, key: str, default: float) -> float:
        raw = self.db.get_setting(key)
        if raw is None:
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        return value if value > 0 else default

    def _first_actual_date(self) -> date | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT MIN(local_date) FROM weight_measurements WHERE active = 1"
            ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def _latest_actual_row(
        self, *, on_or_before: date | None = None, as_of: datetime | None = None
    ) -> dict[str, Any] | None:
        clauses = ["active = 1"]
        parameters: list[Any] = []
        if on_or_before is not None:
            clauses.append("local_date <= ?")
            parameters.append(on_or_before.isoformat())
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM weight_measurements WHERE "
                + " AND ".join(clauses)
                + " ORDER BY local_date DESC, occurred_at DESC, id DESC",
                parameters,
            ).fetchall()
        for row in rows:
            item = dict(row)
            if as_of is None or parse_local_datetime(item["occurred_at"]) <= as_of:
                return item
        return None

    def _revision(self, day: date) -> dict[str, Any] | None:
        return self.profile_service.get_revision_for_date(day)

    def _rmr(self, day: date, weight_kg: float) -> float:
        revision = self._revision(day)
        if revision is None:
            raise LookupError(f"{day.isoformat()} 没有可用的用户资料版本")
        return self.metabolism.calculate_rmr(
            sex=revision["gender"],
            weight_kg=weight_kg,
            height_cm=float(revision["height_cm"]),
            age_years=calculate_age(
                date.fromisoformat(revision["birth_date"]), day
            ),
        )

    def _baseline_interval(
        self, start_at: datetime, end_at: datetime, weight_kg: float
    ) -> float:
        if end_at <= start_at:
            return 0.0
        total = 0.0
        cursor = start_at
        while cursor < end_at:
            boundary = min(
                end_at, datetime.combine(cursor.date() + timedelta(days=1), time.min)
            )
            revision = self._revision(cursor.date())
            if revision is None:
                raise LookupError(
                    f"{cursor.date().isoformat()} 没有可用的用户资料版本"
                )
            result = self.metabolism.calculate_baseline_burn(
                rmr_kcal_day=self._rmr(cursor.date(), weight_kg),
                start_at=cursor,
                end_at=boundary,
                wake_time=time.fromisoformat(revision["wake_time"]),
                sleep_time=time.fromisoformat(revision["sleep_time"]),
                awake_multiplier=float(revision["awake_multiplier"]),
                sleep_multiplier=float(revision["sleep_multiplier"]),
            )
            total += result.total_kcal
            cursor = boundary
        return total

    def _day_baseline(self, day: date, reference_weight_kg: float) -> float:
        start = datetime.combine(day, time.min)
        return self._baseline_interval(
            start, start + timedelta(days=1), reference_weight_kg
        )

    def _daily_facts(self, day: date) -> DailyFacts:
        reference = self._latest_actual_row(on_or_before=day)
        if reference is None:
            raise MissingWeightAnchorError("no actual weight exists on or before day")
        with self.db.connection() as connection:
            intake_rows = connection.execute(
                "SELECT * FROM intake_events WHERE local_date = ? AND active = 1 "
                "ORDER BY occurred_at, id",
                (day.isoformat(),),
            ).fetchall()
            exercise_rows = connection.execute(
                "SELECT * FROM exercise_events WHERE local_date = ? AND active = 1 "
                "ORDER BY occurred_at, id",
                (day.isoformat(),),
            ).fetchall()
            weight_rows = connection.execute(
                "SELECT * FROM weight_measurements WHERE local_date = ? AND active = 1 "
                "ORDER BY occurred_at, id",
                (day.isoformat(),),
            ).fetchall()

        intake_kcal = sum(float(row["kcal_snapshot"]) for row in intake_rows)
        exercise_kcal = sum(float(row["active_kcal"]) for row in exercise_rows)
        events = tuple(
            [
                EnergyEvent(
                    occurred_at=parse_local_datetime(row["occurred_at"]),
                    kcal=float(row["kcal_snapshot"]),
                    event_type=EnergyEventType.INTAKE,
                    name=str(row["name_snapshot"]),
                )
                for row in intake_rows
            ]
            + [
                EnergyEvent(
                    occurred_at=parse_local_datetime(row["occurred_at"]),
                    kcal=float(row["active_kcal"]),
                    event_type=EnergyEventType.EXERCISE,
                    name=str(row["name_snapshot"]),
                )
                for row in exercise_rows
            ]
        )
        measurements = tuple(
            WeightMeasurement(
                occurred_at=parse_local_datetime(row["occurred_at"]),
                weight_kg=float(row["weight_kg"]),
                anchor=str(row["anchor"]),
                note=str(row["note"] or ""),
            )
            for row in weight_rows
        )
        return DailyFacts(
            day=day,
            intake_kcal=intake_kcal,
            exercise_kcal=exercise_kcal,
            baseline_kcal=self._day_baseline(day, float(reference["weight_kg"])),
            measurements=measurements,
            events=events,
        )

    def fit_calibration(
        self, end_day: date, *, persist: bool = False
    ) -> CalibrationResult:
        first_actual = self._first_actual_date()
        if first_actual is None:
            return self.calibration.fit([], end_date=end_day)
        start = max(first_actual, end_day - timedelta(days=29))
        calibration_days: list[CalibrationDay] = []
        with self.db.connection() as connection:
            weight_by_date = {
                row["local_date"]: float(row["weight_kg"])
                for row in connection.execute(
                    "SELECT w.local_date, w.weight_kg FROM weight_measurements w "
                    "JOIN (SELECT local_date, MAX(occurred_at) AS occurred_at "
                    "      FROM weight_measurements WHERE active = 1 "
                    "      GROUP BY local_date) last "
                    "ON last.local_date = w.local_date AND last.occurred_at = w.occurred_at "
                    "WHERE w.active = 1 AND w.local_date BETWEEN ? AND ? "
                    "ORDER BY w.local_date, w.id",
                    (start.isoformat(), end_day.isoformat()),
                ).fetchall()
            }
        for day_value in iter_days(start, end_day):
            facts = self._daily_facts(day_value)
            calibration_days.append(
                CalibrationDay(
                    day=day_value,
                    intake_kcal=facts.intake_kcal,
                    baseline_kcal=facts.baseline_kcal,
                    exercise_kcal=facts.exercise_kcal,
                    actual_weight_kg=weight_by_date.get(day_value.isoformat()),
                )
            )
        result = self.calibration.fit(
            calibration_days, start_date=start, end_date=end_day
        )
        if persist:
            with self.db.transaction() as connection:
                connection.execute(
                    "DELETE FROM calibration_runs WHERE window_end = ?",
                    (end_day.isoformat(),),
                )
                connection.execute(
                    """
                    INSERT INTO calibration_runs(
                        window_start, window_end, weight_sample_count, days_span,
                        calibration_kcal_day, rmse, model_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.window_start.isoformat()
                        if result.window_start is not None
                        else end_day.isoformat(),
                        end_day.isoformat(),
                        result.weight_sample_count,
                        result.days_span,
                        result.calibration_kcal_day,
                        result.rmse_kg,
                        result.model_version,
                        now_iso(),
                    ),
                )
        return result

    def recalculate(
        self, start_day: date | str | None = None, end_day: date | str | None = None
    ) -> int:
        """Rebuild cache rows in chronological order and return row count."""

        first_actual = self._first_actual_date()
        if first_actual is None:
            # No candle can be derived without an actual weight anchor.  This is
            # especially important after deleting the final measurement: keeping
            # old cache rows would display facts which are no longer reproducible.
            with self.db.transaction() as connection:
                connection.execute("DELETE FROM daily_metrics_cache")
                connection.execute("DELETE FROM calibration_runs")
                connection.execute(
                    "UPDATE recalculation_state SET dirty_from_date = NULL, updated_at = ? "
                    "WHERE id = 1",
                    (now_iso(),),
                )
            return 0
        end = (
            date.fromisoformat(normalize_date(end_day)) if end_day else date.today()
        )
        requested_start = (
            date.fromisoformat(normalize_date(start_day))
            if start_day is not None
            else first_actual
        )

        # If the former earliest anchor was removed or moved, cached rows before
        # the new anchor can never be rebuilt.  Remove them and advance the dirty
        # marker so repeated ensure_calculated() calls converge.
        dirty_before = self.db.get_dirty_from_date()
        if requested_start < first_actual:
            with self.db.transaction() as connection:
                connection.execute(
                    "DELETE FROM daily_metrics_cache WHERE date < ?",
                    (first_actual.isoformat(),),
                )
                connection.execute(
                    "DELETE FROM calibration_runs WHERE window_end < ?",
                    (first_actual.isoformat(),),
                )
                next_dirty = connection.execute(
                    "SELECT MIN(date) FROM daily_metrics_cache WHERE is_dirty = 1"
                ).fetchone()[0]
                if next_dirty is None and dirty_before is not None:
                    next_dirty = first_actual.isoformat()
                connection.execute(
                    "UPDATE recalculation_state SET dirty_from_date = ?, updated_at = ? "
                    "WHERE id = 1",
                    (next_dirty, now_iso()),
                )
        start = max(first_actual, requested_start)
        if start > end:
            return 0

        with self.db.connection() as connection:
            previous_row = connection.execute(
                "SELECT close_kg FROM daily_metrics_cache "
                "WHERE date < ? AND is_dirty = 0 AND calculation_version = ? "
                "ORDER BY date DESC LIMIT 1",
                (start.isoformat(), CALCULATION_VERSION),
            ).fetchone()
        if start > first_actual and previous_row is None:
            start = first_actual
        previous_close = float(previous_row[0]) if previous_row is not None else None

        rows: list[dict[str, Any]] = []
        calibration_results: list[CalibrationResult] = []
        for current_day in iter_days(start, end):
            facts = self._daily_facts(current_day)
            calibration = self.fit_calibration(current_day)
            calibration_results.append(calibration)
            result = self.ohlc.generate_day(
                OHLCInput(
                    day=current_day,
                    previous_close_kg=previous_close,
                    measurements=facts.measurements,
                    events=facts.events,
                    baseline_kcal=facts.baseline_kcal,
                    calibration_kcal_day=calibration.calibration_kcal_day,
                )
            )
            total_burn = facts.baseline_kcal + facts.exercise_kcal
            rows.append(
                {
                    "date": current_day.isoformat(),
                    "open_kg": result.open_kg,
                    "high_kg": result.high_kg,
                    "low_kg": result.low_kg,
                    "close_kg": result.close_kg,
                    "open_source": result.open_source.value,
                    "close_source": result.close_source.value,
                    "actual_weight_count": result.actual_weight_count,
                    "intake_kcal": facts.intake_kcal,
                    "baseline_kcal": facts.baseline_kcal,
                    "exercise_kcal": facts.exercise_kcal,
                    "total_burn_kcal": total_burn,
                    "balance_kcal": facts.intake_kcal - total_burn,
                    "predicted_close_kg": result.predicted_close_kg,
                    "calibration_kcal": calibration.calibration_kcal_day,
                }
            )
            previous_close = result.close_kg

        timestamp = now_iso()
        with self.db.transaction() as connection:
            for calibration in calibration_results:
                window_end = calibration.window_end or end
                connection.execute(
                    "DELETE FROM calibration_runs WHERE window_end = ?",
                    (window_end.isoformat(),),
                )
                connection.execute(
                    """
                    INSERT INTO calibration_runs(
                        window_start, window_end, weight_sample_count, days_span,
                        calibration_kcal_day, rmse, model_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (calibration.window_start or window_end).isoformat(),
                        window_end.isoformat(),
                        calibration.weight_sample_count,
                        calibration.days_span,
                        calibration.calibration_kcal_day,
                        calibration.rmse_kg,
                        calibration.model_version,
                        timestamp,
                    ),
                )
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO daily_metrics_cache(
                        date, open_kg, high_kg, low_kg, close_kg,
                        open_source, close_source, actual_weight_count,
                        intake_kcal, baseline_kcal, exercise_kcal, total_burn_kcal,
                        balance_kcal, predicted_close_kg, calibration_kcal,
                        calculation_version, calculated_at, is_dirty
                    ) VALUES (
                        :date, :open_kg, :high_kg, :low_kg, :close_kg,
                        :open_source, :close_source, :actual_weight_count,
                        :intake_kcal, :baseline_kcal, :exercise_kcal, :total_burn_kcal,
                        :balance_kcal, :predicted_close_kg, :calibration_kcal,
                        :calculation_version, :calculated_at, 0
                    )
                    ON CONFLICT(date) DO UPDATE SET
                        open_kg=excluded.open_kg, high_kg=excluded.high_kg,
                        low_kg=excluded.low_kg, close_kg=excluded.close_kg,
                        open_source=excluded.open_source,
                        close_source=excluded.close_source,
                        actual_weight_count=excluded.actual_weight_count,
                        intake_kcal=excluded.intake_kcal,
                        baseline_kcal=excluded.baseline_kcal,
                        exercise_kcal=excluded.exercise_kcal,
                        total_burn_kcal=excluded.total_burn_kcal,
                        balance_kcal=excluded.balance_kcal,
                        predicted_close_kg=excluded.predicted_close_kg,
                        calibration_kcal=excluded.calibration_kcal,
                        calculation_version=excluded.calculation_version,
                        calculated_at=excluded.calculated_at, is_dirty=0
                    """,
                    {
                        **row,
                        "calculation_version": CALCULATION_VERSION,
                        "calculated_at": timestamp,
                    },
                )
        dirty = self.db.get_dirty_from_date()
        if dirty is not None and date.fromisoformat(dirty) <= end:
            # A caller may intentionally rebuild only part of a dirty range.
            # Never clear later stale rows merely because the first repaired day
            # succeeded; advance the global marker to the earliest row still dirty.
            with self.db.transaction() as connection:
                next_dirty = connection.execute(
                    "SELECT MIN(date) FROM daily_metrics_cache WHERE is_dirty = 1"
                ).fetchone()[0]
                connection.execute(
                    "UPDATE recalculation_state SET dirty_from_date = ?, updated_at = ? "
                    "WHERE id = 1",
                    (next_dirty, now_iso()),
                )
        return len(rows)

    def rebuild_all(self, end_day: date | str | None = None) -> int:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM daily_metrics_cache")
            connection.execute("DELETE FROM calibration_runs")
        return self.recalculate(self._first_actual_date(), end_day)

    def ensure_calculated(self, through_day: date | None = None) -> None:
        if not self.profile_service.is_initialized():
            return
        target = through_day or date.today()
        dirty = self.db.get_dirty_from_date()
        if dirty is not None and date.fromisoformat(dirty) <= target:
            self.recalculate(dirty, target)
            return
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT date, calculation_version, is_dirty FROM daily_metrics_cache "
                "WHERE date <= ? ORDER BY date DESC LIMIT 1",
                (target.isoformat(),),
            ).fetchone()
        if row is None:
            self.recalculate(self._first_actual_date(), target)
        elif row["calculation_version"] != CALCULATION_VERSION:
            self.rebuild_all(target)
        elif row["date"] < target.isoformat():
            self.recalculate(date.fromisoformat(row["date"]) + timedelta(days=1), target)
        elif int(row["is_dirty"]):
            self.recalculate(row["date"], target)

    def list_metrics(
        self, *, start_day: date | None = None, end_day: date | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["is_dirty = 0", "calculation_version = ?"]
        parameters: list[Any] = [CALCULATION_VERSION]
        if start_day is not None:
            clauses.append("date >= ?")
            parameters.append(start_day.isoformat())
        if end_day is not None:
            clauses.append("date <= ?")
            parameters.append(end_day.isoformat())
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM daily_metrics_cache WHERE "
                + " AND ".join(clauses)
                + " ORDER BY date",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def calculate_today_projection(
        self, as_of: datetime | None = None
    ) -> DailyProjectionResult | None:
        current = (as_of or datetime.now()).replace(tzinfo=None)
        self.ensure_calculated(current.date())
        actual = self._latest_actual_row(as_of=current)
        if actual is None:
            return None
        anchor_at = parse_local_datetime(actual["occurred_at"])
        with self.db.connection() as connection:
            intake_rows = connection.execute(
                "SELECT * FROM intake_events WHERE active = 1 "
                "AND local_date BETWEEN ? AND ? ORDER BY occurred_at, id",
                (anchor_at.date().isoformat(), current.date().isoformat()),
            ).fetchall()
            exercise_rows = connection.execute(
                "SELECT * FROM exercise_events WHERE active = 1 "
                "AND local_date BETWEEN ? AND ? ORDER BY occurred_at, id",
                (anchor_at.date().isoformat(), current.date().isoformat()),
            ).fetchall()
            cache = connection.execute(
                "SELECT calibration_kcal FROM daily_metrics_cache WHERE date = ?",
                (current.date().isoformat(),),
            ).fetchone()

        events: list[EnergyEvent] = []
        for row in intake_rows:
            occurred = parse_local_datetime(row["occurred_at"])
            if occurred <= current:
                events.append(
                    EnergyEvent(
                        occurred_at=occurred,
                        kcal=float(row["kcal_snapshot"]),
                        event_type=EnergyEventType.INTAKE,
                        name=str(row["name_snapshot"]),
                    )
                )
        for row in exercise_rows:
            occurred = parse_local_datetime(row["occurred_at"])
            if occurred <= current:
                events.append(
                    EnergyEvent(
                        occurred_at=occurred,
                        kcal=float(row["active_kcal"]),
                        event_type=EnergyEventType.EXERCISE,
                        name=str(row["name_snapshot"]),
                    )
                )
        weight = float(actual["weight_kg"])
        midnight = datetime.combine(current.date(), time.min)
        next_midnight = midnight + timedelta(days=1)
        calibration_kcal = float(cache[0]) if cache is not None else 0.0
        return self.projection.calculate_today_projection(
            DailyProjectionInput(
                day=current.date(),
                as_of=current,
                latest_actual_weight_kg=weight,
                latest_actual_at=anchor_at,
                events=tuple(events),
                baseline_burn_since_anchor_kcal=self._baseline_interval(
                    anchor_at, current, weight
                ),
                remaining_baseline_burn_kcal=self._baseline_interval(
                    current, next_midnight, weight
                ),
                today_baseline_burn_elapsed_kcal=self._baseline_interval(
                    midnight, current, weight
                ),
                calibration_kcal_day=calibration_kcal,
                calibration_days_from_anchor_to_end=(
                    next_midnight - anchor_at
                ).total_seconds()
                / 86400.0,
            )
        )


__all__ = [
    "DailyFacts",
    "DailyMetricsService",
    "iter_days",
    "parse_local_datetime",
]
