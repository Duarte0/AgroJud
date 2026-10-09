from datetime import UTC, date, datetime

import pytest

from agrojud.services.schedule_time import next_daily_occurrence, scheduled_instant


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        (datetime(2026, 10, 9, 8, 59, tzinfo=UTC), datetime(2026, 10, 9, 9, tzinfo=UTC)),
        (datetime(2026, 10, 9, 9, 0, tzinfo=UTC), datetime(2026, 10, 10, 9, tzinfo=UTC)),
        (datetime(2026, 10, 9, 10, 0, tzinfo=UTC), datetime(2026, 10, 10, 9, tzinfo=UTC)),
        (datetime(2026, 12, 31, 10, 0, tzinfo=UTC), datetime(2027, 1, 1, 9, tzinfo=UTC)),
    ],
)
def test_next_daily_occurrence_respects_local_six_and_calendar_rollover(
    reference: datetime,
    expected: datetime,
) -> None:
    assert next_daily_occurrence(reference) == expected


def test_scheduled_instant_supports_february_leap_day() -> None:
    assert scheduled_instant(date(2024, 2, 29)) == datetime(2024, 2, 29, 9, tzinfo=UTC)
