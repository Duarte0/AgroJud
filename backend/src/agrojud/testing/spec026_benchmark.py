"""Isolated PostgreSQL benchmark for SPEC-026 page-size decisions."""

from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import subprocess
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.services.collection import CollectionJobHandler, build_collection_request
from agrojud.services.job_worker import LeasedWorker
from agrojud.services.jobs import JobService, PageCommitResult
from agrojud.sources.contracts import (
    DEFAULT_PAGE_SIZE,
    Cursor,
    SourcePage,
    SourceQuery,
    parse_source_page,
)

HIT_COUNT = 25_001
PAGE_PROFILES = (1_000, 5_000, 10_000)
REPETITIONS = 3


class GeneratedPageAdapter:
    """Generate one bounded JSON page at a time and report serialized response bytes."""

    def __init__(self) -> None:
        self.request_latencies: list[float] = []
        self.response_bytes = 0
        self.hit_counts: list[int] = []

    def fetch_page(
        self,
        _query: SourceQuery,
        cursor: Cursor | None,
        page_size: int,
    ) -> SourcePage:
        started = time.perf_counter()
        start_index = 0 if cursor is None else int(str(cursor[1]).rsplit("-", 1)[1]) + 1
        stop_index = min(start_index + page_size, HIT_COUNT)
        hits: list[dict[str, object]] = []
        for index in range(start_index, stop_index):
            source_id = f"benchmark-{index:05d}"
            timestamp = (
                datetime(2026, 1, 1, tzinfo=UTC) + timedelta(milliseconds=index)
            ).isoformat()
            hits.append(
                {
                    "_id": source_id,
                    "_source": {
                        "id": source_id,
                        "numeroProcesso": f"{index + 1:020d}",
                        "tribunal": "TJGO",
                        "@timestamp": timestamp,
                        "movimentos": [],
                    },
                    "sort": [timestamp, source_id],
                }
            )
        response: dict[str, object] = {
            "hits": {
                "total": {"value": 10_000, "relation": "gte"},
                "hits": hits,
            }
        }
        self.response_bytes += len(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        self.hit_counts.append(len(hits))
        page = parse_source_page(response, datetime.now(UTC))
        self.request_latencies.append(time.perf_counter() - started)
        return page

    def fetch_by_case_number(
        self,
        process_number: str,
        cursor: Cursor | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> SourcePage:
        return self.fetch_page(SourceQuery(process_number=process_number), cursor, page_size)


class MeasuredCollectionHandler(CollectionJobHandler):
    def __init__(self, adapter: GeneratedPageAdapter) -> None:
        super().__init__(lambda _lease: adapter)

    @staticmethod
    def _validate_page(
        query: SourceQuery,
        previous_cursor: Cursor | None,
        requested_size: int,
        page: SourcePage,
    ) -> None:
        started = time.perf_counter()
        CollectionJobHandler._validate_page(query, previous_cursor, requested_size, page)
        _ACTIVE_METRICS["validation"] += time.perf_counter() - started


_ACTIVE_METRICS: dict[str, float] = {"validation": 0.0, "ingestion_checkpoint": 0.0}


class MeasuredJobService(JobService):
    def __init__(self, sessions: sessionmaker[Session], metrics: dict[str, float]) -> None:
        super().__init__(sessions)
        self.metrics = metrics

    def commit_page(
        self,
        job_id: UUID,
        possession_token: UUID,
        *,
        expected_revision: int,
        budget_limit: int | None,
        source: Literal["datajud", "synthetic"],
        page: SourcePage,
        ownership_lost: Event | None = None,
    ) -> PageCommitResult:
        started = time.perf_counter()
        try:
            return super().commit_page(
                job_id,
                possession_token,
                expected_revision=expected_revision,
                budget_limit=budget_limit,
                source=source,
                page=page,
                ownership_lost=ownership_lost,
            )
        finally:
            self.metrics["ingestion_checkpoint"] += time.perf_counter() - started


class PeakResidentMemory:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self.peak_bytes = 0
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def start(self) -> None:
        self._read_current()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def _sample(self) -> None:
        while not self._stop.is_set():
            self._read_current()
            if self._stop.wait(0.01):
                return

    def _read_current(self) -> None:
        try:
            with open("/proc/self/status", encoding="ascii") as status_file:
                for line in status_file:
                    if line.startswith("VmRSS:"):
                        self.peak_bytes = max(self.peak_bytes, int(line.split()[1]) * 1024)
                        return
        except FileNotFoundError, PermissionError:
            self.peak_bytes = max(
                self.peak_bytes,
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            )


def _memory_limit_bytes() -> int | None:
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            value = Path(path).read_text(encoding="ascii").strip()
        except OSError:
            continue
        if value == "max":
            return None
        limit = int(value)
        return limit if limit < 1 << 60 else None
    return None


def _database_size(engine: Any) -> int:
    with Session(engine) as session:
        result = session.scalar(select(func.pg_database_size(func.current_database())))
    if not isinstance(result, int):
        raise RuntimeError("PostgreSQL não retornou o tamanho do banco de benchmark.")
    return result


def _run_one(page_size: int, repetition: int) -> dict[str, Any]:
    template_value = os.environ.get("TEST_DATABASE_URL")
    if not template_value:
        raise RuntimeError("TEST_DATABASE_URL é obrigatória para o benchmark isolado.")
    template = make_url(template_value)
    database_name = f"agrojud_spec026_{uuid4().hex}_test"
    admin_engine = create_engine(
        template.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    database_created = False
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        database_created = True
        engine = make_engine(
            template.set(database=database_name).render_as_string(hide_password=False)
        )
        with engine.begin() as connection:
            upgrade_database(connection)
        metrics = {"validation": 0.0, "ingestion_checkpoint": 0.0}
        _ACTIVE_METRICS.update(metrics)
        jobs = MeasuredJobService(sessionmaker(engine, expire_on_commit=False), metrics)
        query = SourceQuery(
            filed_from=date(2026, 1, 1),
            filed_to=date(2026, 2, 1),
        )
        request = build_collection_request(
            mode="demo",
            source="synthetic",
            job_type="discovery",
            query=query,
            page_size=page_size,
            hit_budget=None,
        )
        created = jobs.enqueue(request)
        adapter = GeneratedPageAdapter()
        handler = MeasuredCollectionHandler(adapter)
        worker = LeasedWorker(
            jobs,
            worker_id=f"spec026-benchmark-{page_size}-{repetition}",
            handlers={"discovery": handler},
            heartbeat_interval=1,
        )
        peak_memory = PeakResidentMemory()
        cpu_started = time.process_time()
        wall_started = time.perf_counter()
        peak_memory.start()
        try:
            if not worker.run_once():
                raise RuntimeError("O worker não reservou o job sintético de benchmark.")
        finally:
            peak_memory.stop()
        wall_seconds = time.perf_counter() - wall_started
        cpu_seconds = time.process_time() - cpu_started
        inspection = jobs.inspect(created.job_id)
        database_bytes = _database_size(engine)
        engine.dispose()
        coverage = inspection.coverage or {}
        request_latencies_ms = [value * 1000 for value in adapter.request_latencies]
        result = {
            "page_size": page_size,
            "repetition": repetition,
            "hit_count": coverage.get("hits_confirmed"),
            "status": inspection.status,
            "query_status": coverage.get("query_status"),
            "budget_limit": coverage.get("budget_limit"),
            "source_total_value": coverage.get("source_total", {}).get("value"),
            "source_total_relation": coverage.get("source_total", {}).get("relation"),
            "request_count": len(adapter.request_latencies),
            "retry_count": 0,
            "response_bytes_simulated": adapter.response_bytes,
            "request_latency_p50_ms": statistics.median(request_latencies_ms),
            "request_latency_p95_ms": _percentile(request_latencies_ms, 0.95),
            "parse_validation_seconds": sum(adapter.request_latencies) + metrics["validation"],
            "ingestion_checkpoint_seconds": metrics["ingestion_checkpoint"],
            "worker_wall_seconds": wall_seconds,
            "hits_per_minute": int(HIT_COUNT / wall_seconds * 60),
            "worker_cpu_seconds": cpu_seconds,
            "worker_peak_rss_bytes": peak_memory.peak_bytes,
            "database_bytes": database_bytes,
            "page_hit_counts": adapter.hit_counts,
            "memory_limit_bytes": _memory_limit_bytes(),
        }
        if (
            inspection.status != "completed"
            or coverage.get("hits_confirmed") != HIT_COUNT
            or coverage.get("budget_limit") is not None
            or coverage.get("source_total", {}).get("relation") != "gte"
        ):
            raise RuntimeError(f"A execução não completou os critérios de integridade: {result}")
        return result
    finally:
        admin_engine.dispose()
        if database_created:
            with admin_engine.connect() as connection:
                connection.exec_driver_sql(
                    f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'
                )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def _benchmark_assessment(runs: list[dict[str, Any]], memory_limit: int | None) -> dict[str, Any]:
    summaries: dict[str, dict[str, float]] = {}
    integrity: dict[str, str] = {}
    for page_size in PAGE_PROFILES:
        profile_runs = [run for run in runs if run.get("page_size") == page_size]
        if len(profile_runs) != REPETITIONS:
            integrity[str(page_size)] = "INCONCLUSIVE"
            continue
        summaries[str(page_size)] = {
            "mean_hits_per_minute": statistics.mean(run["hits_per_minute"] for run in profile_runs),
            "mean_worker_wall_seconds": statistics.mean(
                run["worker_wall_seconds"] for run in profile_runs
            ),
            "mean_worker_peak_rss_bytes": statistics.mean(
                run["worker_peak_rss_bytes"] for run in profile_runs
            ),
        }
        expected_pages = [page_size] * (HIT_COUNT // page_size)
        remainder = HIT_COUNT % page_size
        if remainder:
            expected_pages.append(remainder)
        expected_pages.append(0)
        if memory_limit is None:
            integrity[str(page_size)] = "INCONCLUSIVE"
            continue
        passes = all(
            run.get("status") == "completed"
            and run.get("query_status") == "exhausted"
            and run.get("hit_count") == HIT_COUNT
            and run.get("budget_limit") is None
            and run.get("source_total_relation") == "gte"
            and run.get("retry_count") == 0
            and run.get("worker_peak_rss_bytes", 0) < memory_limit
            and run.get("page_hit_counts") == expected_pages
            for run in profile_runs
        )
        integrity[str(page_size)] = "PASS" if passes else "FAIL"

    valid_profiles = [
        int(size) for size, status in integrity.items() if status == "PASS" and size in summaries
    ]
    selected_default = (
        max(valid_profiles, key=lambda size: summaries[str(size)]["mean_hits_per_minute"])
        if valid_profiles
        else None
    )

    if memory_limit is None or "10000" not in summaries or "5000" not in summaries:
        gate = "INCONCLUSIVE"
        reason = (
            "A medição não contém envelope de memória ou três repetições completas comparáveis."
        )
    elif integrity.get("10000") == "INCONCLUSIVE":
        gate = "INCONCLUSIVE"
        reason = "O perfil 10.000 não contém três repetições avaliáveis."
    elif integrity.get("10000") != "PASS":
        gate = "FAIL"
        reason = "O perfil 10.000 não passou em integridade, retries ou limite de memória."
    elif summaries["10000"]["mean_hits_per_minute"] < summaries["5000"]["mean_hits_per_minute"]:
        gate = "FAIL"
        reason = (
            "A vazão média de 10.000 ficou abaixo de 5.000: "
            f"{summaries['10000']['mean_hits_per_minute']:.1f} contra "
            f"{summaries['5000']['mean_hits_per_minute']:.1f} hits/min."
        )
    else:
        gate = "PASS"
        reason = "10.000 passou integridade, memória declarada e vazão frente a 5.000."

    return {
        "profiles": summaries,
        "profile_integrity": integrity,
        "selected_default_page_size": selected_default,
        "page_size_10000_gate": gate,
        "gate_reason": reason,
    }


def run_benchmark() -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for page_size in PAGE_PROFILES:
        for repetition in range(1, REPETITIONS + 1):
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agrojud.testing.spec026_benchmark",
                    "--single",
                    "--page-size",
                    str(page_size),
                    "--repetition",
                    str(repetition),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=600,
            )
            runs.append(json.loads(completed.stdout))
    memory_limits = {run["memory_limit_bytes"] for run in runs}
    memory_limit = next(iter(memory_limits)) if len(memory_limits) == 1 else None
    assessment = _benchmark_assessment(runs, memory_limit)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "synthetic PostgreSQL-only; no operational records or DataJud requests",
        "payload_profile": (
            "unique process/representation records with an explicit empty movement list"
        ),
        "hit_count_per_run": HIT_COUNT,
        "repetitions_per_profile": REPETITIONS,
        "page_profiles": list(PAGE_PROFILES),
        "worker_memory_limit_bytes": memory_limit,
        **assessment,
        "runs": runs,
        "worker_scenarios": {
            "full_short_empty_pages": "executed_by_each_profile",
            "http_and_persistence_failures_replay_cancel_limit_resume": "covered_by_pytest_suite",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--page-size", type=int)
    parser.add_argument("--repetition", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = (
        _run_one(args.page_size, args.repetition)
        if args.single and args.page_size is not None and args.repetition is not None
        else run_benchmark()
    )
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
