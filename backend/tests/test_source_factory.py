"""Source selection keeps demo and real inputs strictly separated."""

import httpx
import pytest

from agrojud.config import Settings
from agrojud.sources import (
    DataJudSourceAdapter,
    SourceError,
    SourceErrorCode,
    SourceKind,
    SyntheticSourceAdapter,
    build_source_adapter,
)

OPERATIONAL_URL = "postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_demo"
TEST_URL = "postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_factory_test"


def settings(environment: str, api_key: str | None = "test-key") -> Settings:
    return Settings(
        environment=environment,  # type: ignore[arg-type]
        database_url=OPERATIONAL_URL,
        test_database_url=TEST_URL,
        datajud_api_key=api_key,
    )


def test_demo_selects_only_synthetic_source() -> None:
    adapter = build_source_adapter(settings("demo"), SourceKind.SYNTHETIC)

    assert isinstance(adapter, SyntheticSourceAdapter)
    with pytest.raises(SourceError) as captured:
        build_source_adapter(settings("demo"), SourceKind.DATAJUD)
    assert captured.value.code is SourceErrorCode.VALIDATION


def test_real_selects_only_datajud_and_missing_key_does_not_fallback() -> None:
    with pytest.raises(SourceError) as missing_key:
        build_source_adapter(settings("real", api_key=None), SourceKind.DATAJUD)
    assert missing_key.value.code is SourceErrorCode.AUTHENTICATION

    with pytest.raises(SourceError) as synthetic:
        build_source_adapter(settings("real"), SourceKind.SYNTHETIC)
    assert synthetic.value.code is SourceErrorCode.VALIDATION


def test_test_datajud_requires_an_injected_http_transport() -> None:
    with pytest.raises(SourceError) as captured:
        build_source_adapter(settings("test"), SourceKind.DATAJUD)
    assert captured.value.code is SourceErrorCode.VALIDATION

    adapter = build_source_adapter(
        settings("test"),
        SourceKind.DATAJUD,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"hits": {"hits": []}})),
    )
    assert isinstance(adapter, DataJudSourceAdapter)
    adapter.close()
