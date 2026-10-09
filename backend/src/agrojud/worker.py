"""Persistent worker process for leased collection jobs."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
from datetime import timedelta
from threading import Event
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from agrojud.config import get_settings
from agrojud.db.engine import make_engine
from agrojud.services.collection import build_collection_job_handler
from agrojud.services.job_worker import LeasedWorker
from agrojud.services.jobs import JobService
from agrojud.services.scheduling import SCHEDULER_INTERVAL_SECONDS, DailyScheduler
from agrojud.services.signal_reprocessing import build_signal_run_handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Processador persistente do AgroJud Radar.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validar a configuração e encerrar sem iniciar o loop do worker",
    )
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.check:
        print(
            json.dumps(
                {
                    "event": "worker_configuration_validated",
                    "environment": settings.environment,
                    "processing_enabled": False,
                },
                sort_keys=True,
            )
        )
        return

    engine = make_engine(settings.effective_database_url)
    with engine.connect() as connection:
        connection.execute(text("select 1"))
    sessions = sessionmaker(engine, expire_on_commit=False)
    jobs = JobService(
        sessions,
        lease_duration=timedelta(seconds=settings.job_lease_seconds),
    )
    collection_handler = build_collection_job_handler(settings)
    signal_handler = build_signal_run_handler(jobs)
    mode: Literal["demo", "real"] = "real" if settings.environment == "real" else "demo"
    source = "datajud" if mode == "real" else "synthetic"
    scheduler = DailyScheduler(sessions, jobs, mode=mode, source=source)
    worker_id = os.getenv("JOB_WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"
    worker = LeasedWorker(
        jobs,
        worker_id=worker_id,
        handlers={
            "discovery": collection_handler,
            "refresh_number": collection_handler,
            "reprocess_rules": signal_handler,
        },
        heartbeat_interval=settings.job_heartbeat_seconds,
    )
    stop = Event()
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop.set())
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.set())
    print(
        json.dumps(
            {
                "event": "worker_started",
                "environment": settings.environment,
                "registered_handlers": sorted(worker.handlers),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        while not stop.is_set():
            try:
                scheduler.run_due()
            except SQLAlchemyError as error:
                print(
                    json.dumps(
                        {
                            "event": "scheduler_check_failed",
                            "error_type": type(error).__name__,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            if not worker.run_once():
                stop.wait(min(settings.job_poll_seconds, SCHEDULER_INTERVAL_SECONDS))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
