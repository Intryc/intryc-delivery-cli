import ipaddress
import os
import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from intryc_delivery.contract.semantic import validate_relative_path
from intryc_delivery.errors import ExitCode, fail


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bucket: str | None = None
    prefix: str | None = None
    region: str | None = None
    profile: str | None = None
    endpoint_url: str | None = None
    concurrency: int = Field(default=4, ge=1, le=16)
    json_output: bool = False
    no_progress: bool = False

    @field_validator("bucket")
    @classmethod
    def bucket_name(cls, value):
        if value is None:
            return value
        bucket = value.strip()
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
            or any(p in bucket for p in ("..", ".-", "-."))
            or bucket.startswith(("xn--", "sthree-", "amzn-s3-demo-"))
            or bucket.endswith(("-s3alias", "--ol-s3", ".mrap", "--x-s3", "--table-s3"))
        ):
            raise ValueError("Invalid bucket")
        try:
            ipaddress.ip_address(bucket)
        except ValueError:
            return bucket
        raise ValueError("Invalid bucket")

    @field_validator("prefix")
    @classmethod
    def prefix_name(cls, value):
        if value is None:
            return value
        prefix = value.strip()
        if prefix.endswith("/"):
            prefix = prefix[:-1]
        validate_relative_path(prefix)
        if len(prefix.encode("utf-8")) > 900:
            raise ValueError("Prefix too long")
        return prefix

    @field_validator("endpoint_url")
    @classmethod
    def endpoint(cls, value):
        if value is not None:
            url = urlsplit(value)
            if (
                url.scheme not in {"https", "http"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("Invalid endpoint")
            if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Use HTTPS for remote endpoints")
        return value


def load_settings(config: Path | None, overrides: dict) -> Settings:
    if config is not None and not config.is_file():
        fail(
            "MISSING_CONFIG",
            "The explicit configuration file does not exist.",
            exit_code=ExitCode.CONFIGURATION,
        )
    config = config or Path("intryc-delivery.toml")
    values = {}
    if config.exists():
        try:
            with config.open("rb") as stream:
                values = tomllib.load(stream)
        except (OSError, ValueError):
            fail(
                "INVALID_CONFIG",
                "Cannot read the TOML configuration.",
                exit_code=ExitCode.CONFIGURATION,
            )
    for field in Settings.model_fields:
        env_key = "INTRYC_DELIVERY_" + ("JSON" if field == "json_output" else field.upper())
        if env_key in os.environ:
            values[field] = os.environ[env_key]
    values.update({k: v for k, v in overrides.items() if v is not None})
    try:
        return Settings.model_validate(values)
    except ValidationError:
        fail(
            "INVALID_CONFIG",
            "Check bucket, prefix, endpoint and options; only documented configuration fields are allowed.",
            exit_code=ExitCode.CONFIGURATION,
        )
