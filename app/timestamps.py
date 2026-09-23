"""CalorieK's recorded-local clock policy (not a global instant timeline).

Dates and model intervals retain the calendar/clock written by the user. Offset
information in historical records is preserved, but never shifts their local
day. No timezone is inferred for naive history. See docs/CAL16_HARDENING.md.
"""

from datetime import date, datetime


def parse_datetime(value: str | datetime) -> datetime:
    """Parse ISO history without discarding its offset or fractional seconds."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_local_datetime(value: str | datetime) -> datetime:
    """Return the recorded wall clock, not UTC or the computer's current zone."""
    return parse_datetime(value).replace(tzinfo=None)


def now_iso() -> str:
    """Audit metadata is a known instant: keep its local offset and precision.

    Unlike model event clocks, these values participate in conservative legacy
    nutrition provenance comparisons. Do not turn known instants into naive
    metadata or infer offsets for old naive metadata.
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_datetime(value: datetime | date | str | None = None) -> tuple[str, str]:
    """Canonical local ISO timestamp and local date; never shift calendar days."""
    if value is None:
        moment = datetime.now()
    elif isinstance(value, date) and not isinstance(value, datetime):
        moment = datetime.combine(value, datetime.min.time())
    else:
        try:
            moment = parse_local_datetime(value if isinstance(value, datetime) else str(value).strip())
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid ISO datetime: {value!r}") from exc
    return moment.isoformat(timespec="seconds"), moment.date().isoformat()


def preserve_or_normalize_event_update(
    current_occurred_at: str,
    current_local_date: str,
    requested_occurred_at: datetime | date | str | None,
) -> tuple[str, str]:
    """Preserve historical text/date unless the recorded-local clock changes.

    Compare before second-resolution normalization so resubmitted fractions,
    offsets, Z and separator spelling survive ordinary edits byte-for-byte.
    Offset changes alone are not retimes; a different local clock is, even when
    the two aware values describe the same global instant.
    """
    if requested_occurred_at is None:
        return current_occurred_at, current_local_date
    if isinstance(requested_occurred_at, date) and not isinstance(requested_occurred_at, datetime):
        requested_clock = datetime.combine(requested_occurred_at, datetime.min.time())
    else:
        requested_clock = parse_local_datetime(
            requested_occurred_at if isinstance(requested_occurred_at, datetime)
            else str(requested_occurred_at).strip()
        )
    if requested_clock == parse_local_datetime(current_occurred_at):
        return current_occurred_at, current_local_date
    return normalize_datetime(requested_clock)


def compare_local_timestamps(left: str, right: str) -> int:
    """SQLite collation for model chronology; equivalent clocks tie by row ID.

    A malformed value fails the query, rather than silently guessing its date.
    Kept out of schema/index definitions so portable SQLite backups need no UDF.
    """
    lhs, rhs = parse_local_datetime(left), parse_local_datetime(right)
    return (lhs > rhs) - (lhs < rhs)
