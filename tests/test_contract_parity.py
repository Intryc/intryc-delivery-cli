import ast
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from intryc_delivery.contract import semantic
from intryc_delivery.contract.json_payload import parse_json_object as load_json_object

PACKAGE = Path(__file__).parents[1]
PROVENANCE = json.loads((PACKAGE / "contract-provenance.json").read_bytes())


def test_bundled_snapshot_hashes():
    for entry in PROVENANCE["files"]:
        assert (
            hashlib.sha256((PACKAGE / entry["local"]).read_bytes()).hexdigest() == entry["sha256"]
        )


def test_reviewed_source_parity(root):
    source_root = os.environ.get("INTRYC_BIFROST_CONTRACT_ROOT")
    if not source_root:
        pytest.skip("Set INTRYC_BIFROST_CONTRACT_ROOT for cross-implementation checks")
    source_root = Path(source_root)
    for entry in PROVENANCE["files"]:
        assert (source_root / entry["source"]).read_bytes() == (
            PACKAGE / entry["local"]
        ).read_bytes()
    bundled = ast.parse((PACKAGE / "src/intryc_delivery/contract/media.py").read_text())

    def named(tree, name):
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.dump(node)
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets
            ):
                return ast.dump(node)
        raise AssertionError(name)

    for path, names in PROVENANCE["extracted_functions"].items():
        original = ast.parse((source_root / path).read_text())
        for name in names:
            assert named(original, name) == named(bundled, name)
    spec = importlib.util.spec_from_file_location(
        "reviewed_contract", source_root / "bifrost/etl/extractors/intryc/contracts.py"
    )
    original = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = original
    spec.loader.exec_module(original)
    for path in (root / "ticket_details").glob("*.json"):
        payload = load_json_object(path.read_bytes())
        for change in (
            {},
            {"updated_at": "2000-01-01T00:00:00Z"},
            {"schema_version": "ticket/99"},
            {"unknown": "example"},
        ):
            outcomes = []
            for module in (semantic, original):
                try:
                    result = module.parse_ticket(
                        {**payload, **change}, expected_source_ticket_id=payload["source_ticket_id"]
                    )
                    outcomes.append(result.model_dump(mode="json"))
                except module.ContractValidationError as exc:
                    outcomes.append(sorted(i.code for i in exc.issues))
            assert outcomes[0] == outcomes[1]
