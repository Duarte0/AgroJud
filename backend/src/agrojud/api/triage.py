"""Human triage mutations and read-only history queries."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.schemas import (
    PaginationResponse,
    ProcessTriageHistoryEntryResponse,
    ProcessTriagePatchRequest,
    ProcessTriageSnapshotResponse,
    ProcessTriageStateResponse,
)
from agrojud.db.models import ProcessTriageHistory
from agrojud.services.triage import (
    ProcessTriageNotFoundError,
    TriageNoteRequiredError,
    TriageVersionConflictError,
    update_triage,
)
from agrojud.services.triage import (
    triage_history as load_triage_history,
)

router = APIRouter(prefix="/api/v1/processes", tags=["triage"])


@router.patch(
    "/{process_id}/triage",
    response_model=ProcessTriageStateResponse,
    responses=ERROR_RESPONSES,
    summary="Atualiza a triagem humana com controle de versão",
)
def patch_process_triage(
    process_id: UUID,
    body: ProcessTriagePatchRequest,
    request: Request,
) -> ProcessTriageStateResponse:
    changes = {
        field: str(getattr(body, field))
        for field in ("decision", "rural_link", "note")
        if field in body.model_fields_set
    }
    with request.app.state.session_factory() as session:
        try:
            state = update_triage(
                session,
                process_id,
                expected_version=body.expected_version,
                changes=changes,
            )
        except ProcessTriageNotFoundError as error:
            raise HTTPException(status_code=404) from error
        except TriageVersionConflictError as error:
            raise HTTPException(
                status_code=409, detail="A triagem foi alterada em outra edição."
            ) from error
        except TriageNoteRequiredError as error:
            raise HTTPException(
                status_code=422, detail="Confirme o vínculo rural com uma nota."
            ) from error
    return ProcessTriageStateResponse(
        decision=state.decision,
        rural_link=state.rural_link,
        note=state.note,
        version=state.version,
        updated_at=state.updated_at,
    )


@router.get(
    "/{process_id}/triage-history",
    response_model=PaginationResponse[ProcessTriageHistoryEntryResponse],
    responses=ERROR_RESPONSES,
    summary="Consulta o histórico humano de triagem",
)
def get_process_triage_history(
    process_id: UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginationResponse[ProcessTriageHistoryEntryResponse]:
    with request.app.state.session_factory() as session:
        try:
            rows, total = load_triage_history(
                session,
                process_id,
                page=page,
                page_size=page_size,
            )
        except ProcessTriageNotFoundError as error:
            raise HTTPException(status_code=404) from error
        items = [_history_entry(row) for row in rows]
    return PaginationResponse(items=items, page=page, page_size=page_size, total=total)


def _history_entry(row: ProcessTriageHistory) -> ProcessTriageHistoryEntryResponse:
    return ProcessTriageHistoryEntryResponse(
        id=row.id,
        version=row.version,
        previous_state=ProcessTriageSnapshotResponse.model_validate(row.previous_state),
        new_state=ProcessTriageSnapshotResponse.model_validate(row.new_state),
        origin="manual",
        created_at=row.created_at,
    )
