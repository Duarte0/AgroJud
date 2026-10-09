"""SQLAlchemy engine construction."""

from sqlalchemy import Engine, create_engine


def make_engine(database_url: str) -> Engine:
    """Build a synchronous engine without logging SQL values or connection details."""

    return create_engine(database_url, hide_parameters=True, pool_pre_ping=True)
