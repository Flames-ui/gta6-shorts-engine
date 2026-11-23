"""Private object storage behind one interface.

* LocalStorage  - a private directory on a persistent disk. Downloads are served
                  through short-lived HMAC-signed links minted by the API.
* SupabaseStorage - a private Supabase Storage bucket. Downloads use provider-
                  signed URLs with the configured TTL.

Keys are validated so that no caller can escape the storage root.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional, Protocol, Tuple

import httpx

from .config import Settings, settings
from .errors import ServiceUnavailable

logger = logging.getLogger("studio.storage")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]{0,300}$")


class StorageError(ServiceUnavailable):
    code = "storage_unavailable"


def validate_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY_RE.match(key) or ".." in key.split("/") or key.endswith("/"):
        raise ValueError(f"Unsafe storage key: {key!r}")
    return key


class Storage(Protocol):
    name: str

    def put_file(self, key: str, local_path: str, content_type: str) -> int: ...
    def download_to(self, key: str, dest_path: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def signed_url(self, key: str, ttl_seconds: int, filename: str) -> Tuple[str, int]: ...


class LocalStorage:
    name = "local"

    def __init__(self, root: str, signing_secret: str):
        self.root = Path(root).resolve()
        self._secret = (signing_secret or "dev-only-signing-secret").encode()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / validate_key(key)).resolve()
        if self.root not in path.parents:
            raise ValueError("Storage key escapes the storage root")
        return path

    def put_file(self, key: str, local_path: str, content_type: str) -> int:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".upload-", suffix=".part")
        os.close(fd)
        try:
            shutil.copyfile(local_path, tmp)
            os.replace(tmp, dest)  # atomic publish: readers never see a partial file
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return dest.stat().st_size

    def local_path(self, key: str) -> str:
        path = self._path(key)
        if not path.is_file():
            raise StorageError("Stored file is missing.", code="storage_object_missing")
        return str(path)

    def download_to(self, key: str, dest_path: str) -> None:
        shutil.copyfile(self.local_path(key), dest_path)

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()

    def sign(self, key: str, expires_at: int) -> str:
        msg = f"{key}|{expires_at}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def signed_url(self, key: str, ttl_seconds: int, filename: str) -> Tuple[str, int]:
        expires_at = int(time.time()) + int(ttl_seconds)
        token_payload = json.dumps({"k": key, "e": expires_at}, separators=(",", ":")).encode()
        token = base64.urlsafe_b64encode(token_payload).decode().rstrip("=")
        sig = self.sign(key, expires_at)
        return f"/api/v1/files/{token}?sig={sig}", expires_at

    def verify_token(self, token: str, sig: str) -> str:
        """Return the storage key for a valid, unexpired token, else raise ValueError."""
        try:
            padded = token + "=" * (-len(token) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
            key, expires_at = payload["k"], int(payload["e"])
        except Exception as exc:  # malformed token
            raise ValueError("malformed token") from exc
        if time.time() > expires_at:
            raise ValueError("expired")
        if not hmac.compare_digest(self.sign(key, expires_at), sig or ""):
            raise ValueError("bad signature")
        validate_key(key)
        return key


class SupabaseStorage:
    name = "supabase"

    def __init__(self, base_url: str, service_key: str, bucket: str, timeout: float = 120.0,
                 transport: Optional[httpx.BaseTransport] = None):
        self.base = base_url.rstrip("/")
        self.bucket = bucket
        self._headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
        self._client = httpx.Client(timeout=timeout, headers=self._headers, transport=transport)

    def _url(self, *parts: str) -> str:
        return f"{self.base}/storage/v1/" + "/".join(parts)

    def put_file(self, key: str, local_path: str, content_type: str) -> int:
        validate_key(key)
        size = os.path.getsize(local_path)
        with open(local_path, "rb") as fh:
            resp = self._client.post(
                self._url("object", self.bucket, key),
                content=fh,
                headers={"Content-Type": content_type, "x-upsert": "true", "Content-Length": str(size)},
            )
        if resp.status_code >= 300:
            logger.error("supabase upload failed status=%s", resp.status_code)
            raise StorageError("Could not store the file. Please retry.")
        return size

    def download_to(self, key: str, dest_path: str) -> None:
        validate_key(key)
        with self._client.stream("GET", self._url("object", "authenticated", self.bucket, key)) as resp:
            if resp.status_code != 200:
                raise StorageError("Stored file could not be read.", code="storage_object_missing")
            with open(dest_path, "wb") as out:
                for chunk in resp.iter_bytes(1024 * 1024):
                    out.write(chunk)

    def delete(self, key: str) -> None:
        validate_key(key)
        self._client.request("DELETE", self._url("object", self.bucket), json={"prefixes": [key]})

    def signed_url(self, key: str, ttl_seconds: int, filename: str) -> Tuple[str, int]:
        validate_key(key)
        resp = self._client.post(self._url("object", "sign", self.bucket, key), json={"expiresIn": int(ttl_seconds)})
        if resp.status_code != 200:
            raise StorageError("Could not create a download link. Please retry.")
        signed = resp.json().get("signedURL", "")
        if not signed:
            raise StorageError("Could not create a download link. Please retry.")
        return f"{self.base}/storage/v1{signed}&download={filename}", int(time.time()) + int(ttl_seconds)


_storage: Optional[Storage] = None


def get_storage(cfg: Settings = settings) -> Storage:
    global _storage
    if _storage is None:
        if cfg.STORAGE_BACKEND == "supabase":
            _storage = SupabaseStorage(cfg.SUPABASE_URL, cfg.SUPABASE_SERVICE_ROLE_KEY, cfg.SUPABASE_STORAGE_BUCKET)
        else:
            secret = cfg.AUTH_JWT_SECRET or ("dev-" + cfg.ENVIRONMENT)
            _storage = LocalStorage(cfg.STORAGE_LOCAL_ROOT, secret)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
