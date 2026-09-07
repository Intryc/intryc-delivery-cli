import json

import pytest
from click.testing import CliRunner

from intryc_delivery.cli import cli, main
from intryc_delivery.config import Settings, load_settings
from intryc_delivery.errors import DeliveryError


def test_validate_json_and_no_aws(root, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Local validation attempted AWS access")

    monkeypatch.setattr("intryc_delivery.s3.boto3.Session", unexpected)
    result = CliRunner().invoke(cli, ["validate", str(root), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["outcome"] == "validated"
    assert payload["import_status"] == "not_checked"


def test_deliver_dry_run_does_not_write_or_contact_aws(root, monkeypatch):
    (root / "manifest.json").unlink()

    def unexpected(*args, **kwargs):
        raise AssertionError("Dry run attempted AWS access")

    monkeypatch.setattr("intryc_delivery.s3.boto3.Session", unexpected)
    result = CliRunner().invoke(
        cli,
        [
            "deliver",
            str(root),
            "--delivery-id",
            "test",
            "--bucket",
            "example-bucket",
            "--prefix",
            "incoming/",
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["outcome"] == "dry_run"
    assert not (root / "manifest.json").exists()


def test_end_to_end_command(root, s3, monkeypatch):
    from intryc_delivery.s3 import S3Publisher

    monkeypatch.setattr(
        "intryc_delivery.cli.S3Publisher",
        lambda settings, progress: S3Publisher(settings, client=s3, progress=progress),
    )
    args = ["deliver", str(root), "--bucket", "example-bucket", "--prefix", "incoming", "--json"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["outcome"] == "published"
    assert "verified" in result.stderr
    result = CliRunner().invoke(cli, args)
    assert json.loads(result.stdout)["outcome"] == "already_published"


def test_invalid_content_and_filesystem_exit_codes(root):
    (root / "manifest.json").write_text('{"x":"private-body", "x":2}')
    result = CliRunner().invoke(cli, ["validate", str(root), "--json"])
    assert result.exit_code == 1
    assert "private-body" not in result.output
    assert json.loads(result.output)["issues"][0]["code"] == "INVALID_JSON"
    result = CliRunner().invoke(cli, ["validate", str(root / "missing"), "--json"])
    assert result.exit_code == 2


def test_configuration_precedence(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text('bucket = "config-bucket"\nprefix = "config"\nconcurrency = 2\n')
    monkeypatch.setenv("INTRYC_DELIVERY_BUCKET", "env-bucket")
    monkeypatch.setenv("INTRYC_DELIVERY_PREFIX", "env-prefix")
    settings = load_settings(config, {"bucket": "flag-bucket", "prefix": None})
    assert settings.bucket == "flag-bucket"
    assert settings.prefix == "env-prefix"
    assert settings.concurrency == 2
    config.write_text('aws_secret_access_key = "never-echo-this"')
    with pytest.raises(DeliveryError) as exc:
        load_settings(config, {})
    assert "never-echo-this" not in str(exc.value.issues)


@pytest.mark.parametrize(
    "prefix", ["", "/root", "s3://bucket/root", "a//b", "a/../b", "a\\b", "a/*", "a//"]
)
def test_invalid_prefix(prefix):
    with pytest.raises(ValueError):
        Settings(bucket="example-bucket", prefix=prefix)


def test_schema_files_distributed():
    from importlib.resources import files

    assert files("intryc_delivery.contract").joinpath("schemas", "ticket-1.0.schema.json").is_file()


def test_main_usage_error_json_is_sanitized(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["intryc-delivery", "validate", "--private-argument", "secret", "--json"]
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "secret" not in output
    assert json.loads(output)["issues"][0]["code"] == "INVALID_ARGUMENTS"
