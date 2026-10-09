"""PostgreSQL-backed test fixtures; all temporary databases are named *_test."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from agrojud.config import get_settings


@pytest.fixture
def scratch_database_url() -> Iterator[URL]:
    settings = get_settings()
    if settings.environment != "test" or settings.test_database_url is None:
        raise RuntimeError("Tests require AGROJUD_ENV=test and TEST_DATABASE_URL.")

    template = make_url(settings.test_database_url)
    database_name = f"agrojud_{uuid4().hex}_test"
    admin_url = template.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    created = False
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        yield template.set(database=database_name)
    finally:
        if created:
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        admin_engine.dispose()
