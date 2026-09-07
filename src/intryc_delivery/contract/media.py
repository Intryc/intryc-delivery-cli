from pathlib import Path


def detect_content_type(header: bytes) -> str | None:
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "audio/wav"
    if header.startswith(b"fLaC"):
        return "audio/flac"
    if header.startswith(b"OggS"):
        return "audio/ogg"
    if header.startswith(b"ID3") or (
        len(header) > 1 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/mp4"
    if header.startswith(b"PK\x03\x04"):
        return "application/zip"
    if header and b"\x00" not in header:
        return "text/plain"
    return None


def content_type_matches(expected: str, detected: str | None) -> bool:
    if detected is None:
        return False
    expected = expected.lower()
    if expected in {"audio/mp4", "video/mp4"} and detected == "video/mp4":
        return True
    if expected in {"audio/wav", "audio/x-wav"} and detected == "audio/wav":
        return True
    if expected in {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        return detected == "application/zip"
    if expected in {"text/csv", "text/plain"}:
        return detected == "text/plain"
    return expected == detected


CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": {".pdf"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "audio/flac": {".flac"},
    "audio/mp4": {".m4a", ".mp4"},
    "audio/mpeg": {".mp3", ".mpeg"},
    "audio/ogg": {".oga", ".ogg", ".opus"},
    "audio/wav": {".wav"},
    "audio/x-wav": {".wav"},
    "image/gif": {".gif"},
    "image/jpeg": {".jpeg", ".jpg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
    "text/csv": {".csv"},
    "text/plain": {".log", ".txt"},
    "video/mp4": {".mp4"},
}


def _filename_matches_content_type(filename: str, content_type: str) -> bool:
    return Path(filename).suffix.lower() in CONTENT_TYPE_EXTENSIONS.get(
        content_type.lower(),
        set(),
    )
