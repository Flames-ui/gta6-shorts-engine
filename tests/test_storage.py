import time

import pytest

from app.storage import LocalStorage, validate_key


@pytest.mark.parametrize("key", ["../etc/passwd", "a/../../b", "/absolute/path", "a\\b", "", "x" * 400, "a\x00b"])
def test_unsafe_storage_keys_are_rejected(key):
    # Keys are generated internally; an unsafe one is a programming error, never user input.
    with pytest.raises(ValueError):
        validate_key(key)


def test_put_download_and_signed_links(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    store = LocalStorage(str(tmp_path / "root"), signing_secret="s" * 32)
    size = store.put_file("jobs/j1/clips/1/clip.mp4", str(src), "video/mp4")
    assert size == 5
    url, expires = store.signed_url("jobs/j1/clips/1/clip.mp4", ttl_seconds=60, filename="clip.mp4")
    token, sig = url.split("/files/")[1].split("?sig=")
    assert store.verify_token(token, sig) == "jobs/j1/clips/1/clip.mp4"
    assert expires > time.time()


def test_tampered_or_expired_links_fail(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"data")
    store = LocalStorage(str(tmp_path / "root"), signing_secret="s" * 32)
    store.put_file("a/b.mp4", str(src), "video/mp4")
    url, _ = store.signed_url("a/b.mp4", ttl_seconds=60, filename="b.mp4")
    token, sig = url.split("/files/")[1].split("?sig=")
    with pytest.raises(ValueError):
        store.verify_token(token + "x", sig)
    with pytest.raises(ValueError, match="bad signature"):
        store.verify_token(token, "0" * len(sig))
    expired_url, _ = store.signed_url("a/b.mp4", ttl_seconds=-5, filename="b.mp4")
    t2, s2 = expired_url.split("/files/")[1].split("?sig=")
    with pytest.raises(ValueError, match="expired"):
        store.verify_token(t2, s2)


def test_other_storage_secret_cannot_verify(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"data")
    a = LocalStorage(str(tmp_path / "root"), signing_secret="a" * 32)
    b = LocalStorage(str(tmp_path / "root"), signing_secret="b" * 32)
    a.put_file("k/v.mp4", str(src), "video/mp4")
    url, _ = a.signed_url("k/v.mp4", ttl_seconds=60, filename="v.mp4")
    token, sig = url.split("/files/")[1].split("?sig=")
    with pytest.raises(ValueError):
        b.verify_token(token, sig)
