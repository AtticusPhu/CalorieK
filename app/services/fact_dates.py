"""Recorded-local dates and weight coverage for changes to actual facts."""

from datetime import date, timedelta
import sqlite3

from app.db.database import Database, now_iso


# The dashboard looks back 89 days. A full-day baseline spans the following
# midnight and its interval loop must be able to advance past that date.
FIRST_CALCULABLE_DAY = (date.min + timedelta(days=89)).isoformat()
LAST_CALCULABLE_DAY = (date.max - timedelta(days=2)).isoformat()


def validate_actual_day(day: str, previous_day: str | None = None) -> None:
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError(f"无效的记录日期 {day}。记录未修改。")
    if not FIRST_CALCULABLE_DAY <= day <= LAST_CALCULABLE_DAY:
        raise ValueError(f"{day} 超出每日计算范围。请选择可计算的日期。记录未修改。")
    if day != previous_day and day > date.today().isoformat():
        raise ValueError(f"{day} 是未来日期。实际记录请选择今天或更早的日期。记录未修改。")


def _check_dependents(connection: sqlite3.Connection, since: str, first_anchor: str | None) -> None:
    """An active meal/activity on the new first anchor's day is covered."""
    if first_anchor is not None and first_anchor <= since:
        return
    earliest = connection.execute(
        "SELECT MIN(day) FROM ("
        "SELECT MIN(local_date) AS day FROM intake_events WHERE active = 1 AND local_date >= ? "
        "UNION ALL SELECT MIN(local_date) FROM exercise_events WHERE active = 1 AND local_date >= ?)",
        (since, since),
    ).fetchone()[0]
    if earliest is not None and (first_anchor is None or earliest < first_anchor):
        raise ValueError(f"{earliest} 有饮食或运动记录需要实际称重锚点。请先保留或补充该日或更早的称重。记录未修改。")


def validate_initial_anchor(connection: sqlite3.Connection, day: str, revision: str) -> None:
    """Check the proposed profile/weight pair before either one is inserted."""
    validate_actual_day(day)
    existing = connection.execute("SELECT MIN(local_date) FROM weight_measurements WHERE active = 1").fetchone()[0]
    first = min(existing, day) if existing is not None else day
    if first < revision:
        raise ValueError(f"首个称重日 {first} 早于资料生效日 {revision}。记录未修改。")
    _check_dependents(connection, date.min.isoformat(), first)


def validate_fact_change(
    connection: sqlite3.Connection, day: str, *, previous_day: str | None = None,
    weight: bool = False, active_weight_id: int | None = None, deleting: bool = False,
) -> None:
    """Check the target day, or the stored day on an explicit delete/restore.

    Pass active_weight_id only when moving or removing an existing anchor.
    A restored row keeps its original day but contributes a newly active fact.
    """
    if previous_day is not None and previous_day != day:
        # Both days participate in the chronological minimum passed to mark_dirty().
        if date.fromisoformat(previous_day).isoformat() != previous_day:
            raise ValueError(f"无效的原记录日期 {previous_day}。记录未修改。")
    validate_actual_day(day, previous_day)
    revision = connection.execute(
        "SELECT MIN(r.effective_from) FROM profile p JOIN profile_revisions r ON r.profile_id = p.id "
        "WHERE p.id = 1"
    ).fetchone()[0]
    if revision is None:
        raise ValueError("缺少用户资料：没有可用的用户资料版本。请先建立或恢复资料。记录未修改。")
    if not weight:
        anchor = connection.execute("SELECT MIN(local_date) FROM weight_measurements WHERE active = 1").fetchone()[0]
        if anchor is None:
            raise ValueError("缺少实际称重锚点。请先补录或恢复称重。记录未修改。")
        earliest = max(revision, anchor)
        if day < earliest:
            raise ValueError(f"最早支持的记录日期为 {earliest}。记录未修改。")
        return

    if day < revision:
        raise ValueError(f"最早支持的称重日期为 {revision}（首个用户资料生效日）。称重记录未修改。")
    if active_weight_id is None or previous_day is None:
        if connection.execute("SELECT 1 FROM weight_measurements WHERE active = 1 LIMIT 1").fetchone() is None:
            _check_dependents(connection, revision, day)
        return
    if not deleting and day <= previous_day:
        return
    remaining = connection.execute(
        "SELECT MIN(local_date) FROM weight_measurements WHERE active = 1 AND id <> ?", (active_weight_id,)
    ).fetchone()[0]
    first_after = remaining
    if not deleting and (remaining is None or day < remaining):
        first_after = day
    # A later/missing first anchor must not orphan previously covered events.
    # Removing the sole anchor requires all dependent facts to be inactive.
    _check_dependents(connection, previous_day if first_after is not None else date.min.isoformat(), first_after)


def set_fact_active(database: Database, table: str, fact_id: int, active: bool) -> None:
    """Toggle only the active flag and audit time, and atomically invalidate its day."""
    if table not in ("weight_measurements", "intake_events", "exercise_events"):
        raise ValueError(f"unknown fact table: {table}")
    with database.transaction() as connection:
        row = connection.execute(f"SELECT active, local_date FROM {table} WHERE id = ?", (fact_id,)).fetchone()
        if row is None:
            raise LookupError(f"{table} record {fact_id} does not exist")
        if bool(row["active"]) == active:
            return
        day = row["local_date"]
        weight = table == "weight_measurements"
        validate_fact_change(
            connection, day, previous_day=day, weight=weight,
            active_weight_id=fact_id if weight and not active else None, deleting=not active,
        )
        connection.execute(f"UPDATE {table} SET active = ?, updated_at = ? WHERE id = ?", (active, now_iso(), fact_id))
        database.mark_dirty(day, connection=connection)
