# Intryc delivery CLI

Standalone Python package for preparing and publishing `ticket-delivery/1.0` deliveries. It includes its own schemas and validators and has no dependency on the AutoQA backend or Bifrost runtime. Python 3.11+ on Linux and macOS is supported. The source is public under the MIT license; the first PyPI release is pending.

## Install and run

```sh
uv sync --group dev
uv run intryc-delivery --help
uv run intryc-delivery validate ./examples/delivery
uv run intryc-delivery manifest ./delivery --delivery-id delivery-001
uv run intryc-delivery deliver ./delivery --bucket customer-bucket --prefix incoming --profile customer --region us-east-1
```

After building with `uv build`, install the wheel using `uv tool install ./dist/intryc_delivery-0.1.0-py3-none-any.whl`. The package is maintained in [Intryc/intryc-delivery-cli](https://github.com/Intryc/intryc-delivery-cli) and installs independently. No command sends analytics. It uses the standard boto3 credential chain, including AWS profiles, SSO, environment credentials and workload roles; credentials never belong in the project config.

For ticket structure, supported media, source IDs and complete examples, use the
[customer guide](https://docs.intryc.com/ll8EgvwxgIPwX759B5CP/connections/bulk-delivery).
The bundled examples are synthetic: set an assignee email belonging to your active
Intryc workspace user and choose fresh delivery/source IDs before a real pilot.
Changing ticket or asset bytes also requires updating their sizes and hashes;
`manifest` validates asset references and never silently rewrites ticket JSON.

## Credential expiration

Keep credentials in the standard AWS credential provider, not in CLI config.
Role profiles (`role_arn` plus `source_profile` or `credential_source`), web identity
and workload-role providers can renew temporary sessions automatically. The CLI
keeps the SDK's refreshable provider attached to its S3 client; it never snapshots
credentials into a static client. The source identity must remain valid. Interactive
SSO/MFA sign-in may still be needed, so use an unattended provider for scheduled jobs.

Temporary access keys and session tokens exported directly into environment
variables cannot renew themselves. If they expire, obtain fresh credentials and
rerun the same command against the unchanged delivery. Permanent credential or
permission failures stop without creating a ready marker; transient streaming
failures use bounded retries. See [Boto3 credential documentation](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html).

## Commands

| Command | Behavior |
| --- | --- |
| `validate DIRECTORY` | Offline validation of JSON Schema, semantic rules, inventory, actual sizes, SHA-256, media signatures and filenames. |
| `manifest DIRECTORY --delivery-id ID` | Creates `manifest.json` atomically; preserves an existing creation time and refuses incompatible replacement without `--force`. |
| `upload DIRECTORY` | Resumes missing or mismatched unpublished files, reads every remote object back and verifies it; never writes readiness. |
| `publish DIRECTORY` | Requires an exact verified remote inventory, then conditionally writes the empty marker. |
| `deliver DIRECTORY` | Generates a missing manifest, validates, uploads, verifies and publishes. |
| `doctor` | Checks packaged schemas, AWS identity, bucket Region and prefix listing. `--probe` explicitly writes and verifies a small object in a fresh unpublished delivery; that object remains for lifecycle cleanup. |

Local files follow this layout:

```text
delivery/
  manifest.json
  ticket_details/*.json
  assets/...
```

Uploads are stored under `<prefix>/deliveries/<delivery-id>/`. The only readiness signal is the separate zero-byte `<prefix>/_ready/<delivery-id>` object, created with `If-None-Match: *` after verification. Local ready markers are invalid. Published deliveries are immutable. Repeating an identical published delivery verifies the manifest and all bytes before reporting `already_published`. Uploads use bounded concurrency and multipart transfers; files are streamed, not loaded into memory. ETags and custom SHA-256 metadata are never used as proof of whole-file integrity.

Ticket and asset uploads complete before the manifest is uploaded. Uploaded objects include `sha256` and `delivery-id-base64` metadata. The latter is the Base64-encoded UTF-8 delivery ID, allowing Unicode IDs within S3's ASCII metadata and size limits. Object keys retain the original ID.

Use one writer per delivery ID. Local exclusive locks cover both the directory and the remote target, and release when a process exits. Separate hosts must use distinct delivery IDs; this CLI does not implement a distributed data-object lock. Do not edit files while a command runs. Never use `manifest --force` to change a published delivery; use a new ID and directory instead.

`--dry-run` validates without writing files, obtaining credentials or contacting AWS. It cannot certify remote access or immutability. `--json` emits one result object on stdout with `result_version`, `command`, `outcome`, counts and sorted `issues`; progress is sent to stderr and can be disabled with `--no-progress`. Exit codes are 0 (success), 1 (invalid delivery content), 2 (local filesystem/configuration), 3 (AWS/transport). Successful publication does not mean ticket import succeeded: workspace eligibility, previously ingested IDs, Intryc's KMS access and downstream processing are checked by the importer.

## Configuration

Flags override `INTRYC_DELIVERY_*` environment variables, which override an `intryc-delivery.toml` file in the current directory (or `--config PATH`). Allowed fields are `bucket`, `prefix`, `region`, `profile`, `endpoint_url`, `concurrency`, `json_output`, and `no_progress`. `INTRYC_DELIVERY_JSON` controls JSON output. Unknown fields are rejected. Configuration belongs outside the delivery directory.

```toml
bucket = "customer-bucket"
prefix = "incoming"
region = "us-east-1"
profile = "customer"
concurrency = 4
```

Pass `--endpoint-url` for an S3-compatible test endpoint. HTTPS is required except on localhost. Endpoints must support conditional marker creation; the CLI never falls back to overwriting a marker. Shell completion is provided by Click: `eval "$(_INTRYC_DELIVERY_COMPLETE=bash_source intryc-delivery)"` for Bash, or replace `bash_source` with `zsh_source` for Zsh.

## AWS permission prerequisite

Both ownership modes require the writer to have prefix-scoped `s3:ListBucket`, `s3:GetObject`, `s3:PutObject`, `s3:AbortMultipartUpload`, and `s3:ListMultipartUploadParts`. `HeadObject` is authorized through `s3:GetObject`. For SSE-KMS, the writer also needs the relevant key permissions for reads and uploads. `doctor` additionally needs `s3:GetBucketLocation`; a doctor failure does not substitute for the actual object readback checks.

The accompanying ingestion changes add reads and bucket-location access to the Intryc-owned resource policy and generated writer IAM policy. Deploy those changes and refresh existing bucket grants before customer release; existing customers must also attach the updated writer IAM policy. An unchanged authenticated setup PUT refreshes regional grants without replacing connections. This CLI does not modify AWS permissions. Until both policies allow reads, remote commands fail closed with actionable errors. Deletion is never required.

## Contract provenance and maintenance

The `contract/semantic.py`, `contract/json_payload.py` and JSON Schemas are an exact snapshot of AutoQA commit `f1b05b23dd4f91abdbd36686e44d62f90ed10e86`. The JSON decoder is copied from Bifrost's `json_document.py`; the local module name is retained for package compatibility. They intentionally do not import the Bifrost application. `contract/media.py` contains the same signature/extension checks. See `contract-provenance.json` for SHA-256 hashes and source paths. Deploy the reviewed importer alongside customer CLI rollout.

`uv run pytest` covers schema/semantic failures, local safety, manifest determinism, S3 resume/readback/publication and CLI reporting. Set `INTRYC_BIFROST_CONTRACT_ROOT=/path/to/AutoQA` when running tests to also compare the bundled contract against that reviewed source snapshot. Golden examples are bundled under `examples/delivery`. The CLI requires all local records to be valid, while Bifrost may reject individual records in a partial delivery.

CI builds distributions and tests the installed wheel on Linux and macOS across Python 3.11–3.14. Release workflows publish with PyPI Trusted Publishing and attestations after approval. Live cross-account S3/KMS testing, PyPI account configuration and a customer pilot remain first-release steps. No deployment, package publication or live customer upload is performed by the test suite.

## Distribution

See [publishing instructions](DISTRIBUTION.md) for PyPI setup, TestPyPI validation and tagged releases. Customer installation will use isolated `pipx`/`uv tool` environments once the first package release is published.
