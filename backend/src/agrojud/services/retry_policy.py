"""Deterministic policy helpers for bounded HTTP and persistence retries."""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from sqlalchemy.exc import DBAPIError, IntegrityError

from agrojud.sources.contracts import SourceError, SourceErrorCode

MAX_HTTP_ATTEMPTS_PER_PAGE = 5
MAX_PERSISTENCE_ATTEMPTS_PER_PAGE = 5
MAX_LEASE_RECOVERIES_PER_CYCLE = 5
MAX_BACKOFF_SECONDS = 60
MIN_REQUEST_INTERVAL = timedelta(seconds=1)
_DELAY_SECONDS = re.compile(r"^[0-9]+$")
type RandomValue = Callable[[], float]
type Clock = Callable[[], datetime]


def is_retryable_source_error(error: SourceError) -> bool:
    """Return true only for the transient source failures in SPEC-009."""

    if error.code is SourceErrorCode.CONTRACT or error.status_code in (400, 401, 403):
        return False
    if error.code in (SourceErrorCode.NETWORK, SourceErrorCode.RATE_LIMIT):
        return True
    return error.status_code is not None and 500 <= error.status_code <= 599


def retry_deadline(
    now: datetime,
    *,
    attempt_number: int,
    retry_after: str | None = None,
    random_value: RandomValue = random.random,
) -> datetime:
    """Compute bounded full-jitter backoff and honor Retry-After as a minimum."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("O relógio de retry precisa incluir fuso horário.")
    if not 1 <= attempt_number <= MAX_HTTP_ATTEMPTS_PER_PAGE:
        raise ValueError("A tentativa HTTP está fora do orçamento da página.")
    fraction = random_value()
    if not 0 <= fraction <= 1:
        raise ValueError("A fonte aleatória deve retornar um valor entre zero e um.")
    backoff_cap = min(MAX_BACKOFF_SECONDS, 2 * (2 ** (attempt_number - 1)))
    local_deadline = now + timedelta(seconds=backoff_cap * fraction)
    server_deadline = parse_retry_after(retry_after, now)
    return max(local_deadline, server_deadline or local_deadline)


def parse_retry_after(value: str | None, now: datetime) -> datetime | None:
    """Parse Retry-After delay-seconds or HTTP-date; malformed values are ignored."""

    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if _DELAY_SECONDS.fullmatch(normalized):
        try:
            return now + timedelta(seconds=int(normalized))
        except OverflowError:
            return None
    try:
        parsed = parsedate_to_datetime(normalized)
    except TypeError, ValueError, OverflowError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def is_transient_persistence_error(error: BaseException) -> bool:
    """Identify PostgreSQL disconnect/deadlock/serialization errors only."""

    if isinstance(error, IntegrityError) or not isinstance(error, DBAPIError):
        return False
    if error.connection_invalidated:
        return True
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None)
    if sqlstate is None:
        diagnostic = getattr(original, "diag", None)
        sqlstate = getattr(diagnostic, "sqlstate", None)
    return isinstance(sqlstate, str) and (
        sqlstate.startswith("08") or sqlstate in {"40001", "40P01"}
    )


def utc_now() -> datetime:
    """Default injectable UTC clock for retry policy callers."""

    return datetime.now(UTC)
