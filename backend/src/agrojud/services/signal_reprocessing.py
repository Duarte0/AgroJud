"""Enqueue, checkpoint, evaluate, and publish deterministic local signal runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from agrojud.db.models import (
    Job,
    JobCheckpoint,
    JobEvent,
    MovementOccurrence,
    MovementSnapshot,
    MovementSnapshotOccurrence,
    Process,
    ProcessSignal,
    Representation,
    SignalEvaluation,
    SignalRunInput,
    SignalRunProcess,
)
from agrojud.domain.canonical_json import JSONValue, sha256_json
from agrojud.domain.signals import (
    SignalEnvironment,
    SignalRule,
    evaluate_rule,
    rule_from_snapshot,
    select_signal_rules,
)
from agrojud.services.job_worker import JobExecutionContext, JobHandler, JobOutcome
from agrojud.services.jobs import JobLease, JobService
from agrojud.sources.contracts import build_query_by_case_number

INPUT_BATCH_SIZE = 100
ACTIVE_STATUSES = ("queued", "running", "retry_wait")


class SignalSelectionError(ValueError):
    """A requested process selection cannot be resolved locally."""


class SignalRunError(ValueError):
    """A signal-run ID does not refer to a reprocess_rules job."""


class SignalReprocessingService:
    """Create immutable local input snapshots and inspect persisted run progress."""

    def __init__(self, sessions: sessionmaker[Session], jobs: JobService) -> None:
        self.sessions = sessions
        self.jobs = jobs

    def enqueue(
        self,
        *,
        environment: SignalEnvironment,
        process_ids: Sequence[UUID] | None = None,
        process_number: str | None = None,
        rule_ids: Sequence[str] | None = None,
    ) -> tuple[UUID, bool, int, int]:
        rules = select_signal_rules(environment, rule_ids)
        selected_rules = [rule.snapshot() for rule in rules]
        with self.sessions() as session, session.begin():
            resolved_ids = self._resolve_process_ids(session, process_ids, process_number)
            processes = (
                session.scalars(
                    select(Process).where(Process.id.in_(resolved_ids)).order_by(Process.id)
                ).all()
                if resolved_ids
                else []
            )
            if len(processes) != len(resolved_ids):
                raise SignalSelectionError(
                    "A seleção contém processo que não existe na base local."
                )

            representations = (
                session.scalars(
                    select(Representation)
                    .where(
                        Representation.process_id.in_(resolved_ids),
                        Representation.source == _source_for_environment(environment),
                    )
                    .order_by(Representation.process_id, Representation.id)
                ).all()
                if resolved_ids
                else []
            )
            representation_ids = [item.id for item in representations]
            complete_snapshots: dict[UUID, MovementSnapshot] = {}
            if representation_ids:
                snapshots = session.scalars(
                    select(MovementSnapshot)
                    .where(
                        MovementSnapshot.representation_id.in_(representation_ids),
                        MovementSnapshot.is_complete.is_(True),
                    )
                    .order_by(
                        MovementSnapshot.representation_id,
                        MovementSnapshot.processed_at.desc(),
                        MovementSnapshot.id.desc(),
                    )
                ).all()
                for snapshot in snapshots:
                    complete_snapshots.setdefault(snapshot.representation_id, snapshot)

            process_input_counts = {process.id: 0 for process in processes}
            input_specs: list[dict[str, Any]] = []
            for representation in representations:
                complete_snapshot = complete_snapshots.get(representation.id)
                latest_version_id = representation.latest_version_id
                selected_version_id = (
                    complete_snapshot.version_id if complete_snapshot is not None else None
                )
                is_stale = selected_version_id != latest_version_id
                input_specs.append(
                    {
                        "id": uuid4(),
                        "process_id": representation.process_id,
                        "representation_id": representation.id,
                        "latest_version_id": latest_version_id,
                        "input_version_id": selected_version_id,
                        "movement_snapshot_id": (
                            complete_snapshot.id if complete_snapshot is not None else None
                        ),
                        "input_complete": complete_snapshot is not None,
                        "evidence_stale": is_stale,
                        "diagnostic": (
                            None
                            if complete_snapshot is not None
                            else "Nenhuma versão da representação foi normalizada integralmente."
                        ),
                    }
                )
                process_input_counts[representation.process_id] += 1

            rules_snapshot: JSONValue = cast(JSONValue, selected_rules)
            selection_snapshot: dict[str, JSONValue] = {
                "process_ids": [str(item) for item in resolved_ids],
                "filter": {"process_number": process_number} if process_number else {},
            }
            inputs_manifest: JSONValue = cast(
                JSONValue,
                [
                    {
                        "process_id": str(item["process_id"]),
                        "representation_id": str(item["representation_id"]),
                        "latest_version_id": str(item["latest_version_id"])
                        if item["latest_version_id"] is not None
                        else None,
                        "input_version_id": str(item["input_version_id"])
                        if item["input_version_id"] is not None
                        else None,
                        "movement_snapshot_id": str(item["movement_snapshot_id"])
                        if item["movement_snapshot_id"] is not None
                        else None,
                        "input_complete": item["input_complete"],
                        "evidence_stale": item["evidence_stale"],
                    }
                    for item in input_specs
                ],
            )
            operation_key = sha256_json(
                {
                    "job_type": "reprocess_rules",
                    "environment": environment,
                    "selection": selection_snapshot,
                    "rules": rules_snapshot,
                    "inputs": inputs_manifest,
                }
            )
            parameters_snapshot: dict[str, JSONValue] = {
                "kind": "reprocess_rules",
                "environment": environment,
                "selection": selection_snapshot,
                "rules": rules_snapshot,
                "input_count": len(input_specs),
                "process_count": len(resolved_ids),
                "input_manifest_sha256": sha256_json(inputs_manifest),
            }

            savepoint = session.begin_nested()
            now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise RuntimeError("O banco não retornou horário para o job local.")
            job_id = session.execute(
                pg_insert(Job)
                .values(
                    id=uuid4(),
                    collection_id=None,
                    job_type="reprocess_rules",
                    mode=environment,
                    source="local",
                    tribunal="TJGO",
                    operation_key=operation_key,
                    parameters_snapshot=parameters_snapshot,
                    status="queued",
                    next_attempt_at=now,
                    attempt_count=0,
                    retry_cycle=1,
                    page_attempt_count=0,
                    persistence_attempt_count=0,
                    recovery_count=0,
                    cursor_invalid=False,
                    event_count=0,
                )
                .on_conflict_do_nothing(
                    index_elements=[Job.operation_key],
                    index_where=text("status in ('queued', 'running', 'retry_wait')"),
                )
                .returning(Job.id)
            ).scalar_one_or_none()
            if job_id is None:
                savepoint.rollback()
                active = session.scalar(
                    select(Job)
                    .where(Job.operation_key == operation_key, Job.status.in_(ACTIVE_STATUSES))
                    .with_for_update()
                )
                if active is None:
                    raise RuntimeError("A execução local equivalente não pôde ser localizada.")
                return active.id, False, len(resolved_ids), len(input_specs)

            session.add(
                JobCheckpoint(
                    id=uuid4(), job_id=job_id, cursor=None, next_page=1, revision=0, updated_at=now
                )
            )
            session.add_all(
                SignalRunProcess(
                    id=uuid4(),
                    job_id=job_id,
                    process_id=process_id,
                    status="pending",
                    input_count=count,
                    processed_input_count=0,
                )
                for process_id, count in process_input_counts.items()
            )
            session.add_all(
                SignalRunInput(
                    id=cast(UUID, item["id"]),
                    job_id=job_id,
                    process_id=cast(UUID, item["process_id"]),
                    representation_id=cast(UUID, item["representation_id"]),
                    latest_version_id=cast(UUID | None, item["latest_version_id"]),
                    input_version_id=cast(UUID | None, item["input_version_id"]),
                    movement_snapshot_id=cast(UUID | None, item["movement_snapshot_id"]),
                    input_complete=cast(bool, item["input_complete"]),
                    evidence_stale=cast(bool, item["evidence_stale"]),
                    status="pending",
                    diagnostic=cast(str | None, item["diagnostic"]),
                )
                for item in input_specs
            )
            job = session.get(Job, job_id)
            if job is None:  # pragma: no cover - inserted above in this transaction
                raise RuntimeError("O job local recém-criado não pôde ser lido.")
            _append_job_event(
                session,
                job,
                "enqueued",
                {
                    "job_type": "reprocess_rules",
                    "process_count": len(resolved_ids),
                    "input_count": len(input_specs),
                    "rule_count": len(rules),
                    "operation_key": operation_key,
                },
            )
            savepoint.commit()
            return job_id, True, len(resolved_ids), len(input_specs)

    @staticmethod
    def _resolve_process_ids(
        session: Session,
        process_ids: Sequence[UUID] | None,
        process_number: str | None,
    ) -> list[UUID]:
        if process_ids is not None:
            if len(set(process_ids)) != len(process_ids):
                raise SignalSelectionError("A seleção explícita contém identificadores repetidos.")
            return sorted(process_ids)
        if process_number is None:
            raise SignalSelectionError("Informe processos ou um filtro local para reprocessamento.")
        normalized = build_query_by_case_number(process_number).process_number
        return list(
            session.scalars(
                select(Process.id).where(Process.numero_cnj == normalized).order_by(Process.id)
            ).all()
        )

    def resume(self, job_id: UUID, *, environment: SignalEnvironment) -> UUID:
        with self.sessions() as session:
            job = session.get(Job, job_id)
            if job is None or job.job_type != "reprocess_rules" or job.mode != environment:
                raise SignalRunError("A execução local de regras não existe.")
        return self.jobs.resume(job_id)

    def inspect(
        self, job_id: UUID, *, environment: SignalEnvironment | None = None
    ) -> dict[str, Any]:
        inspection = self.jobs.inspect(job_id)
        if inspection.job_type != "reprocess_rules" or (
            environment is not None and inspection.mode != environment
        ):
            raise SignalRunError("A execução local de regras não existe.")
        snapshot = inspection.parameters_snapshot
        with self.sessions() as session:
            input_count = (
                session.scalar(
                    select(func.count())
                    .select_from(SignalRunInput)
                    .where(SignalRunInput.job_id == job_id)
                )
                or 0
            )
            processed_count = (
                session.scalar(
                    select(func.count())
                    .select_from(SignalRunInput)
                    .where(SignalRunInput.job_id == job_id, SignalRunInput.status != "pending")
                )
                or 0
            )
            processes = session.scalars(
                select(SignalRunProcess).where(SignalRunProcess.job_id == job_id)
            ).all()
        return {
            "job_id": inspection.id,
            "status": inspection.status,
            "reason": inspection.reason,
            "coverage": inspection.coverage,
            "process_count": int(snapshot.get("process_count", len(processes))),
            "input_count": int(snapshot.get("input_count", input_count)),
            "processed_input_count": int(processed_count),
            "completed_process_count": sum(item.status == "completed" for item in processes),
            "stale_process_count": sum(item.status == "stale" for item in processes),
            "not_evaluated_process_count": sum(
                item.status == "not_evaluated" for item in processes
            ),
            "resumable": inspection.status in ("failed", "cancelled"),
            "created_at": inspection.created_at,
            "finished_at": inspection.finished_at,
        }


def build_signal_run_handler(jobs: JobService) -> JobHandler:
    """Build a pure-local handler with 100-input transactions and leased progress."""

    def handle(lease: JobLease, context: JobExecutionContext) -> JobOutcome:
        if lease.job_type != "reprocess_rules" or lease.collection_id is not None:
            raise RuntimeError("O job recebido não corresponde ao reprocessamento local.")
        raw_rules = lease.parameters_snapshot.get("rules")
        if not isinstance(raw_rules, list):
            raise RuntimeError("O snapshot do job não contém as regras congeladas.")
        rules = tuple(
            rule_from_snapshot(cast(Mapping[str, Any], item))
            for item in raw_rules
            if isinstance(item, Mapping)
        )
        if not rules or len(rules) != len(raw_rules):
            raise RuntimeError("O snapshot do job contém regra inválida.")
        if any(not rule.enablement[lease.mode].enabled for rule in rules):
            raise RuntimeError("O snapshot tenta executar regra inativa neste ambiente.")
        rule_ids = [rule.id for rule in rules]
        inspection = jobs.inspect(lease.job_id)
        coverage = dict(inspection.coverage or {})

        try:
            while True:
                context.assert_active()
                batch_size = _commit_next_batch(lease, context, rules, rule_ids, coverage)
                if batch_size == 0:
                    with context.owned_transaction(lease) as session:
                        _finish_empty_processes(session, lease.job_id, rule_ids, lease.mode)
                        _update_coverage(session, lease.job_id, coverage)
                    return JobOutcome(
                        status="completed",
                        coverage=cast(dict[str, JSONValue], coverage),
                        reason="inputs_not_evaluable"
                        if coverage.get("not_evaluated_inputs", 0)
                        else None,
                    )
        except Exception as error:
            from agrojud.services.jobs import JobCancellationRequestedError

            if isinstance(error, JobCancellationRequestedError):
                current = jobs.inspect(lease.job_id)
                return JobOutcome(
                    status="cancelled",
                    coverage=cast(dict[str, JSONValue] | None, current.coverage),
                    reason="cancel_requested",
                )
            raise

    return handle


def _commit_next_batch(
    lease: JobLease,
    context: JobExecutionContext,
    rules: Sequence[SignalRule],
    rule_ids: Sequence[str],
    coverage: dict[str, Any],
) -> int:
    context.assert_active()
    with context.owned_transaction(lease) as session:
        checkpoint = session.execute(
            select(JobCheckpoint).where(JobCheckpoint.job_id == lease.job_id).with_for_update()
        ).scalar_one()
        cursor = _decode_input_cursor(checkpoint.cursor)
        statement = (
            select(SignalRunInput)
            .where(SignalRunInput.job_id == lease.job_id)
            .order_by(SignalRunInput.id)
            .limit(INPUT_BATCH_SIZE)
            .with_for_update()
        )
        if cursor is not None:
            statement = statement.where(SignalRunInput.id > cursor)
        inputs = session.scalars(statement).all()
        if not inputs:
            pending = (
                session.scalar(
                    select(func.count())
                    .select_from(SignalRunInput)
                    .where(
                        SignalRunInput.job_id == lease.job_id, SignalRunInput.status == "pending"
                    )
                )
                or 0
            )
            if pending:
                raise RuntimeError("Há entradas pendentes antes do cursor local confirmado.")
            return 0

        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise RuntimeError("O banco não retornou horário para avaliação de sinais.")
        for run_input in inputs:
            _evaluate_input(session, lease, run_input, rules, now)
            _advance_process_publication(
                session,
                lease.job_id,
                run_input.process_id,
                rule_ids,
                lease.mode,
                now,
            )

        checkpoint.cursor = str(inputs[-1].id)
        checkpoint.next_page += 1
        checkpoint.revision += 1
        checkpoint.updated_at = now
        session.flush()
        updated = _read_coverage(session, lease.job_id, coverage)
        coverage.clear()
        coverage.update(updated)
        job = session.get(Job, lease.job_id)
        if job is None:  # pragma: no cover - owned transaction locks an existing job
            raise RuntimeError("O job local não existe.")
        job.coverage = cast(dict[str, JSONValue], dict(coverage))
        _append_job_event(
            session,
            job,
            "rule_batch_committed",
            {
                "inputs": len(inputs),
                "processed_inputs": coverage.get("processed_inputs", 0),
                "checkpoint_revision": checkpoint.revision,
            },
        )
        return len(inputs)


def _evaluate_input(
    session: Session,
    lease: JobLease,
    run_input: SignalRunInput,
    rules: Sequence[SignalRule],
    now: datetime,
) -> None:
    movements: list[tuple[UUID, Mapping[str, Any]]] = []
    input_complete = run_input.input_complete and run_input.movement_snapshot_id is not None
    if run_input.input_complete and run_input.movement_snapshot_id is not None:
        rows = session.execute(
            select(MovementOccurrence.id, MovementOccurrence.normalized_content)
            .join(
                MovementSnapshotOccurrence,
                MovementSnapshotOccurrence.occurrence_id == MovementOccurrence.id,
            )
            .where(
                MovementSnapshotOccurrence.snapshot_id == run_input.movement_snapshot_id,
                MovementSnapshotOccurrence.present.is_(True),
            )
            .order_by(MovementOccurrence.id)
        ).all()
        for occurrence_id, content in rows:
            if not isinstance(content, Mapping):
                run_input.diagnostic = "Um movimento normalizado não possui objeto estruturado."
                input_complete = False
                movements = []
                break
            movements.append((cast(UUID, occurrence_id), cast(Mapping[str, Any], content)))

    is_not_evaluable = False
    for rule in rules:
        evaluation = evaluate_rule(rule, movements, input_complete=input_complete)
        matched_signal_ids: list[str] = []
        if evaluation.outcome == "matched":
            for match in evaluation.matches:
                fingerprint = sha256_json(
                    {
                        "rule_id": rule.id,
                        "rule_version": rule.version,
                        "predicate": {
                            "scope": "movement",
                            "field": "codigo",
                            "operator": "equals_any",
                            "values": list(rule.values),
                        },
                        "evidence_id": str(match.evidence_id),
                        "code": match.code,
                    }
                )
                signal_id = session.execute(
                    pg_insert(ProcessSignal)
                    .values(
                        id=uuid4(),
                        process_id=run_input.process_id,
                        representation_id=run_input.representation_id,
                        evidence_kind="movement_occurrence",
                        evidence_id=match.evidence_id,
                        movement_occurrence_id=match.evidence_id,
                        evidence_version_id=run_input.input_version_id,
                        evidence_snapshot_id=run_input.movement_snapshot_id,
                        rule_id=rule.id,
                        rule_version=rule.version,
                        environment=lease.mode,
                        enablement_snapshot=rule.enablement[lease.mode].snapshot(),
                        category=rule.category,
                        explanation=rule.explanation,
                        result_fingerprint=fingerprint,
                        created_run_id=lease.job_id,
                        published_run_id=None,
                        is_published=False,
                        is_current=False,
                        evidence_stale=run_input.evidence_stale,
                        evaluated_at=now,
                    )
                    .on_conflict_do_nothing(constraint="uq_process_signals_rule_evidence_result")
                    .returning(ProcessSignal.id)
                ).scalar_one_or_none()
                if signal_id is None:
                    signal_id = session.scalar(
                        select(ProcessSignal.id).where(
                            ProcessSignal.rule_id == rule.id,
                            ProcessSignal.rule_version == rule.version,
                            ProcessSignal.evidence_id == match.evidence_id,
                            ProcessSignal.result_fingerprint == fingerprint,
                        )
                    )
                if signal_id is None:  # pragma: no cover - unique conflict retains the row
                    raise RuntimeError("O sinal idempotente não pôde ser localizado.")
                matched_signal_ids.append(str(signal_id))
        elif evaluation.outcome == "not_evaluated":
            is_not_evaluable = True
            if run_input.diagnostic is None:
                run_input.diagnostic = evaluation.diagnostic

        session.add(
            SignalEvaluation(
                id=uuid4(),
                job_id=lease.job_id,
                input_id=run_input.id,
                process_id=run_input.process_id,
                rule_id=rule.id,
                rule_version=rule.version,
                outcome=evaluation.outcome,
                diagnostic=evaluation.diagnostic,
                matched_signal_ids=matched_signal_ids,
                input_version_id=run_input.input_version_id,
                movement_snapshot_id=run_input.movement_snapshot_id,
                evaluated_at=now,
            )
        )

    if is_not_evaluable:
        run_input.status = "not_evaluated"
        if run_input.diagnostic is None:
            run_input.diagnostic = "As regras não puderam avaliar todos os dados estruturados."
    else:
        run_input.status = "completed"
        if run_input.evidence_stale and run_input.diagnostic is None:
            run_input.diagnostic = (
                "Resultado calculado sobre a última versão integralmente normalizada."
            )


def _advance_process_publication(
    session: Session,
    job_id: UUID,
    process_id: UUID,
    rule_ids: Sequence[str],
    environment: SignalEnvironment,
    now: datetime,
) -> None:
    process_run = session.execute(
        select(SignalRunProcess)
        .where(SignalRunProcess.job_id == job_id, SignalRunProcess.process_id == process_id)
        .with_for_update()
    ).scalar_one()
    process_run.processed_input_count += 1
    if process_run.processed_input_count != process_run.input_count:
        return

    inputs = session.scalars(
        select(SignalRunInput).where(
            SignalRunInput.job_id == job_id, SignalRunInput.process_id == process_id
        )
    ).all()
    has_not_evaluated = any(item.status == "not_evaluated" for item in inputs)
    if has_not_evaluated:
        process_run.status = "not_evaluated"
        process_run.published_at = now
        session.execute(
            update(ProcessSignal)
            .where(
                ProcessSignal.process_id == process_id,
                ProcessSignal.environment == environment,
                ProcessSignal.is_current.is_(True),
                ProcessSignal.rule_id.in_(rule_ids),
            )
            .values(evidence_stale=True)
        )
        return

    stale = any(item.evidence_stale for item in inputs)
    matched = session.scalars(
        select(SignalEvaluation).where(
            SignalEvaluation.job_id == job_id,
            SignalEvaluation.process_id == process_id,
            SignalEvaluation.outcome == "matched",
            SignalEvaluation.rule_id.in_(rule_ids),
        )
    ).all()
    desired_signal_ids = {
        UUID(value) for evaluation in matched for value in evaluation.matched_signal_ids
    }
    session.execute(
        update(ProcessSignal)
        .where(
            ProcessSignal.process_id == process_id,
            ProcessSignal.environment == environment,
            ProcessSignal.is_current.is_(True),
            ProcessSignal.rule_id.in_(rule_ids),
        )
        .values(is_current=False)
    )
    if desired_signal_ids:
        session.execute(
            update(ProcessSignal)
            .where(
                ProcessSignal.id.in_(desired_signal_ids),
                ProcessSignal.environment == environment,
            )
            .values(
                is_published=True,
                published_run_id=job_id,
                is_current=True,
                evidence_stale=stale,
            )
        )
    process_run.status = "stale" if stale else "completed"
    process_run.published_at = now


def _finish_empty_processes(
    session: Session,
    job_id: UUID,
    rule_ids: Sequence[str],
    environment: SignalEnvironment,
) -> None:
    now = session.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise RuntimeError("O banco não retornou horário para finalizar o job vazio.")
    empty_runs = session.scalars(
        select(SignalRunProcess)
        .where(
            SignalRunProcess.job_id == job_id,
            SignalRunProcess.status == "pending",
            SignalRunProcess.input_count == 0,
        )
        .with_for_update()
    ).all()
    for process_run in empty_runs:
        process_run.status = "completed"
        process_run.published_at = now
        session.execute(
            update(ProcessSignal)
            .where(
                ProcessSignal.process_id == process_run.process_id,
                ProcessSignal.environment == environment,
                ProcessSignal.is_current.is_(True),
                ProcessSignal.rule_id.in_(rule_ids),
            )
            .values(is_current=False)
        )


def _read_coverage(session: Session, job_id: UUID, prior: Mapping[str, Any]) -> dict[str, Any]:
    total_processes = (
        session.scalar(
            select(func.count())
            .select_from(SignalRunProcess)
            .where(SignalRunProcess.job_id == job_id)
        )
        or 0
    )
    total_inputs = (
        session.scalar(
            select(func.count()).select_from(SignalRunInput).where(SignalRunInput.job_id == job_id)
        )
        or 0
    )
    processed_inputs = (
        session.scalar(
            select(func.count())
            .select_from(SignalRunInput)
            .where(SignalRunInput.job_id == job_id, SignalRunInput.status != "pending")
        )
        or 0
    )
    not_evaluated_inputs = (
        session.scalar(
            select(func.count())
            .select_from(SignalRunInput)
            .where(SignalRunInput.job_id == job_id, SignalRunInput.status == "not_evaluated")
        )
        or 0
    )
    completed_processes = (
        session.scalar(
            select(func.count())
            .select_from(SignalRunProcess)
            .where(SignalRunProcess.job_id == job_id, SignalRunProcess.status == "completed")
        )
        or 0
    )
    stale_processes = (
        session.scalar(
            select(func.count())
            .select_from(SignalRunProcess)
            .where(SignalRunProcess.job_id == job_id, SignalRunProcess.status == "stale")
        )
        or 0
    )
    not_evaluated_processes = (
        session.scalar(
            select(func.count())
            .select_from(SignalRunProcess)
            .where(SignalRunProcess.job_id == job_id, SignalRunProcess.status == "not_evaluated")
        )
        or 0
    )
    return {
        "total_processes": int(total_processes),
        "total_inputs": int(total_inputs),
        "processed_inputs": int(processed_inputs),
        "not_evaluated_inputs": int(not_evaluated_inputs),
        "completed_processes": int(completed_processes),
        "stale_processes": int(stale_processes),
        "not_evaluated_processes": int(not_evaluated_processes),
    }


def _update_coverage(session: Session, job_id: UUID, coverage: dict[str, Any]) -> None:
    job = session.get(Job, job_id)
    if job is None:  # pragma: no cover - owned transaction locks an existing job
        raise RuntimeError("O job local não existe.")
    process_count = (
        session.scalar(
            select(func.count())
            .select_from(SignalRunProcess)
            .where(SignalRunProcess.job_id == job_id)
        )
        or 0
    )
    coverage["total_processes"] = int(process_count)
    updated = _read_coverage(session, job_id, coverage)
    coverage.clear()
    coverage.update(updated)
    job.coverage = cast(dict[str, JSONValue], dict(coverage))


def _decode_input_cursor(value: object) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeError("O cursor local do reprocessamento é inválido.")
    try:
        return UUID(value)
    except ValueError as error:
        raise RuntimeError("O cursor local do reprocessamento é inválido.") from error


def _append_job_event(
    session: Session, job: Job, event_type: str, details: dict[str, JSONValue]
) -> None:
    job.event_count += 1
    session.add(
        JobEvent(
            id=uuid4(),
            job_id=job.id,
            event_number=job.event_count,
            event_type=event_type,
            details=details,
        )
    )


def _source_for_environment(environment: SignalEnvironment) -> str:
    return "synthetic" if environment == "demo" else "datajud"
