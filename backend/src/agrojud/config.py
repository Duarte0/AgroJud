"""Validated environment configuration for backend processes."""

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


def _parse_database_url(value: str | None, name: str) -> URL | None:
    if value is None:
        return None

    try:
        url = make_url(value)
    except ArgumentError:
        raise ValueError(f"{name} must be a valid PostgreSQL URL.") from None

    if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
        raise ValueError(f"{name} must use postgresql+psycopg and include a host and database.")

    return url


class Settings(BaseSettings):
    """Application settings loaded from process environment variables."""

    model_config = SettingsConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
        validate_default=True,
    )

    environment: Literal["demo", "real", "test"] = Field(
        default="demo",
        validation_alias="AGROJUD_ENV",
    )
    database_url: str | None = Field(default=None, repr=False, validation_alias="DATABASE_URL")
    test_database_url: str | None = Field(
        default=None,
        repr=False,
        validation_alias="TEST_DATABASE_URL",
    )
    datajud_api_key: SecretStr | None = Field(
        default=None,
        repr=False,
        validation_alias="DATAJUD_API_KEY",
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str | None) -> str | None:
        _parse_database_url(value, "DATABASE_URL")
        return value

    @field_validator("test_database_url")
    @classmethod
    def validate_test_database_url(cls, value: str | None) -> str | None:
        url = _parse_database_url(value, "TEST_DATABASE_URL")
        if url is not None and (url.database is None or not url.database.lower().endswith("_test")):
            raise ValueError("TEST_DATABASE_URL database name must end with _test.")
        return value

    @model_validator(mode="after")
    def validate_environment_urls(self) -> Settings:
        database_url = _parse_database_url(self.database_url, "DATABASE_URL")
        test_database_url = _parse_database_url(self.test_database_url, "TEST_DATABASE_URL")

        if self.environment == "test":
            if test_database_url is None:
                raise ValueError("TEST_DATABASE_URL is required when AGROJUD_ENV=test.")
            if database_url is not None and database_url == test_database_url:
                raise ValueError("TEST_DATABASE_URL must differ from DATABASE_URL.")
        elif database_url is None:
            raise ValueError("DATABASE_URL is required for demo and real environments.")

        return self

    @property
    def effective_database_url(self) -> str:
        """Return the configured URL selected for this process."""

        value = self.test_database_url if self.environment == "test" else self.database_url
        if value is None:
            raise RuntimeError("Validated settings must include a database URL.")
        return value


def get_settings() -> Settings:
    """Read and validate settings from the current process environment."""

    return Settings()
