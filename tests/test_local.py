import hashlib
import json
import os

import pytest
from conftest import save_ticket

from intryc_delivery.contract.json_payload import parse_json_object as load_json_object
from intryc_delivery.errors import DeliveryError
from intryc_delivery.filesystem import delivery_lock, inventory
from intryc_delivery.manifest import generate
from intryc_delivery.validation import validate


def test_golden_full_contract(root):
    delivery = validate(root)
    assert len(delivery.manifest["tickets"]) == 3
    assert len(delivery.objects) == 6
    assert {o.content_type for o in delivery.objects} == {
        "application/json",
        "audio/wav",
        "text/plain",
    }


def test_manifest_determinism_and_dry_run(root):
    (root / "manifest.json").unlink()
    generated = generate(root, delivery_id="new", created_at="2026-09-07T00:00:00Z", dry_run=True)
    assert not (root / "manifest.json").exists()
    generate(root, delivery_id="new", created_at="2026-09-07T00:00:00Z")
    first = (root / "manifest.json").read_bytes()
    generate(root)
    assert (root / "manifest.json").read_bytes() == first
    assert generated.objects[2].path == validate(root).objects[2].path
    assert first.endswith(b"\n")
    assert [t["source_ticket_id"] for t in json.loads(first)["tickets"]] == sorted(
        t["source_ticket_id"] for t in json.loads(first)["tickets"]
    )


def test_manifest_conflict_preserves_file(root):
    before = (root / "manifest.json").read_bytes()
    with pytest.raises(DeliveryError):
        generate(root, delivery_id="conflict")
    assert (root / "manifest.json").read_bytes() == before
    generate(root, delivery_id="conflict", force=True)
    assert validate(root).delivery_id == "conflict"


def test_force_can_replace_malformed_manifest(root):
    (root / "manifest.json").write_text("broken")
    with pytest.raises(DeliveryError):
        generate(root, delivery_id="replacement")
    generate(root, delivery_id="replacement", force=True)
    assert validate(root).delivery_id == "replacement"


def test_shared_users_compared_using_ingestion_normalization(root):
    ticket = json.loads((root / "ticket_details/written.json").read_bytes())
    ticket["users"][0]["email"] = "AGENT@example.com"
    ticket["users"][0]["custom_fields"] = {}
    save_ticket(root, "written.json", ticket)
    validate(root)


def test_attachment_ids_are_scoped_to_each_ticket(root):
    attachment = json.loads((root / "ticket_details/attachment.json").read_bytes())
    written = json.loads((root / "ticket_details/written.json").read_bytes())
    written["comments"][0]["attachments"] = attachment["comments"][0]["attachments"]
    save_ticket(root, "written.json", written)
    validate(root)

    written["comments"][0]["attachments"] *= 2
    save_ticket(root, "written.json", written)
    with pytest.raises(DeliveryError) as exc:
        validate(root)
    assert "DUPLICATE_ATTACHMENT_ID" in {i.code for i in exc.value.issues}


@pytest.mark.parametrize(
    "mutation,code",
    [
        (
            lambda manifest: manifest["tickets"].append(manifest["tickets"][0].copy()),
            "DUPLICATE_SOURCE_TICKET_ID",
        ),
        (lambda manifest: manifest["counts"].update(tickets=999), "COUNT_MISMATCH"),
    ],
)
def test_bad_manifest_inventory(root, mutation, code):
    path = root / "manifest.json"
    payload = json.loads(path.read_bytes())
    mutation(payload)
    if code == "DUPLICATE_SOURCE_TICKET_ID":
        payload["counts"]["tickets"] = len(payload["tickets"])
    path.write_text(json.dumps(payload))
    with pytest.raises(DeliveryError) as exc:
        validate(root)
    assert code in {i.code for i in exc.value.issues}


@pytest.mark.parametrize(
    "content",
    [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'{"x":"\\ud800"}', b"[]", b'{"x":"\\u0000"}'],
)
def test_strict_json(content):
    with pytest.raises(ValueError):
        load_json_object(content)


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda p: p.update(unknown="private-value"), "UNKNOWN_FIELD"),
        (lambda p: p.update(updated_at="2020-01-01T00:00:00Z"), "INVALID_TICKET_TIME_RANGE"),
        (lambda p: p.update(assignee_source_user_id="missing"), "UNKNOWN_USER_REFERENCE"),
    ],
)
def test_ticket_semantics(root, change, code):
    payload = json.loads((root / "ticket_details/written.json").read_bytes())
    change(payload)
    save_ticket(root, "written.json", payload)
    with pytest.raises(DeliveryError) as exc:
        validate(root)
    assert code in {i.code for i in exc.value.issues}
    assert "private-value" not in str(exc.value.issues)


def test_aggregates_missing_and_corrupt_objects(root):
    (root / "assets/attachments/example.txt").write_text("corrupted")
    (root / "ticket_details/written.json").unlink()
    with pytest.raises(DeliveryError) as exc:
        validate(root)
    assert {"MISSING_OBJECT", "SIZE_MISMATCH", "CHECKSUM_MISMATCH"} <= {
        i.code for i in exc.value.issues
    }


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "hidden", "case", "fifo"])
def test_unsafe_local_inventory(root, kind):
    target = root / "assets" / "bad"
    if kind == "symlink":
        target.symlink_to(root / "manifest.json")
    elif kind == "hardlink":
        os.link(root / "manifest.json", target)
    elif kind == "hidden":
        (root / "assets/.temp").write_text("x")
    elif kind == "case":
        (root / "assets/CASE").write_text("x")
        (root / "assets/case").write_text("x")
        if (
            len(list((root / "assets").glob("*ase"))) + len(list((root / "assets").glob("*ASE")))
            < 2
        ):
            pytest.skip("Filesystem itself prevents case-colliding entries")
    else:
        os.mkfifo(target)
    with pytest.raises(DeliveryError) as exc:
        inventory(root)
    assert all(i.code == "UNSAFE_PATH" for i in exc.value.issues)


def test_manifest_rejects_traversal(root):
    path = root / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["tickets"][0]["path"] = "ticket_details/../manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(DeliveryError):
        validate(root)


def test_checksum_valid_but_wrong_media(root):
    path = root / "assets/attachments/example.txt"
    path.write_bytes(b"%PDF-example")
    ticket = json.loads((root / "ticket_details/attachment.json").read_bytes())
    ref = ticket["comments"][0]["attachments"][0]
    ref.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    save_ticket(root, "attachment.json", ticket)
    (root / "manifest.json").unlink()
    with pytest.raises(DeliveryError) as exc:
        generate(root, delivery_id="bad-media")
    assert "MEDIA_TYPE_MISMATCH" in {i.code for i in exc.value.issues}
    assert not (root / "manifest.json").exists()


def test_lock_excludes_other_local_writer():
    with delivery_lock("test-delivery-lock"):
        with pytest.raises(DeliveryError) as exc:
            with delivery_lock("test-delivery-lock"):
                pass
        assert exc.value.issues[0].code == "DELIVERY_LOCKED"
