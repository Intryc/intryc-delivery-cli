import json
from datetime import UTC, datetime
from pathlib import Path

from intryc_delivery.contract.semantic import ContractValidationError, parse_manifest, parse_ticket
from intryc_delivery.errors import DeliveryError, Issue, fail
from intryc_delivery.filesystem import atomic_write, fingerprint, inventory
from intryc_delivery.validation import LIMITS, contract_issues, read_json, schema_issues, validate


def canonical_json(payload: dict) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def generate(
    root: Path,
    *,
    delivery_id: str | None = None,
    created_at: str | None = None,
    default_assignee_source_user_id: str | None = None,
    force: bool = False,
    dry_run: bool = False,
):
    paths = inventory(root)
    existing = None
    if "manifest.json" in paths:
        try:
            existing = read_json(paths["manifest.json"], LIMITS.manifest_bytes)
            errors = schema_issues(existing, "ticket-delivery-1.0.schema.json", "manifest.json")
            if errors:
                raise DeliveryError(errors)
            parsed = parse_manifest(existing, limits=LIMITS)
            if parsed.issues or parsed.rejected_tickets:
                fail("INVALID_MANIFEST", "Existing manifest has invalid inventory entries.")
        except (DeliveryError, ContractValidationError) as exc:
            if not force:
                if isinstance(exc, ContractValidationError):
                    raise DeliveryError(contract_issues(exc.issues, "manifest.json")) from None
                raise
            existing = None
    delivery_id = delivery_id or (existing or {}).get("delivery_id")
    if not delivery_id:
        fail("MISSING_DELIVERY_ID", "Supply --delivery-id for a new delivery.")
    default = default_assignee_source_user_id or (existing or {}).get("defaults", {}).get(
        "assignee_source_user_id"
    )
    tickets, assets, issues = [], {}, []
    for path, local in paths.items():
        if not path.startswith("ticket_details/"):
            continue
        try:
            payload = read_json(local, LIMITS.ticket_bytes)
            issues.extend(schema_issues(payload, "ticket-1.0.schema.json", path))
            ticket = parse_ticket(
                payload,
                expected_source_ticket_id=payload.get("source_ticket_id", ""),
                default_assignee_source_user_id=default,
            )
            size, sha, _ = fingerprint(local, LIMITS.ticket_bytes)
            tickets.append(
                {
                    "source_ticket_id": ticket.source_ticket_id,
                    "path": path,
                    "bytes": size,
                    "sha256": sha,
                }
            )
            for ref in ticket.asset_references():
                entry = {key: ref[key] for key in ("path", "bytes", "sha256", "content_type")}
                if entry["path"] in assets and assets[entry["path"]] != entry:
                    issues.append(
                        Issue(
                            code="ASSET_MANIFEST_MISMATCH",
                            message="References to one asset must be identical.",
                            path=path,
                        )
                    )
                assets[entry["path"]] = entry
        except ContractValidationError as exc:
            issues.extend(contract_issues(exc.issues, path))
        except DeliveryError as exc:
            issues.extend(i.model_copy(update={"path": path}) for i in exc.issues)
    if issues:
        raise DeliveryError(issues)
    payload = {
        "schema_version": "ticket-delivery/1.0",
        "mode": "create_only",
        "delivery_id": delivery_id,
        "created_at": created_at
        or (existing or {}).get("created_at")
        or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tickets": sorted(tickets, key=lambda t: (t["source_ticket_id"], t["path"])),
        "assets": sorted(assets.values(), key=lambda a: a["path"]),
        "counts": {"tickets": len(tickets), "assets": len(assets)},
    }
    if default:
        payload["defaults"] = {"assignee_source_user_id": default}
    delivery = validate(root, candidate=payload)
    if existing is not None and canonical_json(existing) != canonical_json(payload) and not force:
        # Ordering of pre-existing manifest arrays is not meaningful.
        comparable = dict(existing)
        comparable["tickets"] = sorted(
            existing.get("tickets", []), key=lambda t: (t["source_ticket_id"], t["path"])
        )
        comparable["assets"] = sorted(existing.get("assets", []), key=lambda a: a["path"])
        if canonical_json(comparable) != canonical_json(payload):
            fail(
                "MANIFEST_CONFLICT",
                "Existing manifest differs. Use --force only for an unpublished delivery.",
            )
        return validate(root)
    if not dry_run:
        if existing is not None and existing == payload:
            return validate(root)
        atomic_write(root / "manifest.json", canonical_json(payload))
        return validate(root)
    return delivery
