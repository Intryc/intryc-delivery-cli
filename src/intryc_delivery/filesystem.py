"""Bounded reads and filesystem inventory with no link traversal."""

import hashlib
import os
import stat
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from intryc_delivery.contract.semantic import validate_relative_path
from intryc_delivery.errors import DeliveryError, ExitCode, Issue, fail

CHUNK_BYTES = 1024 * 1024


def inventory(root: Path) -> dict[str, Path]:
    if root.is_symlink() or not root.is_dir():
        fail(
            "INVALID_DIRECTORY",
            "Use a regular delivery directory.",
            exit_code=ExitCode.CONFIGURATION,
        )
    files: dict[str, Path] = {}
    seen: set[str] = set()
    issues = []

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir()):
            path = entry.relative_to(root).as_posix()
            normalized = unicodedata.normalize("NFC", path)
            try:
                validate_relative_path(path)
                if (
                    path != normalized
                    or normalized.casefold() in seen
                    or any(p.startswith(".") for p in path.split("/"))
                ):
                    raise ValueError()
                seen.add(normalized.casefold())
                info = entry.lstat()
                if stat.S_ISLNK(info.st_mode) or (
                    stat.S_ISREG(info.st_mode) and info.st_nlink != 1
                ):
                    raise ValueError()
                if stat.S_ISDIR(info.st_mode):
                    if path.split("/")[0] not in {"ticket_details", "assets"}:
                        raise ValueError()
                    walk(entry)
                elif stat.S_ISREG(info.st_mode):
                    if path != "manifest.json" and not path.startswith(
                        ("ticket_details/", "assets/")
                    ):
                        raise ValueError()
                    if path.startswith("ticket_details/") and not path.endswith(".json"):
                        raise ValueError()
                    files[path] = entry
                    if len(files) > 60_001:
                        fail("TOO_MANY_FILES", "Delivery exceeds the file count limit.")
                else:
                    raise ValueError()
            except ValueError:
                issues.append(
                    Issue(
                        code="UNSAFE_PATH",
                        message="Only normalized, unique regular files and directories are supported.",
                    )
                )

    walk(root)
    if issues:
        raise DeliveryError(issues)
    return files


@contextmanager
def regular_file(path: Path):
    # O_NOFOLLOW protects the final component; ancestor links are checked again
    # on every open, not only during the initial inventory.
    if any(parent.is_symlink() for parent in path.parents):
        fail("UNSAFE_PATH", "File ancestors must not be symlinks.")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            fail("UNSAFE_PATH", "Only regular files without hard links are supported.")
        yield stream
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            fail("FILE_CHANGED", "A local file changed during the operation.")


def fingerprint(path: Path, limit: int) -> tuple[int, str, bytes]:
    digest = hashlib.sha256()
    total = 0
    header = b""
    with regular_file(path) as stream:
        if os.fstat(stream.fileno()).st_size > limit:
            fail("OBJECT_TOO_LARGE", "File exceeds its byte limit.")
        while chunk := stream.read(CHUNK_BYTES):
            total += len(chunk)
            if total > limit:
                fail("OBJECT_TOO_LARGE", "File exceeds its byte limit.")
            header += chunk[: max(0, 512 - len(header))]
            digest.update(chunk)
    return total, digest.hexdigest(), header


def read_bounded(path: Path, limit: int) -> bytes:
    with regular_file(path) as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        fail("OBJECT_TOO_LARGE", "JSON file exceeds its byte limit.")
    return content


def atomic_write(path: Path, content: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".intryc-manifest-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def delivery_lock(identity: str):
    # Kept outside delivery inventory; flock releases on interruption. The
    # inode remains so another process can never bypass an existing lock.
    import fcntl

    directory = Path(tempfile.gettempdir()) / f"intryc-delivery-locks-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        fail("UNSAFE_LOCK", "Local lock directory is unsafe.", exit_code=ExitCode.CONFIGURATION)
    key = hashlib.sha256(identity.encode()).hexdigest()
    fd = os.open(directory / key, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail(
                "DELIVERY_LOCKED",
                "Another local process is using this delivery.",
                exit_code=ExitCode.CONFIGURATION,
            )
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
