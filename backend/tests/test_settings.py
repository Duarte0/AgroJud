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
    assert settings.export_process_limit == 50_000
    assert settings.datajud_read_timeout_seconds == 60

    configured = Settings(
        database_url=OPERATIONAL_URL,
        job_lease_seconds=30,
        job_heartbeat_seconds=5,
        job_poll_seconds=0.5,
        export_process_limit=1_000,
    )
    assert configured.job_lease_seconds == 30
    assert configured.job_heartbeat_seconds == 5
    assert configured.job_poll_seconds == 0.5
    assert configured.export_process_limit == 1_000


def test_job_heartbeat_must_be_shorter_than_lease() -> None:
    with pytest.raises(ValidationError, match="JOB_HEARTBEAT_SECONDS must be less"):
        Settings(
            database_url=OPERATIONAL_URL,
            job_lease_seconds=20,
            job_heartbeat_seconds=20,
        )


def test_real_datajud_read_timeout_must_be_shorter_than_lease() -> None:
    with pytest.raises(ValidationError, match="DATAJUD_READ_TIMEOUT_SECONDS must be less"):
        Settings(
            environment="real",
            database_url=OPERATIONAL_URL,
            job_lease_seconds=60,
            datajud_read_timeout_seconds=60,
        )

    demo = Settings(database_url=OPERATIONAL_URL, job_lease_seconds=30, job_heartbeat_seconds=5)
    assert demo.datajud_read_timeout_seconds == 60


def test_frontend_origin_defaults_to_local_vite_server() -> None:
    assert Settings(database_url=OPERATIONAL_URL).frontend_origin == "http://127.0.0.1:5173"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("http://localhost:4173", "http://localhost:4173"),
        ("http://127.0.0.1:5173/", "http://127.0.0.1:5173"),
        ("http://[::1]:5173", "http://[::1]:5173"),
    ],
)
def test_frontend_origin_accepts_loopback_origins(configured: str, expected: str) -> None:
    settings = Settings(database_url=OPERATIONAL_URL, frontend_origin=configured)

    assert settings.frontend_origin == expected


@pytest.mark.parametrize(
    "configured",
    [
        "http://192.168.0.10:5173",
        "http://example.com:5173",
        "http://127.0.0.1",
        "ftp://127.0.0.1:5173",
        "http://127.0.0.1:5173/app",
        "http://user:pass@127.0.0.1:5173",
        "*",
    ],
)
def test_frontend_origin_rejects_non_loopback_or_partial_origins(configured: str) -> None:
    with pytest.raises(ValidationError, match="FRONTEND_ORIGIN"):
        Settings(database_url=OPERATIONAL_URL, frontend_origin=configured)
