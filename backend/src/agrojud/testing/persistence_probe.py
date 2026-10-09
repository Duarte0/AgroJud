"""Disposable persistence proof command for a test-only PostgreSQL database."""

import argparse
import sys

from sqlalchemy import text

from agrojud.config import get_settings
from agrojud.db.engine import make_engine

TABLE_NAME = "agrojud_diagnostic_probe"


def run_probe(action: str, key: str | None) -> int:
    settings = get_settings()
    if settings.environment != "test":
        print("Persistence probes require AGROJUD_ENV=test.", file=sys.stderr)
        return 2

    if action in {"write", "read"} and not key:
        print("A non-empty probe key is required.", file=sys.stderr)
        return 2

    engine = make_engine(settings.effective_database_url)
    try:
        with engine.begin() as connection:
            if action == "write":
                connection.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} "
                        "(probe_key text PRIMARY KEY, payload text NOT NULL)"
                    )
                )
                connection.execute(
                    text(
                        f"INSERT INTO {TABLE_NAME} (probe_key, payload) VALUES (:key, :payload) "
                        "ON CONFLICT (probe_key) DO UPDATE SET payload = EXCLUDED.payload"
                    ),
                    {"key": key, "payload": "persisted"},
                )
                print("diagnostic record written")
                return 0

            if action == "read":
                exists = connection.execute(
                    text(f"SELECT 1 FROM {TABLE_NAME} WHERE probe_key = :key"),
                    {"key": key},
                ).scalar_one_or_none()
                if exists is None:
                    print("diagnostic record was not found", file=sys.stderr)
                    return 1
                print("diagnostic record persisted")
                return 0

            connection.execute(text(f"DROP TABLE IF EXISTS {TABLE_NAME}"))
            print("diagnostic table removed")
            return 0
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("write", "read", "cleanup"))
    parser.add_argument("key", nargs="?")
    args = parser.parse_args()
    raise SystemExit(run_probe(args.action, args.key))


if __name__ == "__main__":
    main()
