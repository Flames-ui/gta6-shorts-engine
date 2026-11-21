import base64
import hashlib
import urllib.parse
from cryptography.fernet import Fernet, InvalidToken
import requests
"""Security utilities for SSRF protection, path traversal defense, and safe execution."""
import ipaddress
import logging
import os
import re
import socket
import subprocess
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger("gta6_shorts.security")

# Allowed schemes
ALLOWED_SCHEMES = frozenset({"https", "http"})

# Disallowed private and special IPv4 / IPv6 networks
PRIVATE_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),          # Loopback
    ipaddress.ip_network("169.254.0.0/16"),       # Link-local / Cloud metadata (169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),       # Private RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),      # Private RFC 1918
    ipaddress.ip_network("198.18.0.0/15"),       # Benchmark
    ipaddress.ip_network("::1/128"),             # IPv6 Loopback
    ipaddress.ip_network("fc00::/7"),            # IPv6 Unique Local
    ipaddress.ip_network("fe80::/10"),           # IPv6 Link-local
)

SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


class SecurityValidationError(ValueError):
    """Raised when an input violates security boundaries."""
    pass


def is_ip_private(ip_str: str) -> bool:
    """Check if an IP address string belongs to private, loopback, or cloud-metadata blocks."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in net for net in PRIVATE_NETWORKS) or ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def validate_safe_url(url: str, require_https: bool = True, allowed_hosts: Optional[Sequence[str]] = None) -> str:
    """
    Validate a remote URL against SSRF, metadata endpoint probing, and scheme hijacking.
    Resolves hostname to ensure it does not map to a loopback or private network.
    """
    if not isinstance(url, str) or not url.strip():
        raise SecurityValidationError("URL must be a non-empty string.")

    cleaned_url = url.strip()

    try:
        parsed = urlsplit(cleaned_url)
    except Exception as exc:
        raise SecurityValidationError(f"Invalid URL structure: {exc}") from exc

    scheme = (parsed.scheme or "").lower()
    if require_https and scheme != "https":
        raise SecurityValidationError("Only secure HTTPS URLs are permitted.")
    if scheme not in ALLOWED_SCHEMES:
        raise SecurityValidationError(f"Disallowed URL scheme: '{scheme}'. Only HTTPS is permitted.")

    hostname = parsed.hostname
    if not hostname:
        raise SecurityValidationError("URL must include a valid hostname.")

    hostname_lower = hostname.lower()

    # Recheck for credentials in authority
    if parsed.username or parsed.password:
        raise SecurityValidationError("URLs containing embedded username or password credentials are rejected.")

    # Recheck ports: only 443 or 80 allowed
    if parsed.port not in (None, 80, 443):
        raise SecurityValidationError(f"Disallowed port: {parsed.port}. Only default HTTPS/HTTP ports are allowed.")

    # Check if hostname matches allowed hosts if specified
    if allowed_hosts:
        matched = any(
            hostname_lower == h or hostname_lower.endswith(f".{h}")
            for h in allowed_hosts
        )
        if not matched:
            raise SecurityValidationError(f"Host '{hostname}' is not in the approved domain allowlist.")

    # Check if hostname directly represents an IP
    try:
        direct_ip = ipaddress.ip_address(hostname_lower)
        if is_ip_private(str(direct_ip)):
            raise SecurityValidationError(f"Access to private/internal IP address '{hostname}' is forbidden.")
    except ValueError:
        # Not an IP literal; perform DNS resolution check to prevent DNS rebinding attacks
        try:
            addr_info = socket.getaddrinfo(hostname_lower, None, proto=socket.IPPROTO_TCP)
            for entry in addr_info:
                sockaddr = entry[4]
                resolved_ip = sockaddr[0]
                if is_ip_private(resolved_ip):
                    raise SecurityValidationError(
                        f"Host '{hostname}' resolves to private/internal IP address '{resolved_ip}', which is forbidden."
                    )
        except socket.gaierror as exc:
            from ..config import settings
            logger.warning("Could not resolve host '%s' during safety check: %s", hostname, exc)
            if settings.ENVIRONMENT.lower() == "production" and settings.FAIL_CLOSED_IN_PRODUCTION:
                raise SecurityValidationError(f"Could not resolve host '{hostname}' in production: {exc}")

    return cleaned_url


def sanitize_filename(filename: str, fallback_prefix: str = "media") -> str:
    """
    Sanitize a user-provided or extracted filename to defend against path traversal.
    Returns a secure basename.
    """
    if not filename:
        return f"{fallback_prefix}_{os.urandom(4).hex()}.mp4"

    # Strip directory components
    base = os.path.basename(filename).strip()

    # Disallow null bytes and traversal sequences
    base = base.replace("\0", "").replace("..", "").replace("/", "").replace("\\", "")

    # Ensure it only has valid alphanumeric, dash, dot, and underscore characters
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", base)

    # Ensure it is not empty or just dots
    if not cleaned or cleaned.strip(".") == "":
        cleaned = f"{fallback_prefix}_{os.urandom(4).hex()}.mp4"

    return cleaned[:100]


def safe_subprocess_run(
    cmd: List[str],
    timeout: int = 300,
    check: bool = True,
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    """
    Execute an external binary (FFmpeg, FFprobe) using explicit argument arrays.
    Never invokes shell=True. Enforces execution timeout.
    """
    if not isinstance(cmd, list) or not all(isinstance(arg, str) for arg in cmd):
        raise SecurityValidationError("Command must be a list of string arguments.")

    logger.debug("Executing safe command: %s (timeout: %ds)", " ".join(cmd[:4]), timeout)

    try:
        result = subprocess.run(
            cmd,
            timeout=timeout,
            check=check,
            capture_output=capture_output,
            text=True,
        )
        return result
    except subprocess.TimeoutExpired as exc:
        logger.error("Command timed out after %d seconds: %s", timeout, cmd[:3])
        raise RuntimeError(f"Command timed out after {timeout} seconds") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("Command failed with code %d: %s\nStderr: %s", exc.returncode, cmd[:3], exc.stderr)
        raise


# -----------------------------------------------------------------------------
# Cryptographic Token Encryption at Rest
# -----------------------------------------------------------------------------

def _get_fernet_instance() -> Fernet:
    """Derive a deterministic Fernet cipher key from server configuration."""
    from ..config import settings
    raw_key = (
        (settings.TOKEN_ENCRYPTION_KEY or "").strip()
        or (settings.ENGINE_API_KEY or "").strip()
        or (settings.SUPABASE_JWT_SECRET or "").strip()
        or "gta6_shorts_engine_production_fallback_key_32bytes_min"
    )
    derived = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt a sensitive string (e.g. OAuth access or refresh token) at rest."""
    if plaintext is None:
        return None
    val = str(plaintext).strip()
    if not val:
        return ""
    try:
        fernet = _get_fernet_instance()
        return fernet.encrypt(val.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        logger.error("Encryption failure: %s", exc)
        raise RuntimeError("Failed to encrypt secret token") from exc


def decrypt_secret(ciphertext: Optional[str]) -> Optional[str]:
    """Decrypt a sensitive string at rest. If plaintext or invalid token, returns safely."""
    if ciphertext is None:
        return None
    val = str(ciphertext).strip()
    if not val:
        return ""
    try:
        fernet = _get_fernet_instance()
        return fernet.decrypt(val.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return val
    except Exception as exc:
        logger.warning("Decryption failure: %s; returning original", exc)
        return val


# -----------------------------------------------------------------------------
# SSRF-Protected HTTP Client with Redirect Verification
# -----------------------------------------------------------------------------

def safe_requests_get(
    url: str,
    headers: Optional[dict] = None,
    timeout: int = 15,
    max_redirects: int = 5,
    require_https: bool = True,
    **kwargs,
) -> requests.Response:
    """
    Execute an HTTP GET request with SSRF protection on the target URL AND on every redirect hop.
    """
    current_url = url
    for _ in range(max_redirects + 1):
        safe_url = validate_safe_url(current_url, require_https=require_https)
        resp = requests.get(safe_url, headers=headers, timeout=timeout, allow_redirects=False, **kwargs)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                return resp
            current_url = urllib.parse.urljoin(current_url, location)
            continue
        return resp
    raise SecurityValidationError(f"Exceeded maximum allowable redirects ({max_redirects}) during safe request")
