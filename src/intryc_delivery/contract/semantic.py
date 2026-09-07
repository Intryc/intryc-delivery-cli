from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


MANIFEST_SCHEMA_VERSION = "ticket-delivery/1.0"
TICKET_SCHEMA_VERSION = "ticket/1.0"
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
RFC3339_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-5][0-9])$"
)


class ErrorScope(StrEnum):
    DELIVERY = "delivery"
    TICKET = "ticket"
    ASSET = "asset"


class DeliveryStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    EXTRACTING = "EXTRACTING"
    TRANSFORMING = "TRANSFORMING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    REJECTED = "REJECTED"
    SYSTEM_FAILED = "SYSTEM_FAILED"


class DeliveryItemStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class BulkIngestionLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest_bytes: int = 10 * 1024 * 1024
    ticket_bytes: int = 10 * 1024 * 1024
    max_tickets: int = 10_000
    max_assets: int = 50_000
    total_delivery_bytes: int = 100 * 1024 * 1024 * 1024
    audio_bytes: int = 1024 * 1024 * 1024
    attachment_bytes: int = 25 * 1024 * 1024
    object_workers: int = Field(default=4, ge=1, le=16)


class ContractIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: ErrorScope
    code: str
    message: str
    json_pointer: str | None = None
    source_ticket_id: str | None = None
    relative_path: str | None = None
    corrective_action: str | None = None

    def sort_key(self) -> tuple[str, str, str, str]:
        return (
            self.source_ticket_id or "",
            self.relative_path or "",
            self.json_pointer or "",
            self.code,
        )


class ContractValidationError(ValueError):
    def __init__(self, issues: list[ContractIssue]):
        self.issues = sorted(issues, key=ContractIssue.sort_key)
        super().__init__(self.issues[0].message if self.issues else "Invalid contract")


class ManifestTicket(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_key: str
    source_ticket_id: str
    path: str
    bytes: int
    sha256: str


class ManifestAsset(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    bytes: int
    sha256: str
    content_type: str


class RejectedManifestTicket(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_key: str
    source_ticket_id: str | None
    path: str | None
    issues: tuple[ContractIssue, ...]


class DeliveryManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    delivery_id: str
    created_at: datetime
    default_assignee_source_user_id: str | None
    tickets: list[ManifestTicket]
    rejected_tickets: list[RejectedManifestTicket]
    assets: dict[str, ManifestAsset]
    total_ticket_entries: int
    declared_bytes: int
    issues: list[ContractIssue] = Field(default_factory=list)


class ValidatedTicket(BaseModel):
    model_config = ConfigDict(frozen=True)

    data: dict[str, Any]
    source_ticket_id: str
    created_at: datetime
    updated_at: datetime
    assignee_email: str

    def asset_references(self) -> list[dict[str, Any]]:
        references: list[dict[str, Any]] = []
        for comment in self.data.get("comments", []):
            references.extend(comment.get("attachments", []))
        for call in self.data.get("calls", []):
            if media := call.get("media"):
                references.append(media)
        return references


_MANIFEST_FIELDS = {
    "schema_version",
    "delivery_id",
    "created_at",
    "mode",
    "defaults",
    "counts",
    "tickets",
    "assets",
}
_TICKET_FIELDS = {
    "schema_version",
    "source_ticket_id",
    "subject",
    "created_at",
    "updated_at",
    "source",
    "status",
    "priority",
    "group",
    "rating",
    "tags",
    "custom_fields",
    "users",
    "assignee_source_user_id",
    "requester_source_user_id",
    "comments",
    "calls",
    "events",
    "field_events",
    "tag_events",
}
_USER_FIELDS = {
    "source_user_id",
    "name",
    "email",
    "role",
    "active",
    "created_at",
    "updated_at",
    "custom_fields",
}
_ASSET_FIELDS = {
    "source_attachment_id",
    "path",
    "file_name",
    "content_type",
    "bytes",
    "sha256",
}
_MEDIA_FIELDS = {"path", "file_name", "content_type", "bytes", "sha256"}
_COMMENT_FIELDS = {
    "source_comment_id",
    "created_at",
    "type",
    "source",
    "body",
    "author_source_user_id",
    "attachments",
}
_CALL_FIELDS = {
    "source_call_id",
    "created_at",
    "source",
    "direction",
    "fields",
    "media",
}
_EVENT_FIELDS = {
    "source_event_id",
    "created_at",
    "type",
    "fields",
    "actor_source_user_id",
}
_FIELD_EVENT_FIELDS = (_EVENT_FIELDS - {"fields"}) | {
    "field_name",
    "previous_value",
    "value",
}
_TAG_EVENT_FIELDS = (_EVENT_FIELDS - {"fields"}) | {"previous_value", "value"}
_ROLES = {"user", "agent", "admin", "bot", "system"}
_BODY_FORMATS = {"plain_text", "html"}
_COMMENT_TYPES = {"message", "note"}
_DIRECTIONS = {"inbound", "outbound"}
_FIELD_EVENT_TYPES = {"change", "update", "add", "remove"}
_TAG_EVENT_TYPES = {"change", "update", "add", "remove"}
_GENERAL_EVENT_TYPES = {
    "assigned",
    "created",
    "custom",
    "group_changed",
    "priority_changed",
    "requester_changed",
    "status_changed",
    "unassigned",
    "updated",
}
_AUDIO_CONTENT_TYPES = {
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav",
    "audio/flac",
    "audio/ogg",
    "video/mp4",
}
_ATTACHMENT_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "text/csv",
    "text/plain",
}


def _issue(
    scope: ErrorScope,
    code: str,
    message: str,
    *,
    pointer: str | None = None,
    ticket_id: str | None = None,
    path: str | None = None,
    action: str | None = None,
) -> ContractIssue:
    return ContractIssue(
        scope=scope,
        code=code,
        message=message,
        json_pointer=pointer,
        source_ticket_id=ticket_id,
        relative_path=path,
        corrective_action=action,
    )


def parse_rfc3339(
    value: Any, *, pointer: str, scope: ErrorScope, ticket_id: str | None = None
) -> datetime:
    if not isinstance(value, str) or not RFC3339_PATTERN.fullmatch(value):
        raise ContractValidationError(
            [
                _issue(
                    scope,
                    "INVALID_TIMESTAMP",
                    "Expected an RFC 3339 timestamp.",
                    pointer=pointer,
                    ticket_id=ticket_id,
                )
            ]
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Every timestamp is eventually stored or normalized in UTC. Reject an
        # offset that would overflow that conversion as customer-controlled data.
        parsed.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise ContractValidationError(
            [
                _issue(
                    scope,
                    "INVALID_TIMESTAMP",
                    "Expected an RFC 3339 timestamp.",
                    pointer=pointer,
                    ticket_id=ticket_id,
                )
            ]
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractValidationError(
            [
                _issue(
                    scope,
                    "TIMESTAMP_OFFSET_REQUIRED",
                    "Timestamp must include a UTC offset.",
                    pointer=pointer,
                    ticket_id=ticket_id,
                )
            ]
        )
    return parsed


def validate_relative_path(value: Any, *, expected_root: str | None = None) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith(("/", "s3://"))
        or "\\" in value
        or any(character in value for character in "*?[]")
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("Expected a normalized relative path")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts) or str(path) != value:
        raise ValueError("Expected a normalized relative path")
    if expected_root and (len(path.parts) < 2 or path.parts[0] != expected_root):
        raise ValueError(f"Expected a path below {expected_root}/")
    return value


def _safe_relative_error_path(
    value: Any, *, expected_root: str | None = None
) -> str | None:
    try:
        return validate_relative_path(value, expected_root=expected_root)
    except ValueError:
        return None


def _require_string(value: Any, *, name: str, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise ValueError(f"{name} cannot exceed {max_length} characters")
    return normalized


def _require_nonnegative_int(
    value: Any, *, name: str, maximum: int | None = None
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} exceeds the supported limit")
    return value


def _require_sha256(value: Any) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ValueError("sha256 must be a 64-character hexadecimal digest")
    return value


def _validate_exact_fields(
    payload: dict[str, Any],
    allowed: set[str],
    pointer: str,
    issues: list[ContractIssue],
    *,
    scope: ErrorScope,
    ticket_id: str | None = None,
    path: str | None = None,
) -> None:
    for key in sorted(set(payload) - allowed):
        issues.append(
            _issue(
                scope,
                "UNKNOWN_FIELD",
                f"Unknown field: {key}.",
                pointer=f"{pointer}/{key}",
                ticket_id=ticket_id,
                path=path,
            )
        )


def parse_manifest(payload: Any, *, limits: BulkIngestionLimits) -> DeliveryManifest:
    root_issues: list[ContractIssue] = []
    if not isinstance(payload, dict):
        raise ContractValidationError(
            [
                _issue(
                    ErrorScope.DELIVERY,
                    "INVALID_MANIFEST",
                    "manifest.json must contain a JSON object.",
                )
            ]
        )
    _validate_exact_fields(
        payload, _MANIFEST_FIELDS, "", root_issues, scope=ErrorScope.DELIVERY
    )
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        root_issues.append(
            _issue(
                ErrorScope.DELIVERY,
                "UNSUPPORTED_SCHEMA_VERSION",
                f"schema_version must be {MANIFEST_SCHEMA_VERSION}.",
                pointer="/schema_version",
            )
        )
    if payload.get("mode") != "create_only":
        root_issues.append(
            _issue(
                ErrorScope.DELIVERY,
                "UNSUPPORTED_MODE",
                "mode must be create_only.",
                pointer="/mode",
            )
        )
    try:
        delivery_id = _require_string(
            payload.get("delivery_id"), name="delivery_id", max_length=255
        )
        validate_relative_path(delivery_id)
        if "/" in delivery_id:
            raise ValueError("delivery_id must be one normalized path segment")
    except ValueError as exc:
        delivery_id = ""
        root_issues.append(
            _issue(
                ErrorScope.DELIVERY,
                "INVALID_DELIVERY_ID",
                str(exc),
                pointer="/delivery_id",
            )
        )
    try:
        created_at = parse_rfc3339(
            payload.get("created_at"), pointer="/created_at", scope=ErrorScope.DELIVERY
        )
    except ContractValidationError as exc:
        created_at = datetime.min.replace(tzinfo=UTC)
        root_issues.extend(exc.issues)

    defaults = payload.get("defaults", {})
    default_assignee_source_user_id: str | None = None
    if not isinstance(defaults, dict):
        root_issues.append(
            _issue(
                ErrorScope.DELIVERY,
                "INVALID_DEFAULTS",
                "defaults must be an object.",
                pointer="/defaults",
            )
        )
    else:
        _validate_exact_fields(
            defaults,
            {"assignee_source_user_id"},
            "/defaults",
            root_issues,
            scope=ErrorScope.DELIVERY,
        )
        if "assignee_source_user_id" in defaults:
            value = defaults.get("assignee_source_user_id")
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value.strip()) > 512
            ):
                root_issues.append(
                    _issue(
                        ErrorScope.DELIVERY,
                        "INVALID_ASSIGNEE_SOURCE_USER_ID",
                        "defaults.assignee_source_user_id must be a non-empty string.",
                        pointer="/defaults/assignee_source_user_id",
                    )
                )
            else:
                default_assignee_source_user_id = value.strip()

    raw_tickets = payload.get("tickets")
    raw_assets = payload.get("assets", [])
    counts = payload.get("counts")
    if not isinstance(raw_tickets, list) or not raw_tickets:
        root_issues.append(
            _issue(
                ErrorScope.DELIVERY,
                "TICKETS_REQUIRED",
                "tickets must be a non-empty array.",
                pointer="/tickets",
            )
        )
        raw_tickets = []
    if len(raw_tickets) > limits.max_tickets:
        root_issues.append(
            _issue(
                ErrorScope.DELIVERY,
                "TOO_MANY_TICKETS",
                f"A delivery can contain at most {limits.max_tickets} tickets.",
                pointer="/tickets",
            )
        )
    if not isinstance(raw_assets, list):
        root_issues.append(
            _issue(
                ErrorScope.DELIVERY,
                "INVALID_ASSETS",
                "assets must be an array.",
                pointer="/assets",
            )
        )
        raw_assets = []
    if len(raw_assets) > limits.max_assets:
        root_issues.append(
            _issue(
                ErrorScope.DELIVERY,
                "TOO_MANY_ASSETS",
                f"A delivery can contain at most {limits.max_assets} assets.",
                pointer="/assets",
            )
        )

    if not isinstance(counts, dict):
        root_issues.append(
            _issue(
                ErrorScope.DELIVERY,
                "COUNTS_REQUIRED",
                "counts must be an object.",
                pointer="/counts",
            )
        )
    else:
        _validate_exact_fields(
            counts,
            {"tickets", "assets"},
            "/counts",
            root_issues,
            scope=ErrorScope.DELIVERY,
        )
        for field_name, expected in (
            ("tickets", len(raw_tickets)),
            ("assets", len(raw_assets)),
        ):
            value = counts.get(field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or value != expected
            ):
                root_issues.append(
                    _issue(
                        ErrorScope.DELIVERY,
                        "COUNT_MISMATCH",
                        f"counts.{field_name} must match the {field_name} array.",
                        pointer=f"/counts/{field_name}",
                    )
                )

    if root_issues:
        raise ContractValidationError(root_issues)

    item_issues: list[ContractIssue] = []
    tickets: list[ManifestTicket] = []
    rejected_tickets: list[RejectedManifestTicket] = []
    for index, item in enumerate(raw_tickets):
        pointer = f"/tickets/{index}"
        item_key = str(index)
        if not isinstance(item, dict):
            issues = _issue(
                ErrorScope.TICKET,
                "INVALID_MANIFEST_TICKET",
                "Ticket inventory entry must be an object.",
                pointer=pointer,
            )
            item_issues.append(issues)
            rejected_tickets.append(
                RejectedManifestTicket(
                    item_key=item_key,
                    source_ticket_id=None,
                    path=None,
                    issues=(issues,),
                )
            )
            continue
        entry_issues: list[ContractIssue] = []
        raw_source_id = (
            item.get("source_ticket_id")
            if isinstance(item.get("source_ticket_id"), str)
            else None
        )
        raw_path = item.get("path") if isinstance(item.get("path"), str) else None
        safe_raw_path = _safe_relative_error_path(
            raw_path,
            expected_root="ticket_details",
        )
        _validate_exact_fields(
            item,
            {"source_ticket_id", "path", "bytes", "sha256"},
            pointer,
            entry_issues,
            scope=ErrorScope.TICKET,
            ticket_id=raw_source_id,
        )
        try:
            source_id = _require_string(
                item.get("source_ticket_id"), name="source_ticket_id"
            )
            path = validate_relative_path(
                item.get("path"), expected_root="ticket_details"
            )
            if not path.endswith(".json"):
                raise ValueError("Ticket paths must end in .json")
            size = _require_nonnegative_int(
                item.get("bytes"), name="bytes", maximum=limits.ticket_bytes
            )
            checksum = _require_sha256(item.get("sha256"))
        except ValueError as exc:
            entry_issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_MANIFEST_TICKET",
                    str(exc),
                    pointer=pointer,
                    ticket_id=raw_source_id,
                    path=safe_raw_path,
                )
            )
        if entry_issues:
            item_issues.extend(entry_issues)
            rejected_tickets.append(
                RejectedManifestTicket(
                    item_key=item_key,
                    source_ticket_id=(
                        (raw_source_id.strip() or None) if raw_source_id else None
                    ),
                    path=safe_raw_path,
                    issues=tuple(sorted(entry_issues, key=ContractIssue.sort_key)),
                )
            )
            continue
        tickets.append(
            ManifestTicket(
                item_key=item_key,
                source_ticket_id=source_id,
                path=path,
                bytes=size,
                sha256=checksum,
            )
        )

    assets: list[ManifestAsset] = []
    for index, item in enumerate(raw_assets):
        pointer = f"/assets/{index}"
        if not isinstance(item, dict):
            item_issues.append(
                _issue(
                    ErrorScope.ASSET,
                    "INVALID_MANIFEST_ASSET",
                    "Asset inventory entry must be an object.",
                    pointer=pointer,
                )
            )
            continue
        entry_issues = []
        safe_asset_path = _safe_relative_error_path(
            item.get("path"), expected_root="assets"
        )
        _validate_exact_fields(
            item,
            {"path", "bytes", "sha256", "content_type"},
            pointer,
            entry_issues,
            scope=ErrorScope.ASSET,
            path=safe_asset_path,
        )
        try:
            path = validate_relative_path(item.get("path"), expected_root="assets")
            content_type = _require_string(
                item.get("content_type"), name="content_type", max_length=255
            ).lower()
            maximum = (
                limits.audio_bytes
                if content_type in _AUDIO_CONTENT_TYPES
                else limits.attachment_bytes
            )
            size = _require_nonnegative_int(
                item.get("bytes"), name="bytes", maximum=maximum
            )
            checksum = _require_sha256(item.get("sha256"))
            if content_type not in _AUDIO_CONTENT_TYPES | _ATTACHMENT_CONTENT_TYPES:
                raise ValueError("content_type is not supported")
        except ValueError as exc:
            entry_issues.append(
                _issue(
                    ErrorScope.ASSET,
                    "INVALID_MANIFEST_ASSET",
                    str(exc),
                    pointer=pointer,
                    path=safe_asset_path,
                )
            )
        if entry_issues:
            item_issues.extend(entry_issues)
            continue
        assets.append(
            ManifestAsset(
                path=path,
                bytes=size,
                sha256=checksum,
                content_type=content_type,
            )
        )

    duplicate_ticket_ids = {
        value
        for value, count in Counter(
            item["source_ticket_id"].strip()
            for item in raw_tickets
            if isinstance(item, dict)
            and isinstance(item.get("source_ticket_id"), str)
            and item["source_ticket_id"].strip()
        ).items()
        if count > 1
    }
    raw_ticket_paths: list[str] = []
    for item in raw_tickets:
        if not isinstance(item, dict):
            continue
        candidate_path = _safe_relative_error_path(
            item.get("path"),
            expected_root="ticket_details",
        )
        if candidate_path is not None:
            raw_ticket_paths.append(candidate_path)
    duplicate_paths = {
        value for value, count in Counter(raw_ticket_paths).items() if count > 1
    }
    enriched_rejected_tickets: list[RejectedManifestTicket] = []
    for rejected in rejected_tickets:
        duplicate_issues: list[ContractIssue] = []
        if rejected.source_ticket_id in duplicate_ticket_ids:
            duplicate_issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "DUPLICATE_SOURCE_TICKET_ID",
                    "Every source_ticket_id must be unique within a delivery.",
                    ticket_id=rejected.source_ticket_id,
                    path=rejected.path,
                )
            )
        if rejected.path in duplicate_paths:
            duplicate_issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "DUPLICATE_TICKET_PATH",
                    "Every ticket path must be unique within a delivery.",
                    ticket_id=rejected.source_ticket_id,
                    path=rejected.path,
                )
            )
        item_issues.extend(duplicate_issues)
        enriched_rejected_tickets.append(
            RejectedManifestTicket(
                item_key=rejected.item_key,
                source_ticket_id=rejected.source_ticket_id,
                path=rejected.path,
                issues=tuple(
                    sorted(
                        (*rejected.issues, *duplicate_issues),
                        key=ContractIssue.sort_key,
                    )
                ),
            )
        )
    rejected_tickets = enriched_rejected_tickets
    unique_tickets: list[ManifestTicket] = []
    for ticket in tickets:
        duplicate_issues: list[ContractIssue] = []
        if ticket.source_ticket_id in duplicate_ticket_ids:
            duplicate_issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "DUPLICATE_SOURCE_TICKET_ID",
                    "Every source_ticket_id must be unique within a delivery.",
                    ticket_id=ticket.source_ticket_id,
                    path=ticket.path,
                )
            )
        if ticket.path in duplicate_paths:
            duplicate_issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "DUPLICATE_TICKET_PATH",
                    "Every ticket path must be unique within a delivery.",
                    ticket_id=ticket.source_ticket_id,
                    path=ticket.path,
                )
            )
        if duplicate_issues:
            item_issues.extend(duplicate_issues)
            rejected_tickets.append(
                RejectedManifestTicket(
                    item_key=ticket.item_key,
                    source_ticket_id=ticket.source_ticket_id,
                    path=ticket.path,
                    issues=tuple(sorted(duplicate_issues, key=ContractIssue.sort_key)),
                )
            )
        else:
            unique_tickets.append(ticket)
    tickets = unique_tickets

    raw_asset_paths: list[str] = []
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        candidate_path = _safe_relative_error_path(
            item.get("path"), expected_root="assets"
        )
        if candidate_path is not None:
            raw_asset_paths.append(candidate_path)
    duplicate_asset_paths = {
        value for value, count in Counter(raw_asset_paths).items() if count > 1
    }
    for duplicate in sorted(duplicate_asset_paths):
        item_issues.append(
            _issue(
                ErrorScope.ASSET,
                "DUPLICATE_ASSET_PATH",
                "Every asset path must be unique within a delivery.",
                path=duplicate,
            )
        )
    assets = [asset for asset in assets if asset.path not in duplicate_asset_paths]

    declared_bytes = sum(
        item.get("bytes", 0)
        for item in raw_tickets
        if isinstance(item, dict)
        and isinstance(item.get("bytes"), int)
        and not isinstance(item.get("bytes"), bool)
        and item["bytes"] >= 0
    ) + sum(
        item.get("bytes", 0)
        for item in raw_assets
        if isinstance(item, dict)
        and isinstance(item.get("bytes"), int)
        and not isinstance(item.get("bytes"), bool)
        and item["bytes"] >= 0
    )
    if declared_bytes > limits.total_delivery_bytes:
        raise ContractValidationError(
            [
                _issue(
                    ErrorScope.DELIVERY,
                    "DELIVERY_TOO_LARGE",
                    "The delivery exceeds the supported total byte limit.",
                )
            ]
        )

    return DeliveryManifest(
        delivery_id=delivery_id,
        created_at=created_at,
        default_assignee_source_user_id=default_assignee_source_user_id,
        tickets=tickets,
        rejected_tickets=sorted(
            rejected_tickets,
            key=lambda item: int(item.item_key),
        ),
        assets={asset.path: asset for asset in assets},
        total_ticket_entries=len(raw_tickets),
        declared_bytes=declared_bytes,
        issues=sorted(item_issues, key=ContractIssue.sort_key),
    )


def _validate_user(
    value: Any,
    *,
    pointer: str,
    ticket_id: str,
    issues: list[ContractIssue],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_USER",
                "User must be an object.",
                pointer=pointer,
                ticket_id=ticket_id,
            )
        )
        return None
    _validate_exact_fields(
        value,
        _USER_FIELDS,
        pointer,
        issues,
        scope=ErrorScope.TICKET,
        ticket_id=ticket_id,
    )
    source_id = value.get("source_user_id")
    email = value.get("email")
    if (
        not isinstance(source_id, str)
        or not source_id.strip()
        or len(source_id.strip()) > 512
    ):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_SOURCE_USER_ID",
                "source_user_id must be a non-empty string.",
                pointer=f"{pointer}/source_user_id",
                ticket_id=ticket_id,
            )
        )
    if email is not None and (
        not isinstance(email, str) or not EMAIL_PATTERN.fullmatch(email.strip())
    ):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_USER_EMAIL",
                "email must be valid.",
                pointer=f"{pointer}/email",
                ticket_id=ticket_id,
            )
        )
    for field_name in ("name",):
        field_value = value.get(field_name)
        if field_value is not None and (
            not isinstance(field_value, str)
            or not field_value.strip()
            or len(field_value.strip()) > 1024
        ):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_USER_FIELD",
                    f"{field_name} must be a non-empty string of at most 1024 characters.",
                    pointer=f"{pointer}/{field_name}",
                    ticket_id=ticket_id,
                )
            )
    role = value.get("role")
    if not isinstance(role, str) or role not in _ROLES:
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_USER_ROLE",
                f"role must be one of: {', '.join(sorted(_ROLES))}.",
                pointer=f"{pointer}/role",
                ticket_id=ticket_id,
            )
        )
    normalized = dict(value)
    if isinstance(source_id, str):
        normalized["source_user_id"] = source_id.strip()
    if isinstance(email, str):
        normalized["email"] = email.strip().lower()
    for field_name in ("name",):
        if isinstance(value.get(field_name), str):
            normalized[field_name] = value[field_name].strip()
    active = value.get("active")
    if active is not None and not isinstance(active, bool):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_USER_ACTIVE",
                "active must be a boolean.",
                pointer=f"{pointer}/active",
                ticket_id=ticket_id,
            )
        )
    parsed_timestamps: dict[str, datetime] = {}
    for field_name in ("created_at", "updated_at"):
        timestamp = value.get(field_name)
        if timestamp is None:
            continue
        try:
            parsed_timestamps[field_name] = parse_rfc3339(
                timestamp,
                pointer=f"{pointer}/{field_name}",
                scope=ErrorScope.TICKET,
                ticket_id=ticket_id,
            )
            normalized[field_name] = (
                parsed_timestamps[field_name]
                .astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except ContractValidationError as exc:
            issues.extend(exc.issues)
    if (
        parsed_timestamps.get("updated_at") is not None
        and parsed_timestamps.get("created_at") is not None
        and parsed_timestamps["updated_at"] < parsed_timestamps["created_at"]
    ):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_USER_TIME_RANGE",
                "updated_at cannot be earlier than created_at.",
                pointer=f"{pointer}/updated_at",
                ticket_id=ticket_id,
            )
        )
    custom_fields = value.get("custom_fields", {})
    if not isinstance(custom_fields, dict) or any(
        not isinstance(key, str) or not key.strip() for key in custom_fields
    ):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_USER_CUSTOM_FIELDS",
                "custom_fields must be an object with non-empty string keys.",
                pointer=f"{pointer}/custom_fields",
                ticket_id=ticket_id,
            )
        )
    elif any(
        field_value is not None
        and (
            isinstance(field_value, (dict, list))
            or not isinstance(field_value, (str, int, float, bool))
        )
        for field_value in custom_fields.values()
    ):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_USER_CUSTOM_FIELD_VALUE",
                "User custom-field values must be strings, numbers, booleans, or null.",
                pointer=f"{pointer}/custom_fields",
                ticket_id=ticket_id,
            )
        )
    return normalized


def _resolve_user_reference(
    value: Any,
    *,
    pointer: str,
    ticket_id: str,
    users_by_id: dict[str, dict[str, Any]],
    issues: list[ContractIssue],
    required_code: str | None = None,
    required_message: str | None = None,
) -> dict[str, Any] | None:
    if value is None:
        if required_code:
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    required_code,
                    required_message or "A user reference is required.",
                    pointer=pointer,
                    ticket_id=ticket_id,
                )
            )
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_USER_REFERENCE",
                "User references must be non-empty source_user_id strings.",
                pointer=pointer,
                ticket_id=ticket_id,
            )
        )
        return None
    source_user_id = value.strip()
    user = users_by_id.get(source_user_id)
    if user is None:
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "UNKNOWN_USER_REFERENCE",
                "The referenced source_user_id is not present in users.",
                pointer=pointer,
                ticket_id=ticket_id,
            )
        )
    return user


def _validate_asset_reference(
    value: Any,
    *,
    pointer: str,
    ticket_id: str,
    issues: list[ContractIssue],
    media: bool,
) -> dict[str, Any] | None:
    allowed = _MEDIA_FIELDS if media else _ASSET_FIELDS
    if not isinstance(value, dict):
        issues.append(
            _issue(
                ErrorScope.ASSET,
                "INVALID_ASSET_REFERENCE",
                "Asset reference must be an object.",
                pointer=pointer,
                ticket_id=ticket_id,
            )
        )
        return None
    _validate_exact_fields(
        value, allowed, pointer, issues, scope=ErrorScope.ASSET, ticket_id=ticket_id
    )
    normalized = dict(value)
    try:
        if not media:
            normalized["source_attachment_id"] = _require_string(
                value.get("source_attachment_id"), name="source_attachment_id"
            )
        normalized["path"] = validate_relative_path(
            value.get("path"), expected_root="assets"
        )
        normalized["file_name"] = _require_string(
            value.get("file_name"), name="file_name", max_length=255
        )
        if (
            normalized["file_name"] in {".", ".."}
            or "\\" in normalized["file_name"]
            or PurePosixPath(normalized["file_name"]).name != normalized["file_name"]
            or any(
                ord(char) < 32 or ord(char) == 127 for char in normalized["file_name"]
            )
        ):
            raise ValueError("file_name must not contain path or control characters")
        normalized["content_type"] = _require_string(
            value.get("content_type"), name="content_type", max_length=255
        ).lower()
        allowed_types = _AUDIO_CONTENT_TYPES if media else _ATTACHMENT_CONTENT_TYPES
        if normalized["content_type"] not in allowed_types:
            raise ValueError("content_type is not supported")
        normalized["bytes"] = _require_nonnegative_int(value.get("bytes"), name="bytes")
        normalized["sha256"] = _require_sha256(value.get("sha256"))
    except ValueError as exc:
        issues.append(
            _issue(
                ErrorScope.ASSET,
                "INVALID_ASSET_REFERENCE",
                str(exc),
                pointer=pointer,
                ticket_id=ticket_id,
                path=_safe_relative_error_path(
                    value.get("path"),
                    expected_root="assets",
                ),
            )
        )
        return None
    return normalized


def parse_ticket(
    payload: Any,
    *,
    expected_source_ticket_id: str,
    default_assignee_source_user_id: str | None = None,
) -> ValidatedTicket:
    issues: list[ContractIssue] = []
    if not isinstance(payload, dict):
        raise ContractValidationError(
            [
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_TICKET_JSON",
                    "Ticket file must contain a JSON object.",
                    ticket_id=expected_source_ticket_id,
                )
            ]
        )
    _validate_exact_fields(
        payload,
        _TICKET_FIELDS,
        "",
        issues,
        scope=ErrorScope.TICKET,
        ticket_id=expected_source_ticket_id,
    )
    if payload.get("schema_version") != TICKET_SCHEMA_VERSION:
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "UNSUPPORTED_SCHEMA_VERSION",
                f"schema_version must be {TICKET_SCHEMA_VERSION}.",
                pointer="/schema_version",
                ticket_id=expected_source_ticket_id,
            )
        )
    source_id = payload.get("source_ticket_id")
    if source_id != expected_source_ticket_id:
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "SOURCE_TICKET_ID_MISMATCH",
                "Manifest and ticket source_ticket_id values must match.",
                pointer="/source_ticket_id",
                ticket_id=expected_source_ticket_id,
            )
        )
    for field_name in ("subject", "source", "status", "priority", "group", "rating"):
        value = payload.get(field_name)
        if value is not None and (
            not isinstance(value, str) or not value.strip() or len(value) > 1_024
        ):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_STRING_FIELD",
                    f"{field_name} must be a non-empty string of at most 1024 characters.",
                    pointer=f"/{field_name}",
                    ticket_id=expected_source_ticket_id,
                )
            )

    try:
        created_at = parse_rfc3339(
            payload.get("created_at"),
            pointer="/created_at",
            scope=ErrorScope.TICKET,
            ticket_id=expected_source_ticket_id,
        )
    except ContractValidationError as exc:
        issues.extend(exc.issues)
        created_at = datetime.min.replace(tzinfo=UTC)
    try:
        updated_at = parse_rfc3339(
            payload.get("updated_at"),
            pointer="/updated_at",
            scope=ErrorScope.TICKET,
            ticket_id=expected_source_ticket_id,
        )
    except ContractValidationError as exc:
        issues.extend(exc.issues)
        updated_at = created_at
    if updated_at < created_at:
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_TICKET_TIME_RANGE",
                "updated_at cannot be earlier than created_at.",
                pointer="/updated_at",
                ticket_id=expected_source_ticket_id,
            )
        )

    normalized = dict(payload)
    users = payload.get("users")
    if not isinstance(users, list) or not users:
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "USERS_REQUIRED",
                "users must be a non-empty array.",
                pointer="/users",
                ticket_id=expected_source_ticket_id,
            )
        )
        users = []
    normalized_users = [
        user
        for index, raw_user in enumerate(users)
        if (
            user := _validate_user(
                raw_user,
                pointer=f"/users/{index}",
                ticket_id=expected_source_ticket_id,
                issues=issues,
            )
        )
        is not None
    ]
    user_ids = [
        user["source_user_id"]
        for user in normalized_users
        if isinstance(user.get("source_user_id"), str)
        and user["source_user_id"].strip()
    ]
    for duplicate in sorted(
        source_user_id
        for source_user_id, count in Counter(user_ids).items()
        if count > 1
    ):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "DUPLICATE_SOURCE_USER_ID",
                "Every source_user_id must be unique within a ticket.",
                pointer="/users",
                ticket_id=expected_source_ticket_id,
            )
        )
    users_by_id = {
        user["source_user_id"]: user
        for user in normalized_users
        if isinstance(user.get("source_user_id"), str)
    }
    normalized["users"] = normalized_users

    assignee_source_user_id = payload.get(
        "assignee_source_user_id", default_assignee_source_user_id
    )
    assignee = _resolve_user_reference(
        assignee_source_user_id,
        pointer="/assignee_source_user_id",
        ticket_id=expected_source_ticket_id,
        users_by_id=users_by_id,
        issues=issues,
        required_code="ASSIGNEE_REQUIRED",
        required_message=(
            "Ticket assignee_source_user_id or manifest default is required."
        ),
    )
    if isinstance(assignee_source_user_id, str):
        normalized["assignee_source_user_id"] = assignee_source_user_id.strip()
    if assignee is not None:
        assignee_role = assignee.get("role")
        if not isinstance(assignee_role, str) or assignee_role not in {
            "agent",
            "admin",
        }:
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_ASSIGNEE_ROLE",
                    "Assignee role must be agent or admin.",
                    pointer="/assignee_source_user_id",
                    ticket_id=expected_source_ticket_id,
                )
            )
        if not assignee.get("email"):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "ASSIGNEE_EMAIL_REQUIRED",
                    "The referenced assignee user must include an email.",
                    pointer="/assignee_source_user_id",
                    ticket_id=expected_source_ticket_id,
                )
            )

    requester_source_user_id = payload.get("requester_source_user_id")
    _resolve_user_reference(
        requester_source_user_id,
        pointer="/requester_source_user_id",
        ticket_id=expected_source_ticket_id,
        users_by_id=users_by_id,
        issues=issues,
    )
    if isinstance(requester_source_user_id, str):
        normalized["requester_source_user_id"] = requester_source_user_id.strip()

    custom_fields = payload.get("custom_fields", {})
    if not isinstance(custom_fields, dict) or any(
        not isinstance(key, str) or not key.strip() for key in custom_fields
    ):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_CUSTOM_FIELDS",
                "custom_fields must be an object with non-empty string keys.",
                pointer="/custom_fields",
                ticket_id=expected_source_ticket_id,
            )
        )
    elif any(
        value is not None
        and (
            isinstance(value, (dict, list))
            or not isinstance(value, (str, int, float, bool))
        )
        for value in custom_fields.values()
    ):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_CUSTOM_FIELD_VALUE",
                "Custom-field values must be strings, numbers, booleans, or null.",
                pointer="/custom_fields",
                ticket_id=expected_source_ticket_id,
            )
        )

    tags = payload.get("tags", [])
    if not isinstance(tags, list) or any(
        not isinstance(tag, str) or not tag.strip() for tag in tags
    ):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_TAGS",
                "tags must contain non-empty strings.",
                pointer="/tags",
                ticket_id=expected_source_ticket_id,
            )
        )
    elif len(set(tags)) != len(tags):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "DUPLICATE_TAG",
                "tags must not contain duplicates.",
                pointer="/tags",
                ticket_id=expected_source_ticket_id,
            )
        )

    normalized_comments: list[dict[str, Any]] = []
    comments = payload.get("comments", [])
    if not isinstance(comments, list):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_COMMENTS",
                "comments must be an array.",
                pointer="/comments",
                ticket_id=expected_source_ticket_id,
            )
        )
        comments = []
    for index, comment in enumerate(comments):
        pointer = f"/comments/{index}"
        if not isinstance(comment, dict):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_COMMENT",
                    "Comment must be an object.",
                    pointer=pointer,
                    ticket_id=expected_source_ticket_id,
                )
            )
            continue
        _validate_exact_fields(
            comment,
            _COMMENT_FIELDS,
            pointer,
            issues,
            scope=ErrorScope.TICKET,
            ticket_id=expected_source_ticket_id,
        )
        item = dict(comment)
        try:
            item["source_comment_id"] = _require_string(
                comment.get("source_comment_id"), name="source_comment_id"
            )
            parse_rfc3339(
                comment.get("created_at"),
                pointer=f"{pointer}/created_at",
                scope=ErrorScope.TICKET,
                ticket_id=expected_source_ticket_id,
            )
        except (ValueError, ContractValidationError) as exc:
            issues.extend(
                exc.issues
                if isinstance(exc, ContractValidationError)
                else [
                    _issue(
                        ErrorScope.TICKET,
                        "INVALID_COMMENT",
                        str(exc),
                        pointer=pointer,
                        ticket_id=expected_source_ticket_id,
                    )
                ]
            )
        comment_type = comment.get("type")
        if not isinstance(comment_type, str) or comment_type not in _COMMENT_TYPES:
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_COMMENT_TYPE",
                    f"type must be one of: {', '.join(sorted(_COMMENT_TYPES))}.",
                    pointer=f"{pointer}/type",
                    ticket_id=expected_source_ticket_id,
                )
            )
        comment_source = comment.get("source")
        if comment_source is not None and (
            not isinstance(comment_source, str)
            or not comment_source.strip()
            or len(comment_source.strip()) > 1024
        ):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_COMMENT_SOURCE",
                    "source must be a non-empty string of at most 1024 characters.",
                    pointer=f"{pointer}/source",
                    ticket_id=expected_source_ticket_id,
                )
            )
        author_source_user_id = comment.get("author_source_user_id")
        _resolve_user_reference(
            author_source_user_id,
            pointer=f"{pointer}/author_source_user_id",
            ticket_id=expected_source_ticket_id,
            users_by_id=users_by_id,
            issues=issues,
            required_code="COMMENT_AUTHOR_REQUIRED",
            required_message="A comment author_source_user_id is required.",
        )
        if isinstance(author_source_user_id, str):
            item["author_source_user_id"] = author_source_user_id.strip()
        body = comment.get("body")
        if body is not None:
            if (
                not isinstance(body, dict)
                or set(body) - {"format", "content"}
                or not isinstance(body.get("format"), str)
                or body.get("format") not in _BODY_FORMATS
                or not isinstance(body.get("content"), str)
            ):
                issues.append(
                    _issue(
                        ErrorScope.TICKET,
                        "INVALID_COMMENT_BODY",
                        "body requires format and string content.",
                        pointer=f"{pointer}/body",
                        ticket_id=expected_source_ticket_id,
                    )
                )
        attachments = comment.get("attachments", [])
        if not isinstance(attachments, list):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_ATTACHMENTS",
                    "attachments must be an array.",
                    pointer=f"{pointer}/attachments",
                    ticket_id=expected_source_ticket_id,
                )
            )
            attachments = []
        item["attachments"] = [
            validated
            for attachment_index, attachment in enumerate(attachments)
            if (
                validated := _validate_asset_reference(
                    attachment,
                    pointer=f"{pointer}/attachments/{attachment_index}",
                    ticket_id=expected_source_ticket_id,
                    issues=issues,
                    media=False,
                )
            )
            is not None
        ]
        if body is None and not item["attachments"]:
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "EMPTY_COMMENT",
                    "A comment requires a body or at least one attachment.",
                    pointer=pointer,
                    ticket_id=expected_source_ticket_id,
                )
            )
        normalized_comments.append(item)
    normalized["comments"] = normalized_comments

    normalized_calls: list[dict[str, Any]] = []
    calls = payload.get("calls", [])
    if not isinstance(calls, list):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_CALLS",
                "calls must be an array.",
                pointer="/calls",
                ticket_id=expected_source_ticket_id,
            )
        )
        calls = []
    for index, call in enumerate(calls):
        pointer = f"/calls/{index}"
        if not isinstance(call, dict):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_CALL",
                    "Call must be an object.",
                    pointer=pointer,
                    ticket_id=expected_source_ticket_id,
                )
            )
            continue
        _validate_exact_fields(
            call,
            _CALL_FIELDS,
            pointer,
            issues,
            scope=ErrorScope.TICKET,
            ticket_id=expected_source_ticket_id,
        )
        item = dict(call)
        try:
            item["source_call_id"] = _require_string(
                call.get("source_call_id"), name="source_call_id"
            )
            parse_rfc3339(
                call.get("created_at"),
                pointer=f"{pointer}/created_at",
                scope=ErrorScope.TICKET,
                ticket_id=expected_source_ticket_id,
            )
        except (ValueError, ContractValidationError) as exc:
            issues.extend(
                exc.issues
                if isinstance(exc, ContractValidationError)
                else [
                    _issue(
                        ErrorScope.TICKET,
                        "INVALID_CALL",
                        str(exc),
                        pointer=pointer,
                        ticket_id=expected_source_ticket_id,
                    )
                ]
            )
        direction = call.get("direction")
        if direction is not None and (
            not isinstance(direction, str) or direction not in _DIRECTIONS
        ):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_CALL_DIRECTION",
                    f"direction must be one of: {', '.join(sorted(_DIRECTIONS))}.",
                    pointer=f"{pointer}/direction",
                    ticket_id=expected_source_ticket_id,
                )
            )
        call_source = call.get("source")
        if call_source is not None and (
            not isinstance(call_source, str)
            or not call_source.strip()
            or len(call_source.strip()) > 1024
        ):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_CALL_SOURCE",
                    "source must be a non-empty string of at most 1024 characters.",
                    pointer=f"{pointer}/source",
                    ticket_id=expected_source_ticket_id,
                )
            )
        fields = call.get("fields", {})
        if not isinstance(fields, dict):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_CALL_FIELDS",
                    "fields must be an object.",
                    pointer=f"{pointer}/fields",
                    ticket_id=expected_source_ticket_id,
                )
            )
        if call.get("media") is None:
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "CALL_MEDIA_REQUIRED",
                    "Every call requires packaged media in ticket/1.0.",
                    pointer=f"{pointer}/media",
                    ticket_id=expected_source_ticket_id,
                )
            )
        else:
            item["media"] = _validate_asset_reference(
                call["media"],
                pointer=f"{pointer}/media",
                ticket_id=expected_source_ticket_id,
                issues=issues,
                media=True,
            )
        normalized_calls.append(item)
    normalized["calls"] = normalized_calls

    normalized["events"] = _validate_events(
        payload.get("events", []),
        pointer="/events",
        ticket_id=expected_source_ticket_id,
        users_by_id=users_by_id,
        issues=issues,
        allowed_fields=_EVENT_FIELDS,
        event_types=_GENERAL_EVENT_TYPES,
    )
    normalized["field_events"] = _validate_events(
        payload.get("field_events", []),
        pointer="/field_events",
        ticket_id=expected_source_ticket_id,
        users_by_id=users_by_id,
        issues=issues,
        allowed_fields=_FIELD_EVENT_FIELDS,
        event_types=_FIELD_EVENT_TYPES,
        require_field_name=True,
    )
    normalized["tag_events"] = _validate_events(
        payload.get("tag_events", []),
        pointer="/tag_events",
        ticket_id=expected_source_ticket_id,
        users_by_id=users_by_id,
        issues=issues,
        allowed_fields=_TAG_EVENT_FIELDS,
        event_types=_TAG_EVENT_TYPES,
        tag_values=True,
    )

    for collection_name, id_name in (
        ("comments", "source_comment_id"),
        ("calls", "source_call_id"),
        ("events", "source_event_id"),
        ("field_events", "source_event_id"),
        ("tag_events", "source_event_id"),
    ):
        values = [
            item.get(id_name)
            for item in normalized.get(collection_name, [])
            if isinstance(item.get(id_name), str)
        ]
        duplicates = sorted(
            value for value, count in Counter(values).items() if count > 1
        )
        for duplicate in duplicates:
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "DUPLICATE_CHILD_ID",
                    f"Duplicate {id_name}: {duplicate}.",
                    pointer=f"/{collection_name}",
                    ticket_id=expected_source_ticket_id,
                )
            )

    if issues:
        raise ContractValidationError(issues)
    assignee = users_by_id[normalized["assignee_source_user_id"]]
    return ValidatedTicket(
        data=normalized,
        source_ticket_id=expected_source_ticket_id,
        created_at=created_at,
        updated_at=updated_at,
        assignee_email=assignee["email"],
    )


def _validate_events(
    value: Any,
    *,
    pointer: str,
    ticket_id: str,
    users_by_id: dict[str, dict[str, Any]],
    issues: list[ContractIssue],
    allowed_fields: set[str],
    event_types: set[str] | None = None,
    require_field_name: bool = False,
    tag_values: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        issues.append(
            _issue(
                ErrorScope.TICKET,
                "INVALID_EVENTS",
                "Event collection must be an array.",
                pointer=pointer,
                ticket_id=ticket_id,
            )
        )
        return []
    normalized: list[dict[str, Any]] = []
    for index, event in enumerate(value):
        item_pointer = f"{pointer}/{index}"
        if not isinstance(event, dict):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_EVENT",
                    "Event must be an object.",
                    pointer=item_pointer,
                    ticket_id=ticket_id,
                )
            )
            continue
        _validate_exact_fields(
            event,
            allowed_fields,
            item_pointer,
            issues,
            scope=ErrorScope.TICKET,
            ticket_id=ticket_id,
        )
        item = dict(event)
        try:
            item["source_event_id"] = _require_string(
                event.get("source_event_id"), name="source_event_id"
            )
            parse_rfc3339(
                event.get("created_at"),
                pointer=f"{item_pointer}/created_at",
                scope=ErrorScope.TICKET,
                ticket_id=ticket_id,
            )
        except (ValueError, ContractValidationError) as exc:
            issues.extend(
                exc.issues
                if isinstance(exc, ContractValidationError)
                else [
                    _issue(
                        ErrorScope.TICKET,
                        "INVALID_EVENT",
                        str(exc),
                        pointer=item_pointer,
                        ticket_id=ticket_id,
                    )
                ]
            )
        event_type = event.get("type")
        if (
            not isinstance(event_type, str)
            or not EVENT_TYPE_PATTERN.fullmatch(event_type)
            or (event_types is not None and event_type not in event_types)
        ):
            expected = (
                f" one of: {', '.join(sorted(event_types))}"
                if event_types
                else " a documented lowercase event type"
            )
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_EVENT_TYPE",
                    f"type must be{expected}.",
                    pointer=f"{item_pointer}/type",
                    ticket_id=ticket_id,
                )
            )
        if not isinstance(event.get("fields", {}), dict):
            issues.append(
                _issue(
                    ErrorScope.TICKET,
                    "INVALID_EVENT_FIELDS",
                    "fields must be an object.",
                    pointer=f"{item_pointer}/fields",
                    ticket_id=ticket_id,
                )
            )
        actor_source_user_id = event.get("actor_source_user_id")
        _resolve_user_reference(
            actor_source_user_id,
            pointer=f"{item_pointer}/actor_source_user_id",
            ticket_id=ticket_id,
            users_by_id=users_by_id,
            issues=issues,
        )
        if isinstance(actor_source_user_id, str):
            item["actor_source_user_id"] = actor_source_user_id.strip()
        if require_field_name:
            field_name = event.get("field_name")
            if (
                not isinstance(field_name, str)
                or not field_name.strip()
                or len(field_name.strip()) > 512
            ):
                issues.append(
                    _issue(
                        ErrorScope.TICKET,
                        "FIELD_NAME_REQUIRED",
                        "field_name must be a non-empty string of at most 512 characters.",
                        pointer=f"{item_pointer}/field_name",
                        ticket_id=ticket_id,
                    )
                )
            for key in ("value", "previous_value"):
                field_value = event.get(key)
                if isinstance(field_value, (dict, list)):
                    issues.append(
                        _issue(
                            ErrorScope.TICKET,
                            "INVALID_FIELD_EVENT_VALUE",
                            f"{key} must be a string, number, boolean, or null.",
                            pointer=f"{item_pointer}/{key}",
                            ticket_id=ticket_id,
                        )
                    )
        if tag_values:
            for key in ("value", "previous_value"):
                tag_list = event.get(key, [])
                if not isinstance(tag_list, list) or any(
                    not isinstance(tag, str) or not tag.strip() for tag in tag_list
                ):
                    issues.append(
                        _issue(
                            ErrorScope.TICKET,
                            "INVALID_TAG_EVENT_VALUE",
                            f"{key} must be an array of tag strings.",
                            pointer=f"{item_pointer}/{key}",
                            ticket_id=ticket_id,
                        )
                    )
        normalized.append(item)
    return normalized


def manifest_asset_matches(
    reference: dict[str, Any], manifest_asset: ManifestAsset | None
) -> bool:
    return bool(
        manifest_asset
        and reference.get("bytes") == manifest_asset.bytes
        and reference.get("sha256", "").lower() == manifest_asset.sha256
        and reference.get("content_type", "").lower() == manifest_asset.content_type
    )


def is_audio_content_type(content_type: str) -> bool:
    return content_type.lower() in _AUDIO_CONTENT_TYPES
