"""Environment and database isolation guards."""

import pytest
from pydantic import ValidationError

from agrojud.config import Settings

OPERATIONAL_URL = "postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_demo"
TEST_URL = "postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_settings_test"


def test_demo_is_the_default_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGROJUD_ENV", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(database_url=OPERATIONAL_URL)

    assert settings.environment == "demo"
    assert settings.effective_database_url == OPERATIONAL_URL


def test_test_environment_selects_test_database() -> None:
    settings = Settings(
        environment="test",
        database_url=OPERATIONAL_URL,
        test_database_url=TEST_URL,
    )

    assert settings.effective_database_url == TEST_URL


def test_test_database_must_not_match_operational_database() -> None:
    with pytest.raises(ValidationError, match="must differ") as error:
        Settings(environment="test", database_url=TEST_URL, test_database_url=TEST_URL)

    assert "secret" not in str(error.value)


def test_test_database_name_must_have_test_suffix() -> None:
    with pytest.raises(ValidationError, match="must end with _test"):
        Settings(
            environment="test",
            database_url=OPERATIONAL_URL,
            test_database_url=OPERATIONAL_URL.replace("agrojud_demo", "agrojud_checks"),
        )


def test_demo_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGROJUD_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="DATABASE_URL is required"):
        Settings()


def test_job_lease_and_heartbeat_defaults_are_configurable() -> None:
    settings = Settings(database_url=OPERATIONAL_URL)
    assert settings.job_lease_seconds == 120
    assert settings.job_heartbeat_seconds == 20
    assert settings.job_poll_seconds == 2

    configured = Settings(
        database_url=OPERATIONAL_URL,
        job_lease_seconds=30,
        job_heartbeat_seconds=5,
        job_poll_seconds=0.5,
    )
    assert configured.job_lease_seconds == 30
    assert configured.job_heartbeat_seconds == 5
    assert configured.job_poll_seconds == 0.5


def test_job_heartbeat_must_be_shorter_than_lease() -> None:
    with pytest.raises(ValidationError, match="JOB_HEARTBEAT_SECONDS must be less"):
        Settings(
            database_url=OPERATIONAL_URL,
            job_lease_seconds=20,
            job_heartbeat_seconds=20,
        )
