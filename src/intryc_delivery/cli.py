"""Commands for preparing, verifying and publishing customer deliveries."""

import os
import sys
from contextlib import nullcontext
from pathlib import Path

import click

from intryc_delivery import __version__
from intryc_delivery.config import load_settings
from intryc_delivery.errors import DeliveryError, ExitCode, Issue, fail
from intryc_delivery.filesystem import delivery_lock
from intryc_delivery.manifest import generate
from intryc_delivery.reporting import report
from intryc_delivery.s3 import S3Publisher
from intryc_delivery.validation import schema_validators, validate


@click.group()
@click.version_option(__version__)
def cli():
    """Prepare, verify and publish immutable Intryc ticket deliveries."""


def execute(
    command,
    directory=None,
    *,
    config=None,
    dry_run=False,
    delivery_id=None,
    created_at=None,
    default_assignee_source_user_id=None,
    force=False,
    probe=False,
    **options,
):
    json_output = options.get("json_output") or os.environ.get(
        "INTRYC_DELIVERY_JSON", ""
    ).lower() in {"1", "true", "yes"}
    try:
        settings = load_settings(config, options)
        json_output = settings.json_output
        progress = (
            (lambda _: None)
            if settings.no_progress
            else (lambda message: click.echo(message, err=True))
        )
        schema_validators()
        if command == "doctor":
            if not dry_run:
                S3Publisher(settings, progress=progress).doctor(probe=probe)
            report(
                command,
                "dry_run" if dry_run else "access_checked",
                json_output=json_output,
                probe_object_retained=bool(probe and not dry_run),
                import_status="not_checked",
            )
            return
        root = Path(directory)
        if root.is_symlink():
            fail(
                "UNSAFE_PATH",
                "Delivery root must not be a symlink.",
                exit_code=ExitCode.CONFIGURATION,
            )
        root = root.resolve(strict=True)
        local_lock = (
            delivery_lock(str(root)) if command != "validate" and not dry_run else nullcontext()
        )
        with local_lock:
            if command == "manifest" or (
                command == "deliver" and not (root / "manifest.json").exists()
            ):
                delivery = generate(
                    root,
                    delivery_id=delivery_id,
                    created_at=created_at,
                    default_assignee_source_user_id=default_assignee_source_user_id,
                    force=force,
                    dry_run=dry_run,
                )
            else:
                delivery = validate(root)
                if delivery_id is not None and delivery_id != delivery.delivery_id:
                    fail(
                        "DELIVERY_ID_MISMATCH",
                        "The provided delivery ID differs from manifest.json.",
                    )
                if created_at is not None and created_at != delivery.manifest["created_at"]:
                    fail(
                        "MANIFEST_CONFLICT",
                        "The provided creation timestamp differs from manifest.json.",
                    )
                if (
                    default_assignee_source_user_id is not None
                    and default_assignee_source_user_id
                    != delivery.manifest.get("defaults", {}).get("assignee_source_user_id")
                ):
                    fail(
                        "MANIFEST_CONFLICT",
                        "The provided default assignee differs from manifest.json.",
                    )
            outcome = "validated" if command == "validate" else "manifest_created"
            if command in {"upload", "publish", "deliver"}:
                if not settings.bucket or not settings.prefix:
                    fail(
                        "MISSING_TARGET",
                        "Supply --bucket and --prefix or configure their equivalents.",
                        exit_code=ExitCode.CONFIGURATION,
                    )
                # No client or AWS credential resolution during dry-run.
                root_key = f"{settings.prefix}/deliveries/{delivery.delivery_id}/"
                if any(len((root_key + obj.path).encode()) > 1024 for obj in delivery.objects):
                    fail(
                        "KEY_TOO_LONG",
                        "Combined target key exceeds 1024 bytes.",
                        exit_code=ExitCode.CONFIGURATION,
                    )
                if not dry_run:
                    with delivery_lock(
                        f"s3:{settings.bucket}:{settings.prefix}:{delivery.delivery_id}"
                    ):
                        publisher = S3Publisher(settings, progress=progress)
                        if command in {"upload", "deliver"}:
                            already_published = publisher.upload(delivery)
                            outcome = "already_published" if already_published else "uploaded"
                        if command in {"publish", "deliver"}:
                            already_published = publisher.publish(delivery)
                            outcome = "already_published" if already_published else "published"
            report(
                command,
                "dry_run" if dry_run else outcome,
                json_output=json_output,
                delivery_id=delivery.delivery_id,
                files=len(delivery.objects),
                bytes=sum(o.bytes for o in delivery.objects),
                import_status="not_checked",
            )
    except DeliveryError as exc:
        report(command, "failed", json_output=json_output, issues=exc.issues)
        raise SystemExit(exc.exit_code) from None
    except (OSError, ValueError):
        report(
            command,
            "failed",
            json_output=json_output,
            issues=[
                Issue(
                    code="LOCAL_CONFIGURATION",
                    message="Cannot read local files or configuration. Check paths, permissions and options.",
                )
            ],
        )
        raise SystemExit(ExitCode.CONFIGURATION) from None
    except KeyboardInterrupt:
        report(
            command,
            "interrupted",
            json_output=json_output,
            issues=[
                Issue(
                    code="INTERRUPTED",
                    message="Retry the same delivery to verify its state and resume.",
                )
            ],
        )
        raise SystemExit(ExitCode.TRANSPORT) from None


def register(command: str, help_text: str):
    def callback(**kwargs):
        execute(command, **kwargs)

    options = [
        click.option("--config", type=click.Path(path_type=Path)),
        click.option(
            "--dry-run",
            is_flag=True,
            help="Validate and describe the operation without writing files or contacting AWS.",
        ),
        click.option(
            "--json",
            "json_output",
            is_flag=True,
            default=None,
            help="Emit one machine-readable JSON result.",
        ),
        click.option("--no-progress", is_flag=True, default=None),
    ]
    if command in {"upload", "publish", "deliver", "doctor"}:
        options += [
            click.option("--bucket"),
            click.option("--prefix"),
            click.option("--region"),
            click.option("--profile"),
            click.option("--endpoint-url"),
            click.option("--concurrency", type=click.IntRange(1, 16)),
        ]
    if command in {"manifest", "deliver"}:
        options += [
            click.option("--delivery-id"),
            click.option("--created-at"),
            click.option("--default-assignee-source-user-id"),
        ]
    if command == "manifest":
        options += [
            click.option(
                "--force",
                is_flag=True,
                help="Replace an incompatible manifest for an unpublished delivery.",
            )
        ]
    if command == "doctor":
        options += [
            click.option(
                "--probe",
                is_flag=True,
                help="Write/read a small retained probe object; never creates a ready marker.",
            )
        ]
    else:
        callback = click.argument("directory", type=click.Path(path_type=Path))(callback)
    for option in reversed(options):
        callback = option(callback)
    cli.add_command(click.command(name=command, help=help_text)(callback))


for name, description in {
    "validate": "Validate the entire local delivery without contacting AWS.",
    "manifest": "Generate a deterministic manifest from validated ticket JSON and assets.",
    "upload": "Upload and verify files without publishing a ready marker.",
    "publish": "Verify all remote files and publish the empty ready marker last.",
    "deliver": "Prepare, validate, upload, verify and publish a delivery.",
    "doctor": "Check schemas, credentials, bucket Region and prefix access.",
}.items():
    register(name, description)


def main():
    try:
        cli.main(standalone_mode=False)
    except click.ClickException:
        # Click's default usage errors can echo raw argument values.
        report(
            "usage",
            "failed",
            json_output="--json" in sys.argv,
            issues=[
                Issue(
                    code="INVALID_ARGUMENTS",
                    message="Invalid arguments. Run intryc-delivery --help or COMMAND --help.",
                )
            ],
        )
        raise SystemExit(ExitCode.CONFIGURATION) from None


if __name__ == "__main__":
    main()
