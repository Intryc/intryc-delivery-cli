import importlib.metadata
import importlib.util
from pathlib import Path

import pytest

from intryc_delivery import __version__

SPEC = importlib.util.spec_from_file_location(
    "check_release", Path(__file__).parents[1] / "scripts/check_release.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("ref", ["", "refs/heads/main", "refs/tags/v0.2.0", "refs/tags/0.1.0"])
def test_release_rejects_branches_and_mismatched_tags(ref):
    with pytest.raises(ValueError):
        MODULE.check_release(ref, "0.1.0")


def test_matching_release_tag_and_installed_version():
    MODULE.check_release("refs/tags/v0.1.0", "0.1.0")
    assert __version__ == importlib.metadata.version("intryc-delivery")
