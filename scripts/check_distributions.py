"""Ensure release archives contain only the intended public source and package."""

import tarfile
import zipfile
from pathlib import Path, PurePosixPath

SOURCE_ROOTS = {
    "src",
    "tests",
    "scripts",
    "examples",
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "ruff.toml",
    "contract-version.json",
    "uv.lock",
    ".gitignore",
    "PKG-INFO",
}


def check_distributions(directory: Path) -> None:
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    assert len(wheels) == len(sources) == 1, "Expected one wheel and source distribution"
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        assert all(
            name.startswith("intryc_delivery/")
            or (name.startswith("intryc_delivery-") and ".dist-info/" in name)
            for name in names
        ), "Unexpected wheel content"
        assert len([name for name in names if name.endswith(".schema.json")]) == 2
        assert any(name.endswith("/licenses/LICENSE") for name in names)
    with tarfile.open(sources[0]) as source:
        for member in source.getmembers():
            parts = PurePosixPath(member.name).parts
            assert not member.issym() and not member.islnk(), "Unexpected archive link"
            assert len(parts) >= 2 and parts[1] in SOURCE_ROOTS, "Unexpected source content"
            assert not any(
                part in {".venv", ".wheel-venv", ".git", "__pycache__"} for part in parts
            )
            assert not member.name.endswith((".pyc", ".pyo")), "Bytecode in source distribution"
    print(
        "Distribution inventory verified: packaged schemas/license and intended source files only."
    )


if __name__ == "__main__":
    check_distributions(Path("dist"))
