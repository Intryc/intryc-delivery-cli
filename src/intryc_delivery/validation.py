"""Offline validation of the complete published inventory."""

import hashlib
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict
from referencing import Registry, Resource

from intryc_delivery.contract.json_payload import parse_json_object as load_json_object
from intryc_delivery.contract.media import (
    _filename_matches_content_type,
    content_type_matches,
    detect_content_type,
)
from intryc_delivery.contract.semantic import (
    BulkIngestionLimits,
    ContractValidationError,
    manifest_asset_matches,
    parse_manifest,
    parse_ticket,
)
from intryc_delivery.errors import DeliveryError, Issue
from intryc_delivery.filesystem import fingerprint, inventory, read_bounded

LIMITS = BulkIngestionLimits()


class LocalObject(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    bytes: int
    sha256: str
    content_type: str


class Delivery(BaseModel):
    root: Path
    delivery_id: str
    manifest: dict[str, Any]
    objects: list[LocalObject]


@lru_cache(maxsize=1)
def schema_validators() -> dict[str, Draft202012Validator]:
    schemas = {}
    for name in ("ticket-1.0.schema.json", "ticket-delivery-1.0.schema.json"):
        schemas[name] = load_json_object(
            files("intryc_delivery.contract").joinpath("schemas", name).read_bytes()
        )
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas.values()
    )
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    return {
        name: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
        for name, schema in schemas.items()
    }


def schema_issues(payload: dict, schema: str, path: str) -> list[Issue]:
    # Do not echo jsonschema's message: it embeds arbitrary customer values.
    return [
        Issue(
            code="SCHEMA_VALIDATION",
            message="A field does not comply with the published JSON Schema.",
            path=path,
            json_pointer="/"
            + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in error.absolute_path),
            corrective_action="Check the field type, allowed values and required fields in the schema.",
        )
        for error in schema_validators()[schema].iter_errors(payload)
    ]


def contract_issues(issues, path: str) -> list[Issue]:
    return [
        Issue(
            code=i.code,
            message="Contract validation failed: " + i.code.lower().replace("_", " ") + ".",
            path=path,
            source_ticket_id=i.source_ticket_id,
            json_pointer=i.json_pointer,
            corrective_action="Correct this field according to ticket/1.0 or ticket-delivery/1.0.",
        )
        for i in issues
    ]


def read_json(path: Path, limit: int) -> dict[str, Any]:
    content = read_bounded(path, limit)
    try:
        return load_json_object(content)
    except (ValueError, RecursionError):
        raise DeliveryError(
            [
                Issue(
                    code="INVALID_JSON",
                    message="Use a UTF-8 JSON object with unique keys and finite numbers.",
                )
            ]
        ) from None


def validate(root: Path, candidate: dict | None = None) -> Delivery:
    paths = inventory(root)
    issues: list[Issue] = []
    if candidate is None:
        if "manifest.json" not in paths:
            raise DeliveryError(
                [
                    Issue(
                        code="MISSING_MANIFEST", message="Generate manifest.json before validation."
                    )
                ]
            )
        manifest_bytes = read_bounded(paths["manifest.json"], LIMITS.manifest_bytes)
        try:
            payload = load_json_object(manifest_bytes)
        except (ValueError, RecursionError):
            raise DeliveryError(
                [Issue(code="INVALID_JSON", message="manifest.json must be strict UTF-8 JSON.")]
            ) from None
    else:
        from intryc_delivery.manifest import canonical_json

        payload = candidate
        manifest_bytes = canonical_json(payload)
        if len(manifest_bytes) > LIMITS.manifest_bytes:
            raise DeliveryError(
                [Issue(code="OBJECT_TOO_LARGE", message="Generated manifest exceeds 10 MiB.")]
            )
    issues.extend(schema_issues(payload, "ticket-delivery-1.0.schema.json", "manifest.json"))
    try:
        manifest = parse_manifest(payload, limits=LIMITS)
    except ContractValidationError as exc:
        issues.extend(contract_issues(exc.issues, "manifest.json"))
        raise DeliveryError(issues) from None
    issues.extend(contract_issues(manifest.issues, "manifest.json"))
    for rejected in manifest.rejected_tickets:
        issues.extend(contract_issues(rejected.issues, "manifest.json"))
    listed = {t.path for t in manifest.tickets} | set(manifest.assets) | {"manifest.json"}
    actual = set(paths) | ({"manifest.json"} if candidate is not None else set())
    for path in sorted(actual - listed):
        issues.append(
            Issue(code="UNLISTED_OBJECT", message="File is not listed in the manifest.", path=path)
        )
    for path in sorted(listed - actual):
        issues.append(
            Issue(code="MISSING_OBJECT", message="A manifest file is missing.", path=path)
        )
    objects = [
        LocalObject(
            path="manifest.json",
            bytes=len(manifest_bytes),
            sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            content_type="application/json",
        )
    ]
    references = set()
    identities: dict[tuple[str, str], str] = {}
    users: dict[str, dict] = {}
    for item in [*manifest.tickets, *manifest.assets.values()]:
        if item.path not in paths:
            continue
        content_type = getattr(item, "content_type", "application/json")
        limit = (
            LIMITS.ticket_bytes
            if item.path.startswith("ticket_details/")
            else (
                LIMITS.audio_bytes
                if content_type.startswith(("audio/", "video/"))
                else LIMITS.attachment_bytes
            )
        )
        try:
            size, sha, header = fingerprint(paths[item.path], limit)
            if size != item.bytes:
                issues.append(
                    Issue(
                        code="SIZE_MISMATCH",
                        message="Actual size differs from the manifest.",
                        path=item.path,
                    )
                )
            if sha != item.sha256:
                issues.append(
                    Issue(
                        code="CHECKSUM_MISMATCH",
                        message="Actual SHA-256 differs from the manifest.",
                        path=item.path,
                    )
                )
            if size != item.bytes or sha != item.sha256:
                continue
            objects.append(
                LocalObject(path=item.path, bytes=size, sha256=sha, content_type=content_type)
            )
            if item.path.startswith("assets/"):
                if not content_type_matches(content_type, detect_content_type(header)):
                    issues.append(
                        Issue(
                            code="MEDIA_TYPE_MISMATCH",
                            message="Detected media differs from the declared content type.",
                            path=item.path,
                        )
                    )
                continue
            ticket_data = read_json(paths[item.path], LIMITS.ticket_bytes)
            issues.extend(schema_issues(ticket_data, "ticket-1.0.schema.json", item.path))
            ticket = parse_ticket(
                ticket_data,
                expected_source_ticket_id=item.source_ticket_id,
                default_assignee_source_user_id=manifest.default_assignee_source_user_id,
            )
            attachment_ids = set()
            for ref in ticket.asset_references():
                references.add(ref["path"])
                if not manifest_asset_matches(ref, manifest.assets.get(ref["path"])):
                    issues.append(
                        Issue(
                            code="ASSET_MANIFEST_MISMATCH",
                            message="Asset reference must match path, size, SHA-256 and content type.",
                            path=item.path,
                        )
                    )
                if not _filename_matches_content_type(ref["file_name"], ref["content_type"]):
                    issues.append(
                        Issue(
                            code="FILE_EXTENSION_MISMATCH",
                            message="Filename extension must agree with the content type.",
                            path=item.path,
                        )
                    )
                if attachment_id := ref.get("source_attachment_id"):
                    if attachment_id in attachment_ids:
                        issues.append(
                            Issue(
                                code="DUPLICATE_ATTACHMENT_ID",
                                message="Attachment IDs must be unique in a ticket.",
                                path=item.path,
                            )
                        )
                    attachment_ids.add(attachment_id)
            for user in ticket.data["users"]:
                uid = user["source_user_id"]
                fields = dict(user.get("custom_fields", {}))
                fields.update(
                    {
                        key: user[key]
                        for key in ("active", "created_at", "updated_at")
                        if key in user
                    }
                )
                user_details = {
                    "name": user.get("name"),
                    "email": user.get("email"),
                    "role": user["role"],
                    "fields": fields,
                }
                if uid in users and users[uid] != user_details:
                    issues.append(
                        Issue(
                            code="USER_ID_REUSED",
                            message="The same user ID has conflicting details.",
                            path=item.path,
                        )
                    )
                users[uid] = user_details
            for kind, id_field in (
                ("comments", "source_comment_id"),
                ("calls", "source_call_id"),
                ("events", "source_event_id"),
                ("field_events", "source_event_id"),
                ("tag_events", "source_event_id"),
            ):
                for child in ticket.data.get(kind, []):
                    identity = (kind, child[id_field])
                    if identity in identities and identities[identity] != item.source_ticket_id:
                        issues.append(
                            Issue(
                                code="CHILD_ID_REUSED",
                                message="Child ID belongs to another ticket.",
                                path=item.path,
                            )
                        )
                    identities[identity] = item.source_ticket_id
        except ContractValidationError as exc:
            issues.extend(contract_issues(exc.issues, item.path))
        except DeliveryError as exc:
            issues.extend(i.model_copy(update={"path": item.path}) for i in exc.issues)
    for path in sorted(set(manifest.assets) - references):
        issues.append(
            Issue(code="ORPHAN_ASSET", message="No valid ticket references this asset.", path=path)
        )
    if issues:
        raise DeliveryError(issues)
    return Delivery(
        root=root,
        delivery_id=manifest.delivery_id,
        manifest=payload,
        objects=sorted(objects, key=lambda o: o.path),
    )
