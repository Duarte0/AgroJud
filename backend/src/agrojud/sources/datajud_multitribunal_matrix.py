"""Offline merge and human review for multitribunal DataJud evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from agrojud.sources.catalog import load_catalog
from agrojud.sources.datajud_multitribunal_validation import (
    ALLOWLIST_VERSION,
    CODE_VERSION,
    MAX_TRIBUNALS_PER_BATCH,
    PRESET_IDS,
    TRIBUNAL_ALLOWLIST,
    EvidenceState,
    _aggregate_capabilities,
    _aggregate_pair,
    _sanitize_rationale,
    _validate_official_reference,
    current_discovery_presets,
)
from agrojud.sources.datajud_validation import write_report

type JsonObject = dict[str, object]

_CAPABILITY_NAMES = (
    "http_access",
    "envelope",
    "essential_fields",
    "tribunal_identity",
    "date_filter",
    "query_by_case_number",
    "source_identity",
    "sort_timestamp",
    "compound_sort",
    "pagination",
)
_VALID_STATES = {
    EvidenceState.VALIDATED,
    EvidenceState.INCOMPATIBLE,
    EvidenceState.INCONCLUSIVE,
    EvidenceState.NOT_RUN,
    EvidenceState.NOT_APPLICABLE,
}


def merge_batch_reports(batch_paths: Sequence[Path]) -> JsonObject:
    """Merge reports from one or more local batches, retaining every unique attempt."""

    if not batch_paths:
        raise ValueError("Informe ao menos um resumo batch JSON.")
    catalog = load_catalog()
    preset_versions = {item.id: item.version for item in current_discovery_presets()}
    if set(preset_versions) != set(PRESET_IDS):
        raise ValueError("O catálogo S5 atual diverge dos quatro presets desta SPEC.")

    attempts: list[JsonObject] = []
    batches: list[JsonObject] = []
    seen_batch_digests: dict[str, str] = {}
    seen_report_digests: dict[tuple[str, str], str] = {}

    for batch_path in batch_paths:
        batch, batch_bytes = _read_json_object(batch_path, "resumo batch")
        _validate_batch_summary(batch)
        run_id = cast(str, batch["run_id"])
        if batch_path.name != f"batch-{run_id}.json":
            raise ValueError("O nome do resumo batch deve corresponder ao run_id registrado.")
        batch_digest = _canonical_sha256(batch)
        previous_batch_digest = seen_batch_digests.get(run_id)
        if previous_batch_digest is not None and previous_batch_digest != batch_digest:
            raise ValueError(f"run_id {run_id} aparece em resumos batch divergentes.")
        seen_batch_digests[run_id] = batch_digest
        batch_sha256 = hashlib.sha256(batch_bytes).hexdigest()
        references = cast(list[JsonObject], batch["tribunal_reports"])
        requested_aliases = cast(list[str], batch["requested_aliases"])
        reference_aliases = [cast(str, item["alias"]) for item in references]
        if len(set(reference_aliases)) != len(reference_aliases):
            raise ValueError(f"O resumo batch {run_id} repete alias de relatório.")
        if set(reference_aliases) != set(requested_aliases):
            raise ValueError(f"O resumo batch {run_id} não referencia todos os aliases pedidos.")
        observed_requests = 0

        if previous_batch_digest is None:
            batches.append(
                {
                    "run_id": run_id,
                    "batch_file": batch_path.name,
                    "batch_sha256": batch_sha256,
                    "started_at": batch["started_at"],
                    "requested_aliases": requested_aliases,
                    "code_version": batch["code_version"],
                    "catalog_version": batch["catalog_version"],
                }
            )

        for reference in references:
            alias = cast(str, reference["alias"])
            file_key = cast(str, reference["file_key"])
            report_path = _resolve_batch_file(batch_path, alias, run_id, file_key)
            endpoint_report, report_bytes = _read_json_object(report_path, "relatório de tribunal")
            _validate_endpoint_report(endpoint_report, alias, run_id, batch)
            observed_requests += cast(int, endpoint_report["requests_made"])
            report_digest = _canonical_sha256(endpoint_report)
            report_key = (run_id, alias)
            previous_report_digest = seen_report_digests.get(report_key)
            if previous_report_digest is not None:
                if previous_report_digest != report_digest:
                    raise ValueError(f"run_id {run_id} tem relatórios divergentes para {alias}.")
                continue
            seen_report_digests[report_key] = report_digest
            attempts.append(
                _normalize_attempt(
                    endpoint_report,
                    run_id=run_id,
                    batch_file=batch_path.name,
                    batch_sha256=batch_sha256,
                    report_file=file_key,
                    report_sha256=hashlib.sha256(report_bytes).hexdigest(),
                )
            )
        if batch.get("requests_made") != observed_requests:
            raise ValueError(f"requests_made diverge entre resumo e relatórios do lote {run_id}.")

    matrix: JsonObject = {
        "report_version": 1,
        "kind": "datajud_multitribunal_coverage_matrix",
        "created_at": datetime.now(UTC).isoformat(),
        "code_version": CODE_VERSION,
        "allowlist_version": ALLOWLIST_VERSION,
        "catalog_version": catalog.catalog_version,
        "tpu_version": catalog.tpu_version,
        "preset_versions": preset_versions,
        "batch_count": len(batches),
        "batches": batches,
        "attempts": attempts,
        "matrix_review": {"state": "PENDING_HUMAN_REVIEW"},
    }
    coverage_matrix = _project_coverage_matrix(matrix, resolutions=())
    matrix["coverage_matrix"] = coverage_matrix
    matrix["coverage_assessment"] = (
        "EVIDENCE_COMPLETE_PENDING_REVIEW"
        if _matrix_has_complete_coverage(coverage_matrix)
        else "EVIDENCE_INCOMPLETE"
    )
    matrix["coverage_status"] = "PENDING_HUMAN_REVIEW"
    return matrix


def review_coverage_matrix(
    matrix_path: Path,
    output_path: Path,
    *,
    reviewer: str | None = None,
    decision: str,
    rationale: str,
    resolutions_path: Path | None = None,
    reviewed_at: datetime | None = None,
) -> JsonObject:
    """Write an immutable review copy bound to the exact input matrix bytes."""

    if matrix_path.resolve() == output_path.resolve():
        raise ValueError("A revisão precisa ser gravada em arquivo separado da matriz de entrada.")
    if resolutions_path is not None and resolutions_path.resolve() == output_path.resolve():
        raise ValueError("O arquivo revisado não pode sobrescrever o arquivo de resoluções.")
    if output_path.exists():
        raise ValueError(
            "O arquivo de saída já existe; escolha um novo caminho para preservar a revisão."
        )
    if decision not in {"APPROVED", "REJECTED"}:
        raise ValueError("A decisão de revisão deve ser APPROVED ou REJECTED.")
    reviewer_value = None
    if reviewer is not None:
        reviewer_value = _sanitize_rationale(
            _bounded_required_text(reviewer, "reviewer", maximum=120)
        )
        if not reviewer_value:
            raise ValueError("A identificação informada para a pessoa revisora é inválida.")
    rationale_value = _sanitize_rationale(
        _bounded_required_text(rationale, "rationale", maximum=500)
    )
    if not rationale_value:
        raise ValueError("A justificativa da revisão é obrigatória.")

    matrix, matrix_bytes = _read_json_object(matrix_path, "matriz consolidada")
    _validate_matrix_for_review(matrix)
    review = cast(Mapping[str, object], matrix["matrix_review"])
    if review.get("state") != "PENDING_HUMAN_REVIEW":
        raise ValueError("Somente uma matriz pendente pode receber sua primeira revisão.")
    base_projection = _project_coverage_matrix(matrix, resolutions=())
    if matrix.get("coverage_matrix") != base_projection:
        raise ValueError("A matriz de cobertura diverge das tentativas consolidadas.")

    resolutions = _load_review_resolutions(resolutions_path)
    _validate_resolutions(matrix, resolutions)
    reviewed_matrix = dict(matrix)
    reviewed_matrix["coverage_matrix"] = _project_coverage_matrix(matrix, resolutions=resolutions)
    current_targets = _current_target_versions()
    targets_current = all(
        matrix.get(key) == current_targets[key]
        for key in ("catalog_version", "tpu_version", "preset_versions", "allowlist_version")
    )
    complete = targets_current and _matrix_has_complete_coverage(
        cast(list[JsonObject], reviewed_matrix["coverage_matrix"])
    )
    timestamp = reviewed_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("A data da revisão precisa incluir fuso horário.")
    review_record: JsonObject = {
        "state": "REVIEWED",
        "decision": decision,
        "reviewed_at": timestamp.astimezone(UTC).isoformat(),
        "rationale": rationale_value,
        "matrix_sha256": hashlib.sha256(matrix_bytes).hexdigest(),
        "resolutions": resolutions,
    }
    if reviewer_value is not None:
        review_record["reviewer"] = reviewer_value
    reviewed_matrix["matrix_review"] = review_record
    reviewed_matrix["coverage_assessment"] = (
        "EVIDENCE_COMPLETE" if complete else "EVIDENCE_INCOMPLETE"
    )
    if decision == "REJECTED":
        reviewed_matrix["coverage_status"] = "REJECTED_BY_REVIEW"
    elif complete:
        reviewed_matrix["coverage_status"] = "NATIONAL_COVERAGE_VALIDATED"
    else:
        reviewed_matrix["coverage_status"] = "APPROVED_PARTIAL"
    write_report(reviewed_matrix, output_path)
    return reviewed_matrix


def main(argv: Sequence[str] | None = None) -> int:
    """Offline CLI for merging batches and recording human review."""

    parser = argparse.ArgumentParser(
        description="Consolida relatórios DataJud locais e registra revisão sem chamadas HTTP."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    merge_parser = subparsers.add_parser("merge", help="Consolida um ou mais lotes JSON.")
    merge_parser.add_argument(
        "--batch",
        action="append",
        required=True,
        type=Path,
        help="Resumo batch JSON; repita para cada lote.",
    )
    merge_parser.add_argument("--output", required=True, type=Path, help="Matriz consolidada JSON.")
    review_parser = subparsers.add_parser("review", help="Grava uma cópia revisada da matriz.")
    review_parser.add_argument("--matrix", required=True, type=Path, help="Matriz pendente JSON.")
    review_parser.add_argument(
        "--output", required=True, type=Path, help="Novo arquivo revisado JSON."
    )
    review_parser.add_argument(
        "--reviewer", help="Identificação opcional da pessoa revisora; registrada se informada."
    )
    review_parser.add_argument(
        "--decision", required=True, choices=("APPROVED", "REJECTED"), help="Decisão explícita."
    )
    review_parser.add_argument("--rationale", required=True, help="Justificativa da revisão.")
    review_parser.add_argument(
        "--resolutions",
        type=Path,
        help="JSON com resoluções explícitas de conflitos por evidência VALIDATED.",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "merge":
            batch_paths = cast(list[Path], args.batch)
            matrix = merge_batch_reports(batch_paths)
            output_path = cast(Path, args.output)
            _protect_merge_inputs(output_path, batch_paths)
            write_report(matrix, output_path)
            print(f"Matriz consolidada: {output_path}")
        else:
            review_coverage_matrix(
                cast(Path, args.matrix),
                cast(Path, args.output),
                reviewer=cast(str | None, args.reviewer),
                decision=cast(str, args.decision),
                rationale=cast(str, args.rationale),
                resolutions_path=cast(Path | None, args.resolutions),
            )
            print(f"Cópia revisada da matriz: {args.output}")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 0


def _protect_merge_inputs(output_path: Path, batch_paths: Sequence[Path]) -> None:
    protected = {path.resolve() for path in batch_paths}
    for batch_path in batch_paths:
        batch, _ = _read_json_object(batch_path, "resumo batch")
        _validate_batch_summary(batch)
        run_id = cast(str, batch["run_id"])
        for reference in cast(list[JsonObject], batch["tribunal_reports"]):
            report_path = _resolve_batch_file(
                batch_path,
                cast(str, reference["alias"]),
                run_id,
                cast(str, reference["file_key"]),
            )
            protected.add(report_path)
    if output_path.resolve() in protected:
        raise ValueError("O arquivo de saída não pode sobrescrever evidência de entrada.")


def _read_json_object(path: Path, label: str) -> tuple[JsonObject, bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except OSError, json.JSONDecodeError:
        raise ValueError(f"Não foi possível ler {label} JSON: {path.name}.") from None
    if not isinstance(value, dict):
        raise ValueError(f"O conteúdo de {label} precisa ser um objeto JSON.")
    return cast(JsonObject, value), raw


def _canonical_sha256(value: Mapping[str, object]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_batch_summary(batch: Mapping[str, object]) -> None:
    if type(batch.get("report_version")) is not int or batch.get("report_version") != 1:
        raise ValueError("Versão de resumo batch não suportada.")
    if batch.get("kind") != "datajud_multitribunal_batch_summary":
        raise ValueError("O arquivo informado não é um resumo batch multitribunal.")
    run_id = _required_text(batch.get("run_id"), "run_id")
    try:
        uuid.UUID(run_id)
    except ValueError:
        raise ValueError("O resumo batch contém run_id inválido.") from None
    if batch.get("allowlist_version") != ALLOWLIST_VERSION:
        raise ValueError("Versão de allowlist divergente; o lote não pode ser combinado.")
    _required_text(batch.get("code_version"), "code_version")
    _required_text(batch.get("catalog_version"), "catalog_version")
    _timestamp_text(batch.get("started_at"), "started_at")
    request_count = batch.get("requests_made")
    if type(request_count) is not int or request_count < 0:
        raise ValueError("requests_made do resumo batch deve ser inteiro não negativo.")
    requested = batch.get("requested_aliases")
    if not isinstance(requested, list) or not 1 <= len(requested) <= MAX_TRIBUNALS_PER_BATCH:
        raise ValueError("O resumo batch deve conter de um a cinco aliases.")
    aliases = cast(list[object], requested)
    if any(not isinstance(alias, str) or alias not in TRIBUNAL_ALLOWLIST for alias in aliases):
        raise ValueError("O resumo batch contém alias fora da allowlist.")
    if len(set(aliases)) != len(aliases):
        raise ValueError("O resumo batch repete alias.")
    if type(batch.get("requested_count")) is not int or batch.get("requested_count") != len(
        aliases
    ):
        raise ValueError("requested_count diverge dos aliases listados.")
    references = batch.get("tribunal_reports")
    if not isinstance(references, list) or not all(isinstance(item, dict) for item in references):
        raise ValueError("O resumo batch não contém referências de relatório válidas.")
    for reference in cast(list[JsonObject], references):
        alias = reference.get("alias")
        file_key = reference.get("file_key")
        if not isinstance(alias, str) or alias not in TRIBUNAL_ALLOWLIST:
            raise ValueError("O resumo batch referencia alias fora da allowlist.")
        if not isinstance(file_key, str):
            raise ValueError("O resumo batch contém file_key inválido.")


def _resolve_batch_file(batch_path: Path, alias: str, run_id: str, file_key: str) -> Path:
    expected = f"tribunal-{alias}-{run_id}.json"
    if file_key != expected or Path(file_key).name != file_key:
        raise ValueError("file_key deve apontar para o relatório do alias/run_id no lote.")
    base = batch_path.parent.resolve()
    report_path = (base / file_key).resolve()
    if report_path.parent != base or report_path.name != file_key:
        raise ValueError("file_key deve permanecer no diretório do resumo batch.")
    return report_path


def _validate_endpoint_report(
    report: Mapping[str, object], alias: str, run_id: str, batch: Mapping[str, object]
) -> None:
    if type(report.get("report_version")) is not int or report.get("report_version") != 1:
        raise ValueError(f"Relatório de {alias} usa versão não suportada.")
    if report.get("kind") != "datajud_multitribunal_endpoint_report":
        raise ValueError(f"Relatório de {alias} tem tipo inválido.")
    if report.get("alias") != alias or report.get("run_id") != run_id:
        raise ValueError(f"Alias/run_id do relatório não corresponde ao resumo batch para {alias}.")
    endpoint = TRIBUNAL_ALLOWLIST[alias]
    if (
        report.get("canonical_tribunal") != endpoint.canonical_tribunal
        or report.get("justice") != endpoint.justice
        or report.get("endpoint") != endpoint.endpoint_url
    ):
        raise ValueError(f"Metadados de endpoint de {alias} divergem da allowlist.")
    for key in ("code_version", "allowlist_version", "catalog_version"):
        _required_text(report.get(key), key)
        if report.get(key) != batch.get(key):
            raise ValueError(f"{key} diverge entre resumo e relatório de {alias}.")
    _timestamp_text(report.get("started_at"), "started_at")
    _required_text(report.get("environment"), "environment")
    terms_review = report.get("terms_review")
    if not isinstance(terms_review, Mapping) or not isinstance(
        terms_review.get("operator_confirmed_before_probe"), bool
    ):
        raise ValueError(f"Relatório de {alias} não contém confirmação de termos válida.")
    terms_confirmed = terms_review["operator_confirmed_before_probe"] is True
    s1 = report.get("s1_s2")
    if not isinstance(s1, Mapping):
        raise ValueError(f"Relatório de {alias} não contém S1/S2.")
    capabilities = s1.get("capabilities")
    if not isinstance(capabilities, Mapping) or set(capabilities) != set(_CAPABILITY_NAMES):
        raise ValueError(f"Relatório de {alias} não contém as dez capabilities S1/S2.")
    normalized_states: dict[str, str] = {}
    for name in _CAPABILITY_NAMES:
        item = capabilities[name]
        if not isinstance(item, Mapping):
            raise ValueError(f"Capability {name} em {alias} tem formato inválido.")
        state = _state_value(item.get("state"), f"{alias}.{name}")
        if state == EvidenceState.NOT_APPLICABLE:
            raise ValueError(f"Capability {name} em {alias} não aceita NOT_APPLICABLE.")
        normalized_states[name] = state
    calculated_s1 = _aggregate_capabilities(
        {name: {"state": state} for name, state in normalized_states.items()}
    )
    if s1.get("aggregate_state") != calculated_s1:
        raise ValueError(f"aggregate_state S1/S2 diverge das capabilities de {alias}.")
    s1_requests = s1.get("requests")
    s1_request_count = s1.get("requests_made")
    if (
        not isinstance(s1_requests, list)
        or type(s1_request_count) is not int
        or s1_request_count != len(s1_requests)
        or s1_request_count < 0
    ):
        raise ValueError(f"Contagem de requisições S1/S2 inválida para {alias}.")
    has_observed_s1_state = any(
        state in {EvidenceState.VALIDATED, EvidenceState.INCOMPATIBLE}
        for state in normalized_states.values()
    )
    if has_observed_s1_state and (
        report.get("environment") != "real" or not terms_confirmed or s1_request_count == 0
    ):
        raise ValueError(f"Evidência S1/S2 de {alias} não prova chamada real autorizada.")

    pairs = report.get("s5_pairs")
    if not isinstance(pairs, list) or len(pairs) != len(PRESET_IDS):
        raise ValueError(f"Relatório de {alias} não contém os quatro pares S5.")
    seen: set[str] = set()
    for raw_pair in cast(list[object], pairs):
        if not isinstance(raw_pair, Mapping):
            raise ValueError(f"Par S5 em {alias} tem formato inválido.")
        preset_id = raw_pair.get("preset_id")
        if not isinstance(preset_id, str) or preset_id not in PRESET_IDS or preset_id in seen:
            raise ValueError(f"Relatório de {alias} contém preset inválido ou repetido.")
        seen.add(preset_id)
        _required_text(raw_pair.get("preset_version"), "preset_version")
        if raw_pair.get("catalog_version") != report.get("catalog_version"):
            raise ValueError(f"catalog_version S5 diverge do relatório de {alias}.")
        applicability = raw_pair.get("applicability")
        if not isinstance(applicability, Mapping):
            raise ValueError(f"Aplicabilidade S5 de {alias}/{preset_id} é inválida.")
        applicability_state = applicability.get("state")
        if applicability_state not in {
            EvidenceState.APPLICABLE,
            EvidenceState.NOT_APPLICABLE,
            EvidenceState.INCONCLUSIVE,
        }:
            raise ValueError(f"Estado de aplicabilidade S5 de {alias}/{preset_id} é inválido.")
        tpu_version = applicability.get("tpu_version")
        if applicability_state in {EvidenceState.APPLICABLE, EvidenceState.NOT_APPLICABLE}:
            _required_text(tpu_version, "tpu_version")
            if _validate_official_reference(applicability.get("reference_url")) is None:
                raise ValueError(
                    f"Aplicabilidade de {alias}/{preset_id} não tem referência oficial."
                )
        elif tpu_version is not None and not isinstance(tpu_version, str):
            raise ValueError(f"tpu_version de {alias}/{preset_id} é inválida.")
        query_status = raw_pair.get("query_status")
        sample_status = raw_pair.get("sample_status")
        if not isinstance(query_status, Mapping) or not isinstance(sample_status, Mapping):
            raise ValueError(
                f"Estados de consulta/amostra S5 de {alias}/{preset_id} são inválidos."
            )
        query_state = _state_value(query_status.get("state"), "query_status")
        sample_state = _state_value(sample_status.get("state"), "sample_status")
        pair_state = _state_value(raw_pair.get("pair_state"), "pair_state")
        calculated_pair = _aggregate_pair(
            cast(str, applicability_state), {"state": query_state}, {"state": sample_state}
        )
        if pair_state != calculated_pair:
            raise ValueError(f"pair_state diverge da evidência S5 de {alias}/{preset_id}.")
        pair_requests = raw_pair.get("requests")
        pair_request_count = raw_pair.get("requests_made")
        if (
            not isinstance(pair_requests, list)
            or type(pair_request_count) is not int
            or pair_request_count != len(pair_requests)
            or pair_request_count < 0
        ):
            raise ValueError(f"Contagem de requisições S5 inválida para {alias}/{preset_id}.")
        has_observed_s5_state = any(
            state in {EvidenceState.VALIDATED, EvidenceState.INCOMPATIBLE}
            for state in (query_state, sample_state)
        )
        if has_observed_s5_state and (
            report.get("environment") != "real"
            or not terms_confirmed
            or pair_request_count == 0
            or applicability_state != EvidenceState.APPLICABLE
        ):
            raise ValueError(
                f"Evidência S5 de {alias}/{preset_id} não prova chamada real autorizada."
            )
    if seen != set(PRESET_IDS):
        raise ValueError(f"Relatório de {alias} omite pares S5.")
    pair_request_total = sum(
        cast(int, cast(Mapping[str, object], pair)["requests_made"])
        for pair in cast(list[object], pairs)
    )
    total_requests = report.get("requests_made")
    if (
        type(total_requests) is not int
        or total_requests < 0
        or total_requests != s1_request_count + pair_request_total
    ):
        raise ValueError(f"requests_made total diverge das requisições de {alias}.")


def _normalize_attempt(
    report: Mapping[str, object],
    *,
    run_id: str,
    batch_file: str,
    batch_sha256: str,
    report_file: str,
    report_sha256: str,
) -> JsonObject:
    s1 = cast(Mapping[str, object], report["s1_s2"])
    capabilities = cast(Mapping[str, Mapping[str, object]], s1["capabilities"])
    pair_map: dict[str, object] = {}
    for pair_value in cast(list[object], report["s5_pairs"]):
        pair = cast(Mapping[str, object], pair_value)
        applicability = cast(Mapping[str, object], pair["applicability"])
        query_status = cast(Mapping[str, object], pair["query_status"])
        sample_status = cast(Mapping[str, object], pair["sample_status"])
        pair_map[cast(str, pair["preset_id"])] = {
            "pair_state": pair["pair_state"],
            "query_state": query_status["state"],
            "sample_state": sample_status["state"],
            "applicability_state": applicability["state"],
            "tpu_version": applicability.get("tpu_version"),
            "reference_url": applicability.get("reference_url"),
            "catalog_version": pair["catalog_version"],
            "preset_version": pair["preset_version"],
        }
    return {
        "alias": report["alias"],
        "run_id": run_id,
        "started_at": report["started_at"],
        "code_version": report["code_version"],
        "allowlist_version": report["allowlist_version"],
        "catalog_version": report["catalog_version"],
        "batch_file": batch_file,
        "batch_sha256": batch_sha256,
        "report_file": report_file,
        "report_sha256": report_sha256,
        "s1_s2": {
            "aggregate_state": s1["aggregate_state"],
            "capabilities": {name: capabilities[name]["state"] for name in _CAPABILITY_NAMES},
        },
        "s5_pairs": pair_map,
    }


def _project_coverage_matrix(
    matrix: Mapping[str, object], *, resolutions: Sequence[Mapping[str, object]]
) -> list[JsonObject]:
    attempts = cast(list[JsonObject], matrix["attempts"])
    preset_versions = cast(dict[str, str], matrix["preset_versions"])
    catalog_version = cast(str, matrix["catalog_version"])
    tpu_version = cast(str, matrix["tpu_version"])
    resolution_map = {_resolution_key(item): item for item in resolutions}
    rows: list[JsonObject] = []
    for alias, endpoint in TRIBUNAL_ALLOWLIST.items():
        alias_attempts = [item for item in attempts if item.get("alias") == alias]
        capabilities: dict[str, object] = {}
        capability_states: list[str] = []
        for capability in _CAPABILITY_NAMES:
            observed: list[tuple[JsonObject, str]] = []
            for attempt in alias_attempts:
                s1 = cast(Mapping[str, object], attempt["s1_s2"])
                state = cast(str, cast(Mapping[str, object], s1["capabilities"])[capability])
                observed.append((attempt, state))
            states = [state for _, state in observed]
            resolution = resolution_map.get(f"s1_s2:{alias}:{capability}")
            state = _rollup_evidence_states(states)
            if resolution is not None:
                state = EvidenceState.VALIDATED
            capability_states.append(state)
            capabilities[capability] = {
                "state": state,
                "attempt_count": len(observed),
                "attempts": [
                    _attempt_reference(attempt, state=attempt_state)
                    for attempt, attempt_state in observed
                ],
                "resolution": dict(resolution) if resolution is not None else None,
            }
        if EvidenceState.INCOMPATIBLE in capability_states:
            s1_state = EvidenceState.INCOMPATIBLE
        elif all(state == EvidenceState.VALIDATED for state in capability_states):
            s1_state = EvidenceState.VALIDATED
        elif all(state == EvidenceState.NOT_RUN for state in capability_states):
            s1_state = EvidenceState.NOT_RUN
        else:
            s1_state = EvidenceState.INCONCLUSIVE

        s5_matrix: dict[str, object] = {}
        for preset_id in PRESET_IDS:
            pair_attempts: list[tuple[JsonObject, Mapping[str, object], list[str]]] = []
            for attempt in alias_attempts:
                pair = cast(
                    Mapping[str, object], cast(Mapping[str, object], attempt["s5_pairs"])[preset_id]
                )
                stale_reasons = _s5_stale_reasons(
                    pair,
                    catalog_version=catalog_version,
                    preset_version=preset_versions[preset_id],
                    tpu_version=tpu_version,
                )
                pair_attempts.append((attempt, pair, stale_reasons))
            current = [item for item in pair_attempts if not item[2]]
            states = [cast(str, pair["pair_state"]) for _, pair, _ in current]
            resolution = resolution_map.get(f"s5:{alias}:{preset_id}")
            pair_state = _rollup_evidence_states(states, allow_not_applicable=True)
            if resolution is not None:
                pair_state = EvidenceState.VALIDATED
            applicability_states = {
                cast(str, pair["applicability_state"]) for _, pair, _ in current
            }
            if not current:
                applicability_state = EvidenceState.NOT_RUN
            elif len(applicability_states) == 1:
                applicability_state = next(iter(applicability_states))
            else:
                applicability_state = EvidenceState.INCONCLUSIVE
            s5_matrix[preset_id] = {
                "current_catalog_version": catalog_version,
                "current_preset_version": preset_versions[preset_id],
                "current_tpu_version": tpu_version,
                "applicability_state": applicability_state,
                "pair_state": pair_state,
                "attempt_count": len(pair_attempts),
                "current_attempt_count": len(current),
                "attempts": [
                    {
                        **_attempt_reference(attempt, state=pair["pair_state"]),
                        "catalog_version": pair["catalog_version"],
                        "preset_version": pair["preset_version"],
                        "tpu_version": pair["tpu_version"],
                        "applicability_state": pair["applicability_state"],
                        "current_for_coverage": not stale_reasons,
                        "stale_reasons": stale_reasons,
                    }
                    for attempt, pair, stale_reasons in pair_attempts
                ],
                "resolution": dict(resolution) if resolution is not None else None,
            }
        rows.append(
            {
                "alias": alias,
                "canonical_tribunal": endpoint.canonical_tribunal,
                "justice": endpoint.justice,
                "s1_s2_state": s1_state,
                "s1_s2_capabilities": capabilities,
                "s5_pairs": s5_matrix,
            }
        )
    return rows


def _attempt_reference(attempt: Mapping[str, object], *, state: object) -> JsonObject:
    return {
        "run_id": attempt["run_id"],
        "started_at": attempt["started_at"],
        "state": state,
        "batch_file": attempt["batch_file"],
        "batch_sha256": attempt["batch_sha256"],
        "report_file": attempt["report_file"],
        "report_sha256": attempt["report_sha256"],
    }


def _s5_stale_reasons(
    pair: Mapping[str, object], *, catalog_version: str, preset_version: str, tpu_version: str
) -> list[str]:
    reasons: list[str] = []
    if pair.get("catalog_version") != catalog_version:
        reasons.append("CATALOG_VERSION_CHANGED")
    if pair.get("preset_version") != preset_version:
        reasons.append("PRESET_VERSION_CHANGED")
    if (
        pair.get("applicability_state") in {EvidenceState.APPLICABLE, EvidenceState.NOT_APPLICABLE}
        and pair.get("tpu_version") != tpu_version
    ):
        reasons.append("TPU_VERSION_CHANGED")
    return reasons


def _rollup_evidence_states(states: Sequence[str], *, allow_not_applicable: bool = False) -> str:
    if not states:
        return EvidenceState.NOT_RUN
    values = set(states)
    if EvidenceState.INCOMPATIBLE in values:
        return EvidenceState.INCOMPATIBLE
    if EvidenceState.NOT_APPLICABLE in values:
        if allow_not_applicable and values == {EvidenceState.NOT_APPLICABLE}:
            return EvidenceState.NOT_APPLICABLE
        return EvidenceState.INCOMPATIBLE
    if EvidenceState.VALIDATED in values:
        return EvidenceState.VALIDATED
    if EvidenceState.INCONCLUSIVE in values:
        return EvidenceState.INCONCLUSIVE
    return EvidenceState.NOT_RUN


def _matrix_has_complete_coverage(matrix_rows: Sequence[Mapping[str, object]]) -> bool:
    if len(matrix_rows) != len(TRIBUNAL_ALLOWLIST):
        return False
    rows = {cast(str, row.get("alias")): row for row in matrix_rows}
    if set(rows) != set(TRIBUNAL_ALLOWLIST):
        return False
    for alias in TRIBUNAL_ALLOWLIST:
        row = rows[alias]
        capabilities = cast(Mapping[str, object], row.get("s1_s2_capabilities", {}))
        if set(capabilities) != set(_CAPABILITY_NAMES):
            return False
        if any(
            cast(Mapping[str, object], capabilities[name]).get("state") != EvidenceState.VALIDATED
            for name in _CAPABILITY_NAMES
        ):
            return False
        pairs = cast(Mapping[str, object], row.get("s5_pairs", {}))
        if set(pairs) != set(PRESET_IDS):
            return False
        if any(
            cast(Mapping[str, object], pair).get("pair_state")
            not in {EvidenceState.VALIDATED, EvidenceState.NOT_APPLICABLE}
            for pair in pairs.values()
        ):
            return False
    return True


def _validate_matrix_for_review(matrix: Mapping[str, object]) -> None:
    if type(matrix.get("report_version")) is not int or matrix.get("report_version") != 1:
        raise ValueError("Versão de matriz não suportada.")
    if matrix.get("kind") != "datajud_multitribunal_coverage_matrix":
        raise ValueError("O arquivo informado não é uma matriz consolidada SPEC-024.")
    if matrix.get("allowlist_version") != ALLOWLIST_VERSION:
        raise ValueError("A matriz usa uma allowlist diferente da versão atual.")
    _required_text(matrix.get("catalog_version"), "catalog_version")
    _required_text(matrix.get("tpu_version"), "tpu_version")
    preset_versions = matrix.get("preset_versions")
    if not isinstance(preset_versions, Mapping) or set(preset_versions) != set(PRESET_IDS):
        raise ValueError("A matriz não contém versões dos quatro presets atuais.")
    for version in preset_versions.values():
        _required_text(version, "preset_version")
    attempts = matrix.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("A matriz não contém tentativas consolidadas.")
    unique_attempts: set[tuple[str, str]] = set()
    for raw_attempt in cast(list[object], attempts):
        if not isinstance(raw_attempt, Mapping):
            raise ValueError("A matriz contém tentativa em formato inválido.")
        alias = raw_attempt.get("alias")
        if not isinstance(alias, str) or alias not in TRIBUNAL_ALLOWLIST:
            raise ValueError("A matriz contém tentativa fora da allowlist.")
        run_id = _required_text(raw_attempt.get("run_id"), "run_id")
        try:
            uuid.UUID(run_id)
        except ValueError:
            raise ValueError("A matriz contém run_id inválido.") from None
        if (run_id, alias) in unique_attempts:
            raise ValueError("A matriz contém tentativa duplicada para run_id/alias.")
        unique_attempts.add((run_id, alias))
        if raw_attempt.get("allowlist_version") != ALLOWLIST_VERSION:
            raise ValueError("Uma tentativa da matriz usa allowlist divergente.")
        for key in ("started_at", "code_version", "catalog_version"):
            _required_text(raw_attempt.get(key), key)
        _timestamp_text(raw_attempt.get("started_at"), "started_at")
        expected_batch = f"batch-{run_id}.json"
        expected_report = f"tribunal-{alias}-{run_id}.json"
        if (
            raw_attempt.get("batch_file") != expected_batch
            or raw_attempt.get("report_file") != expected_report
        ):
            raise ValueError("A matriz contém referência de arquivo de evidência inválida.")
        for key in ("batch_sha256", "report_sha256"):
            digest = _required_text(raw_attempt.get(key), key)
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"{key} precisa ser SHA-256 hexadecimal.")
        s1 = raw_attempt.get("s1_s2")
        pairs = raw_attempt.get("s5_pairs")
        if not isinstance(s1, Mapping) or not isinstance(pairs, Mapping):
            raise ValueError("A matriz contém evidência S1/S2 ou S5 inválida.")
        capabilities = s1.get("capabilities")
        if not isinstance(capabilities, Mapping) or set(capabilities) != set(_CAPABILITY_NAMES):
            raise ValueError("A matriz contém capabilities inválidas.")
        s1_states: dict[str, dict[str, object]] = {}
        for name, state in capabilities.items():
            if _state_value(state, "capability") == EvidenceState.NOT_APPLICABLE:
                raise ValueError("Capability S1/S2 não aceita NOT_APPLICABLE.")
            s1_states[cast(str, name)] = {"state": state}
        if s1.get("aggregate_state") != _aggregate_capabilities(s1_states):
            raise ValueError("aggregate_state S1/S2 da matriz diverge das capabilities.")
        if set(pairs) != set(PRESET_IDS):
            raise ValueError("A matriz contém pares S5 inválidos.")
        for preset_id, raw_pair in pairs.items():
            if not isinstance(raw_pair, Mapping):
                raise ValueError("A matriz contém par S5 inválido.")
            pair_state = _state_value(raw_pair.get("pair_state"), "pair_state")
            applicability_state = raw_pair.get("applicability_state")
            if applicability_state not in {
                EvidenceState.APPLICABLE,
                EvidenceState.NOT_APPLICABLE,
                EvidenceState.INCONCLUSIVE,
            }:
                raise ValueError("Estado de applicability_state desconhecido.")
            query_state = _state_value(raw_pair.get("query_state"), "query_state")
            sample_state = _state_value(raw_pair.get("sample_state"), "sample_state")
            calculated_pair = _aggregate_pair(
                cast(str, applicability_state),
                {"state": query_state},
                {"state": sample_state},
            )
            if pair_state != calculated_pair:
                raise ValueError(f"pair_state da matriz diverge para {alias}/{preset_id}.")
            _required_text(raw_pair.get("catalog_version"), "catalog_version")
            _required_text(raw_pair.get("preset_version"), "preset_version")
            tpu_version = raw_pair.get("tpu_version")
            if applicability_state in {EvidenceState.APPLICABLE, EvidenceState.NOT_APPLICABLE}:
                _required_text(tpu_version, "tpu_version")
                if _validate_official_reference(raw_pair.get("reference_url")) is None:
                    raise ValueError(
                        f"Aplicabilidade de {alias}/{preset_id} não tem referência oficial."
                    )
            elif tpu_version is not None and not isinstance(tpu_version, str):
                raise ValueError(f"tpu_version de {alias}/{preset_id} é inválida.")


def _load_review_resolutions(path: Path | None) -> list[JsonObject]:
    if path is None:
        return []
    document, _ = _read_json_object(path, "resoluções")
    if type(document.get("schema_version")) is not int or document.get("schema_version") != 1:
        raise ValueError("O arquivo de resoluções precisa usar schema_version=1.")
    raw_resolutions = document.get("resolutions")
    if not isinstance(raw_resolutions, list):
        raise ValueError("O arquivo de resoluções precisa conter resolutions como lista.")
    resolutions: list[JsonObject] = []
    for raw in cast(list[object], raw_resolutions):
        if not isinstance(raw, Mapping):
            raise ValueError("A resolução precisa ser um objeto.")
        alias = raw.get("alias")
        target = raw.get("target")
        if not isinstance(alias, str) or alias not in TRIBUNAL_ALLOWLIST:
            raise ValueError("A resolução contém alias fora da allowlist.")
        if target == "s1_s2":
            capability = raw.get("capability")
            if not isinstance(capability, str) or capability not in _CAPABILITY_NAMES:
                raise ValueError("Resolução S1/S2 precisa indicar capability conhecida.")
            target_fields: JsonObject = {"capability": capability}
        elif target == "s5":
            preset_id = raw.get("preset_id")
            if not isinstance(preset_id, str) or preset_id not in PRESET_IDS:
                raise ValueError("Resolução S5 precisa indicar preset conhecido.")
            target_fields = {"preset_id": preset_id}
        else:
            raise ValueError("A resolução precisa selecionar target s1_s2 ou s5.")
        resolutions.append(
            {
                "alias": alias,
                "target": target,
                **target_fields,
                "selected_run_id": _required_text(raw.get("selected_run_id"), "selected_run_id"),
                "rationale": _sanitize_rationale(
                    _bounded_required_text(raw.get("rationale"), "rationale", maximum=500)
                ),
            }
        )
    keys = [_resolution_key(item) for item in resolutions]
    if len(set(keys)) != len(keys):
        raise ValueError("O arquivo contém resoluções repetidas para o mesmo alvo.")
    return resolutions


def _resolution_key(resolution: Mapping[str, object]) -> str:
    if resolution.get("target") == "s1_s2":
        return f"s1_s2:{resolution.get('alias')}:{resolution.get('capability')}"
    return f"s5:{resolution.get('alias')}:{resolution.get('preset_id')}"


def _validate_resolutions(
    matrix: Mapping[str, object], resolutions: Sequence[Mapping[str, object]]
) -> None:
    rows = {
        cast(str, row["alias"]): row for row in _project_coverage_matrix(matrix, resolutions=())
    }
    attempts = cast(list[JsonObject], matrix["attempts"])
    preset_versions = cast(Mapping[str, str], matrix["preset_versions"])
    for resolution in resolutions:
        alias = cast(str, resolution["alias"])
        run_id = cast(str, resolution["selected_run_id"])
        row = rows[alias]
        if resolution.get("target") == "s1_s2":
            capability = cast(str, resolution["capability"])
            current = cast(Mapping[str, object], row["s1_s2_capabilities"])[capability]
            if cast(Mapping[str, object], current).get("state") != EvidenceState.INCOMPATIBLE:
                raise ValueError("Só é possível resolver um alvo S1/S2 em conflito incompatível.")
            eligible = any(
                attempt.get("alias") == alias
                and attempt.get("run_id") == run_id
                and cast(
                    Mapping[str, object],
                    cast(Mapping[str, object], attempt["s1_s2"])["capabilities"],
                ).get(capability)
                == EvidenceState.VALIDATED
                for attempt in attempts
            )
        else:
            preset_id = cast(str, resolution["preset_id"])
            current = cast(Mapping[str, object], row["s5_pairs"])[preset_id]
            if cast(Mapping[str, object], current).get("pair_state") != EvidenceState.INCOMPATIBLE:
                raise ValueError("Só é possível resolver um alvo S5 em conflito incompatível.")
            eligible = any(
                attempt.get("alias") == alias
                and attempt.get("run_id") == run_id
                and _attempt_has_current_validated_pair(
                    attempt,
                    preset_id,
                    cast(str, matrix["catalog_version"]),
                    preset_versions[preset_id],
                    cast(str, matrix["tpu_version"]),
                )
                for attempt in attempts
            )
        if not eligible:
            raise ValueError(f"run_id {run_id} não tem evidência VALIDATED atual para {alias}.")


def _attempt_has_current_validated_pair(
    attempt: Mapping[str, object],
    preset_id: str,
    catalog_version: str,
    preset_version: str,
    tpu_version: str,
) -> bool:
    pair = cast(Mapping[str, object], cast(Mapping[str, object], attempt["s5_pairs"])[preset_id])
    return pair.get("pair_state") == EvidenceState.VALIDATED and not _s5_stale_reasons(
        pair,
        catalog_version=catalog_version,
        preset_version=preset_version,
        tpu_version=tpu_version,
    )


def _current_target_versions() -> JsonObject:
    catalog = load_catalog()
    return {
        "catalog_version": catalog.catalog_version,
        "tpu_version": catalog.tpu_version,
        "preset_versions": {item.id: item.version for item in current_discovery_presets()},
        "allowlist_version": ALLOWLIST_VERSION,
    }


def _state_value(value: object, label: str) -> str:
    if not isinstance(value, str) or value not in _VALID_STATES:
        raise ValueError(f"Estado de {label} desconhecido.")
    return value


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} precisa ser texto não vazio.")
    return value.strip()


def _timestamp_text(value: object, label: str) -> str:
    result = _required_text(value, label)
    try:
        parsed = datetime.fromisoformat(result)
    except ValueError:
        raise ValueError(f"{label} precisa ser uma data ISO-8601.") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{label} precisa incluir fuso horário.")
    return result


def _bounded_required_text(value: object, label: str, *, maximum: int) -> str:
    result = _required_text(value, label)
    if len(result) > maximum:
        raise ValueError(f"{label} excede {maximum} caracteres.")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
