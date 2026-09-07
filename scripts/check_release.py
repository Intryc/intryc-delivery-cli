"""Reject accidental branch releases and tags that do not match package metadata."""

import os
import tomllib
from pathlib import Path


def check_release(ref: str, version: str) -> None:
    if ref != f"refs/tags/v{version}":
        raise ValueError(
            f"Select the version tag v{version}; branch or mismatched-tag releases fail."
        )


if __name__ == "__main__":
    project = tomllib.loads(Path("pyproject.toml").read_text())
    check_release(os.environ.get("GITHUB_REF", ""), project["project"]["version"])
