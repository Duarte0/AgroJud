"""The initial worker validates configuration without printing credentials."""

import json

import pytest

from agrojud.worker import main


def test_worker_validates_configuration_without_logging_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGROJUD_ENV", "test")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://agrojud:do-not-log-db@localhost:5432/agrojud_demo",
    )
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://agrojud:do-not-log-test@localhost:5432/agrojud_worker_test",
    )
    monkeypatch.setenv("DATAJUD_API_KEY", "do-not-log-api-key")

    main()

    output = capsys.readouterr().out
    assert json.loads(output) == {
        "environment": "test",
        "event": "worker_configuration_validated",
        "processing_enabled": False,
    }
    assert "do-not-log" not in output
