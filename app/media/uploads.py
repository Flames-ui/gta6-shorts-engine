"""Upload validation: extension allow-list, streamed size enforcement, content sniffing.

The extension is only a hint. The file must start with a container signature
that matches its family, and ffprobe must recognise a video stream (see probe.py).
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import BinaryIO, Optional

from ..errors import MediaValidationError, TooLarge

ALLOWED_EXTENSIONS = {
    ".mp4": "isobmff", ".m4v": "isobmff", ".mov": "isobmff",
    ".mkv": "ebml", ".webm": "ebml",
}
ALLOWED_MIME = {
    "video/mp4", "video/quicktime", "video/x-matroska", "video/webm", "video/x-m4v",
    "application/octet-stream",  # many clients send this for video; content is still checked
}
EBML_MAGIC = b"\x1a\x45\xdf\xa3"
CHUNK = 1024 * 1024


@dataclass
class StreamedFile:
    path: str
    size: int
    sha256: str
    extension: str


def safe_display_name(name: Optional[str]) -> str:
    base = os.path.basename((name or "upload").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()[:200]
    return base or "upload"


def extension_of(name: str) -> str:
    return os.path.splitext(name.lower())[1]


def check_extension(name: str) -> str:
    ext = extension_of(name)
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise MediaValidationError(
            f"Unsupported file type '{ext or 'none'}'. Upload one of: {allowed}.",
            code="unsupported_format",
        )
    return ext


def signature_matches(extension: str, head: bytes) -> bool:
    family = ALLOWED_EXTENSIONS.get(extension)
    if family == "isobmff":
        return len(head) >= 12 and head[4:8] == b"ftyp"
    if family == "ebml":
        return head[:4] == EBML_MAGIC
    return False


def stream_to_file(stream: BinaryIO, dest_path: str, max_bytes: int, extension: str) -> StreamedFile:
    """Copy an upload to disk in chunks, enforcing the byte limit and hashing as we go."""
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise TooLarge(
                        f"File is larger than the {max_bytes // (1024 * 1024)} MB limit.",
                        code="upload_too_large",
                    )
                if len(head) < 64:
                    head += chunk[: 64 - len(head)]
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        if os.path.exists(dest_path):
            os.remove(dest_path)  # incomplete uploads never persist
        raise
    if size == 0:
        os.remove(dest_path)
        raise MediaValidationError("The uploaded file is empty.", code="empty_file")
    if not signature_matches(extension, head):
        os.remove(dest_path)
        raise MediaValidationError(
            "The file content does not match its extension. It may be corrupt or not a video.",
            code="content_type_mismatch",
        )
    return StreamedFile(path=dest_path, size=size, sha256=digest.hexdigest(), extension=extension)
