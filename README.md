# Intryc delivery CLI

Prepare and send helpdesk tickets, calls and attachments to Intryc through S3.
The CLI checks your files, creates a manifest, uploads the delivery and marks it
ready only after verifying the uploaded contents.

You need Python 3.11 or later on Linux or macOS, an Intryc bulk-delivery connection,
and AWS credentials for its configured bucket and prefix.

## Install

Install the official [PyPI package](https://pypi.org/project/intryc-delivery/) with
[uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv tool install intryc-delivery
intryc-delivery --version
intryc-delivery --help
```

Alternatively, use `pipx install intryc-delivery`. To upgrade an installation made
with uv:

```sh
uv tool upgrade intryc-delivery
```

## Prepare your files

Use one directory for each delivery. Do not place configuration, credentials,
archives or ready markers inside it.

```text
delivery/
  manifest.json
  ticket_details/
    ticket-1001.json
  assets/
    calls/recording.wav
    attachments/example.txt
```

Include one JSON file per ticket. Put referenced recordings and attachments under
`assets/`. Omit that directory when there are no assets. Paths in JSON are relative
to the delivery directory and are case-sensitive.

For a first written ticket, save this as `delivery/ticket_details/ticket-1001.json`:

```json
{
  "schema_version": "ticket/1.0",
  "source_ticket_id": "ticket-1001",
  "created_at": "2026-01-01T10:00:00Z",
  "updated_at": "2026-01-01T10:30:00Z",
  "subject": "Example support conversation",
  "status": "closed",
  "assignee_source_user_id": "agent-1",
  "users": [
    {"source_user_id": "agent-1", "role": "agent", "email": "agent@example.com"},
    {"source_user_id": "customer-1", "role": "user", "name": "Example Customer"}
  ],
  "comments": [
    {
      "source_comment_id": "ticket-1001-comment-1",
      "created_at": "2026-01-01T10:01:00Z",
      "type": "message",
      "author_source_user_id": "customer-1",
      "body": {"format": "plain_text", "content": "An example customer message."}
    }
  ]
}
```

Replace the sample identifiers with stable IDs from your source system and the
assignee email with an active, eligible user in your Intryc workspace. Include
all referenced users in each ticket's `users` list. The CLI does not create Intryc
accounts or verify workspace eligibility.

[Complete examples](examples/delivery) show a written ticket, a call-only ticket
and a ticket with an attachment. Their data is synthetic; the WAV contains one
second of silence. Use a real recording when testing transcription.

For calls, use `calls[].media`; for attachments, use `comments[].attachments`.
Each reference must include `path`, `file_name`, `content_type`, `bytes` and
`sha256`. Attachments also need `source_attachment_id`, unique within the ticket.
Calculate size and SHA-256 over the actual asset bytes. The declared media type,
file signature and filename extension must agree. The CLI validates these fields
and does not rewrite ticket JSON for you.

See the [ticket schema](src/intryc_delivery/contract/schemas/ticket-1.0.schema.json)
and [manifest schema](src/intryc_delivery/contract/schemas/ticket-delivery-1.0.schema.json)
for supported fields and media types. These schemas are included in the package;
offline validation does not fetch a website.

## Validate and deliver

Generate a manifest after your ticket files and assets are ready. Choose a fresh
delivery ID for each new delivery:

```sh
intryc-delivery manifest ./delivery --delivery-id delivery-001
intryc-delivery validate ./delivery
intryc-delivery doctor --bucket your-bucket --prefix your-prefix --profile delivery --region us-east-1
intryc-delivery deliver ./delivery --bucket your-bucket --prefix your-prefix --profile delivery --region us-east-1
```

Use the exact bucket, prefix and Region from your Intryc connection. If the
manifest is missing, `deliver` can create it when you supply `--delivery-id`.

Files upload to `<prefix>/deliveries/<delivery-id>/`. After all tickets and assets,
the CLI uploads the manifest and reads every remote file back to verify its size,
SHA-256 and Content-Type. It then creates the empty
`<prefix>/_ready/<delivery-id>` marker. An ETag or custom checksum label alone is
not accepted as proof that the bytes match.

Successful publication means the files are ready for Intryc to process. Ask your
Intryc contact to confirm import enablement and your first delivery's outcome;
publication does not mean import, transcription or evaluation has finished.

## Commands

| Command | What it does |
| --- | --- |
| `validate DIRECTORY` | Checks JSON, fields, file inventory, sizes, hashes and media entirely offline. |
| `manifest DIRECTORY --delivery-id ID` | Creates the manifest atomically. Replacing an incompatible existing manifest requires `--force`. |
| `upload DIRECTORY` | Uploads or resumes files and verifies them, without marking the delivery ready. |
| `publish DIRECTORY` | Verifies the remote delivery and creates the ready marker. |
| `deliver DIRECTORY` | Creates a missing manifest, validates, uploads, verifies and publishes. |
| `doctor` | Checks credentials, bucket Region and prefix listing. |

Use `--dry-run` to validate without changing files or contacting AWS.
`doctor --probe` additionally writes and reads a small test object in a separate,
unpublished delivery. It creates no ready marker and retains the object for
lifecycle cleanup.

## AWS credentials and access

Use a named AWS profile, SSO profile, web identity or workload role. The CLI uses
the standard AWS credential chain; never put access keys or session tokens in
ticket files or CLI configuration.

Role profiles and workload providers renew their temporary credentials while the
source identity remains valid. SSO or MFA may require another interactive sign-in.
Temporary keys exported directly into environment variables cannot renew
themselves: obtain fresh credentials and resume the unchanged delivery if they expire.

Your uploader needs access to its delivery prefix for `s3:ListBucket`,
`s3:GetObject`, `s3:PutObject`, `s3:AbortMultipartUpload` and
`s3:ListMultipartUploadParts`. `HeadObject` uses the GetObject permission.
`doctor` also needs `s3:GetBucketLocation`. Encrypted customer-owned buckets need
the corresponding KMS read/upload permissions.

For Intryc-owned buckets, use the current writer policy supplied during setup.
Older upload-only grants need a policy update and an Intryc-side access refresh
before using verified uploads. Contact Intryc if readback is denied. The CLI
never changes permissions, requires deletion or bypasses verification.

## Retries and corrections

- Use one writer per delivery ID and do not edit files while a command runs.
  Concurrent processes on the same machine are rejected; separate hosts must
  coordinate or use different delivery IDs.
- If an upload stops before publication, rerun the same command with unchanged
  local files. Missing or mismatched unpublished objects are resumed.
- A published delivery is immutable. Repeating an identical delivery verifies
  its remote contents and reports `already_published`; conflicting reuse fails.
- Correct rejected content under a new delivery ID. Keep original source IDs for
  records that were never accepted. Accepted tickets cannot be updated or deleted
  through this tool; coordinate corrections with Intryc.
- Do not use `manifest --force` or remote cleanup to change a published delivery.

## Configuration and automation

You can place `intryc-delivery.toml` outside the delivery directory:

```toml
bucket = "your-bucket"
prefix = "your-prefix"
region = "us-east-1"
profile = "delivery"
concurrency = 4
```

Flags override `INTRYC_DELIVERY_*` environment variables, which override the
configuration file. Use `--config PATH` for a different file. Other supported
settings are `endpoint_url`, `json_output` and `no_progress`. Unknown settings
are rejected. Use `--endpoint-url` only for a compatible test endpoint; remote
endpoints require HTTPS and conditional writes.

`--json` returns a single machine-readable result on stdout. Progress goes to
stderr; `--no-progress` suppresses it. `INTRYC_DELIVERY_JSON=true` also enables JSON
output. Exit codes are `0` for success, `1` for invalid delivery content, `2` for
filesystem/configuration errors and `3` for AWS/transport failures.

The CLI sends no analytics. Avoid including sensitive data in filenames, IDs or
support reports. For delivery or access issues, contact Intryc with the delivery ID
and error code. Report reproducible tool bugs through [GitHub Issues](https://github.com/Intryc/intryc-delivery-cli/issues),
without credentials, ticket contents or customer data.

## License

[MIT](LICENSE).
