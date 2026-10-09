"""Alembic runtime configuration."""

from alembic import context
from sqlalchemy.engine import Connection

from agrojud.config import get_settings
from agrojud.db.engine import make_engine

config = context.config


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=None, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


injected_connection = config.attributes.get("connection")
if injected_connection is not None:
    run_migrations(injected_connection)
else:
    settings = get_settings()
    engine = make_engine(settings.effective_database_url)
    try:
        with engine.connect() as connection:
            run_migrations(connection)
    finally:
        engine.dispose()
