import hashlib
import json
from pathlib import Path

PACKAGE = Path(__file__).parents[1]


def test_bundled_contract_hashes():
    manifest = json.loads((PACKAGE / "contract-version.json").read_bytes())
    for entry in manifest["files"]:
        assert hashlib.sha256((PACKAGE / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
