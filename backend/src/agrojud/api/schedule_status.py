"""API mapping for persisted schedule dispatch status."""

from typing import Any, cast

from agrojud.api.schemas import ScheduleDispatchResponse
from agrojud.db.models import ScheduleDispatch


def schedule_dispatch_response(
    dispatch: ScheduleDispatch | None,
) -> ScheduleDispatchResponse | None:
    if dispatch is None:
        return None
    return ScheduleDispatchResponse(
        id=dispatch.id,
        scheduled_for_date=dispatch.scheduled_for_date,
        status=cast(Any, dispatch.status),
        job_id=dispatch.job_id,
        missed_from=dispatch.missed_from,
        missed_through=dispatch.missed_through,
        reason=dispatch.reason,
        updated_at=dispatch.updated_at,
    )
