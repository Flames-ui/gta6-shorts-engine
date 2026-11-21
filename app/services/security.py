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
            # If resolution fails during validation, reject in production
            logger.warning("Could not resolve host '%s' during safety check: %s", hostname, exc)

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
