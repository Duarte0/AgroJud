"""Calendar helpers for the fixed daily local-time schedule."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SCHEDULE_TIMEZONE = ZoneInfo("America/Sao_Paulo")
SCHEDULE_HOUR = 6


def next_daily_occurrence(reference: datetime) -> datetime:
    """Return the next 06:00 America/Sao_Paulo instant as UTC."""

    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("O instante de referência precisa conter fuso horário.")
    local = reference.astimezone(SCHEDULE_TIMEZONE)
    candidate = datetime.combine(local.date(), time(SCHEDULE_HOUR), tzinfo=SCHEDULE_TIMEZONE)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def local_schedule_date(value: datetime) -> date:
    """Map an aware instant to the schedule's local date."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("O instante agendado precisa conter fuso horário.")
    return value.astimezone(SCHEDULE_TIMEZONE).date()


def scheduled_instant(day: date) -> datetime:
    """Build the local 06:00 occurrence for a calendar date as UTC."""

    return datetime.combine(day, time(SCHEDULE_HOUR), tzinfo=SCHEDULE_TIMEZONE).astimezone(UTC)
