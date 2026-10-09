"""Export the deterministic local OpenAPI contract without connecting to PostgreSQL."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Exporta o schema OpenAPI local.")
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--stdout", action="store_true", help="escreve o schema em stdout")
    output.add_argument("--output", type=Path, help="grava o schema no caminho informado")
    args = parser.parse_args(argv)

    os.environ["AGROJUD_ENV"] = "demo"
    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://agrojud:contract-only@127.0.0.1:5432/agrojud_demo"
    )
    os.environ.pop("TEST_DATABASE_URL", None)
    os.environ.pop("DATAJUD_API_KEY", None)

    from agrojud.api.app import app

    serialized = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    app.state.database_engine.dispose()
    if args.stdout:
        sys.stdout.write(serialized)
        return
    assert args.output is not None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")


if __name__ == "__main__":
    main()
