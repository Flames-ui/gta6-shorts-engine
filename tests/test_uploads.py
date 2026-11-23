import io
import os

import pytest

from app.errors import MediaValidationError, StudioError, TooLarge
from app.media.uploads import check_extension, safe_display_name, signature_matches, stream_to_file


def test_path_components_are_stripped_from_names():
    assert safe_display_name("../../etc/passwd.mp4") == "passwd.mp4"
    assert safe_display_name("C:\\Users\\me\\clip.mov") == "clip.mov"
    assert safe_display_name("weird\x00name\n.webm") == "weirdname.webm"
    assert safe_display_name(None) == "upload"


@pytest.mark.parametrize("name,ext", [("a.mp4", ".mp4"), ("A.MOV", ".mov"), ("x.mkv", ".mkv"), ("y.webm", ".webm"),
                                      ("z.m4v", ".m4v")])
def test_allowed_extensions(name, ext):
    assert check_extension(name) == ext


@pytest.mark.parametrize("name", ["script.exe", "page.html", "notes.txt", "noextension", "clip.mp4.exe", "x.svg"])
def test_disallowed_extensions_are_rejected(name):
    with pytest.raises(StudioError) as exc:
        check_extension(name)
    assert exc.value.code == "unsupported_format"


def test_signature_checks_use_content_not_extension():
    mp4_head = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16
    assert signature_matches(".mp4", mp4_head)
    assert signature_matches(".mov", mp4_head)
    assert not signature_matches(".mp4", b"MZ\x90\x00 this is a windows executable")
    assert signature_matches(".webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 20)
    assert not signature_matches(".webm", mp4_head)


def test_stream_to_file_computes_sha_and_size(tmp_path):
    payload = b"\x00\x00\x00\x18ftypisom" + os.urandom(4096)
    dest = tmp_path / "out.mp4"
    result = stream_to_file(io.BytesIO(payload), str(dest), max_bytes=1_000_000, extension=".mp4")
    import hashlib

    assert result.size == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert dest.read_bytes() == payload


def test_stream_to_file_enforces_size_limit_and_cleans_up(tmp_path):
    payload = b"\x00\x00\x00\x18ftypisom" + b"x" * 5000
    dest = tmp_path / "big.mp4"
    with pytest.raises(TooLarge) as exc:
        stream_to_file(io.BytesIO(payload), str(dest), max_bytes=1000, extension=".mp4")
    assert exc.value.code == "upload_too_large"
    assert not dest.exists()


def test_stream_to_file_rejects_empty_and_mismatched_content(tmp_path):
    with pytest.raises(MediaValidationError) as empty:
        stream_to_file(io.BytesIO(b""), str(tmp_path / "e.mp4"), max_bytes=1000, extension=".mp4")
    assert empty.value.code == "empty_file"
    with pytest.raises(MediaValidationError) as fake:
        stream_to_file(io.BytesIO(b"hello world, not a video at all"), str(tmp_path / "f.mp4"),
                       max_bytes=1000, extension=".mp4")
    assert fake.value.code == "content_type_mismatch"
    assert not (tmp_path / "f.mp4").exists()
