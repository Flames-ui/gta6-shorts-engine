"""Supabase Storage adapter against a mocked HTTP transport (no network)."""
import httpx
import pytest

from app.errors import ServiceUnavailable
from app.storage import StorageError, SupabaseStorage

BASE = "https://project.supabase.co"


def _storage(handler):
    return SupabaseStorage(BASE, "service-key-value", "studio-private", transport=httpx.MockTransport(handler))


def test_upload_uses_the_service_key_and_private_bucket(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x" * 1234)
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["ctype"] = request.headers["content-type"]
        return httpx.Response(200, json={"Key": "ok"})

    size = _storage(handler).put_file("jobs/j/clips/1/clip.mp4", str(f), "video/mp4")
    assert size == 1234
    assert seen["url"] == f"{BASE}/storage/v1/object/studio-private/jobs/j/clips/1/clip.mp4"
    assert seen["auth"] == "Bearer service-key-value"
    assert seen["ctype"] == "video/mp4"


def test_upload_failure_is_a_retryable_storage_error(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    with pytest.raises(StorageError) as exc:
        _storage(lambda r: httpx.Response(500, text="boom")).put_file("a/b.mp4", str(f), "video/mp4")
    assert isinstance(exc.value, ServiceUnavailable)
    assert "boom" not in exc.value.message


def test_download_writes_the_object_to_disk(tmp_path):
    def handler(request):
        assert "/object/authenticated/studio-private/a/b.mp4" in str(request.url)
        return httpx.Response(200, content=b"video-bytes")

    dest = tmp_path / "out.mp4"
    _storage(handler).download_to("a/b.mp4", str(dest))
    assert dest.read_bytes() == b"video-bytes"


def test_missing_object_is_reported_without_leaking_paths(tmp_path):
    with pytest.raises(StorageError) as exc:
        _storage(lambda r: httpx.Response(404)).download_to("a/missing.mp4", str(tmp_path / "x.mp4"))
    assert exc.value.code == "storage_object_missing"


def test_signed_links_are_built_from_the_provider_response():
    def handler(request):
        assert request.url.path.endswith("/object/sign/studio-private/a/b.mp4")
        return httpx.Response(200, json={"signedURL": "/object/sign/studio-private/a/b.mp4?token=abc"})

    url, expires = _storage(handler).signed_url("a/b.mp4", ttl_seconds=300, filename="clip.mp4")
    assert url == f"{BASE}/storage/v1/object/sign/studio-private/a/b.mp4?token=abc&download=clip.mp4"
    assert expires > 0


def test_unsafe_keys_never_reach_the_network():
    calls = []
    storage = _storage(lambda r: calls.append(r) or httpx.Response(200))
    with pytest.raises(ValueError):
        storage.put_file("../escape.mp4", "/dev/null", "video/mp4")
    assert calls == []


def test_migration_check_only_needs_the_database_url(monkeypatch):
    from app.config import Settings, validate_database_settings

    validate_database_settings(Settings(ENVIRONMENT="production", DATABASE_URL="postgresql://u:p@h/db",
                                        AUTH_JWT_SECRET="", REDIS_URL="", GEMINI_API_KEY=""))
    with pytest.raises(Exception):
        validate_database_settings(Settings(ENVIRONMENT="production", DATABASE_URL="sqlite:///x"))
