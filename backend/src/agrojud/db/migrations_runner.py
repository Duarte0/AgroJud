"""Programmatic Alembic entrypoint shared by the CLI and integration tests."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection


def make_alembic_config(connection: Connection | None = None) -> Config:
    """Load the package migration configuration and optionally inject a connection."""

    project_root = Path(__file__).resolve().parents[3]
    config = Config(str(project_root / "alembic.ini"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def upgrade_database(connection: Connection | None = None) -> None:
    """Apply migrations explicitly; callers decide when this command is run."""

    command.upgrade(make_alembic_config(connection), "head")
