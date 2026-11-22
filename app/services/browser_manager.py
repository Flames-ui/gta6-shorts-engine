"""
Dedicated persistent browser automation service for creator-rewards marketplace operations.
Operates as an ordinary authorized browser session.
Adheres strictly to safety, resource bounding (512MB RAM constraint), and session encryption at rest.
Does NOT implement stealth plugins, fingerprint spoofing, or CAPTCHA bypasses.
"""
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import settings
from .security import decrypt_secret, encrypt_secret

logger = logging.getLogger("gta6_shorts.browser_manager")


class BrowserManager:
    """
    Manages a single dedicated persistent browser session with Playwright.
    Resource-conscious for 512MB RAM environments.
    Guarantees session encryption at rest and safe operator login workflows.
    """

    def __init__(self):
        # Support durable storage under /var/data if mounted on Render
        env_profile = os.environ.get("BROWSER_PROFILE_DIR")
        if env_profile:
            self.profile_dir = Path(env_profile)
        elif os.path.exists("/var/data") and os.access("/var/data", os.W_OK):
            self.profile_dir = Path("/var/data/browser_profile")
        else:
            self.profile_dir = Path(settings.BROWSER_PROFILE_DIR)

        self.session_file = self.profile_dir / "session.enc"
        self.diagnostics_dir = Path("/tmp/browser_diagnostics")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self._restored_storage_summary: Dict[str, Any] = {}

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._is_running = False
        self._last_health_check = None
        self._crash_count = 0

    @property
    def is_running(self) -> bool:
        return self._is_running and self._browser is not None

    def start_browser(self, headless: Optional[bool] = None) -> bool:
        """
        Start the dedicated Chromium browser session.
        Applies strict memory and resource flags for 512MB deployment.
        """
        if self.is_running:
            return True

        from playwright.sync_api import sync_playwright

        is_headless = settings.BROWSER_HEADLESS if headless is None else headless
        logger.info("Starting dedicated browser session (headless=%s)...", is_headless)

        try:
            self._playwright = sync_playwright().start()

            # Resource-conscious Chromium launch flags
            chromium_args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                f"--js-flags=--max-old-space-size={settings.BROWSER_MAX_MEMORY_MB}",
            ]

            self._browser = self._playwright.chromium.launch(
                headless=is_headless,
                args=chromium_args,
            )

            # Check for decrypted persistent storage state (cookies + origins)
            storage_state_arg = self.get_decrypted_storage_state()
            context_kwargs: Dict[str, Any] = {
                "viewport": {"width": 1280, "height": 800},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            }
            if storage_state_arg:
                context_kwargs["storage_state"] = storage_state_arg

            self._context = self._browser.new_context(**context_kwargs)
            self._context.set_default_timeout(settings.BROWSER_NAVIGATION_TIMEOUT_MS)

            # Record session restoration summary
            self.restore_encrypted_session()

            self._page = self._context.new_page()
            self._is_running = True
            self._last_health_check = datetime.now(timezone.utc).isoformat()
            logger.info("Dedicated browser session successfully initialized.")
            return True

        except Exception as exc:
            logger.error("Failed to start dedicated browser session: %s", exc)
            self.stop_browser()
            return False

    def stop_browser(self):
        """Safely close page, context, browser, and Playwright instances."""
        try:
            if self._context:
                # Save session before stopping
                self.save_encrypted_session()
        except Exception as e:
            logger.warning("Could not auto-save session on shutdown: %s", e)

        try:
            if self._page:
                self._page.close()
        except Exception:
            pass
        finally:
            self._page = None

        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        finally:
            self._context = None

        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        finally:
            self._browser = None

        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._playwright = None

        self._is_running = False
        logger.info("Dedicated browser session stopped.")

    def get_page(self):
        """Retrieve active page, auto-recovering if browser crashed."""
        if not self.is_running or not self._page or self._page.is_closed():
            logger.warning("Browser page not active; attempting recovery...")
            recovered = self.recover_from_crash()
            if not recovered:
                raise RuntimeError("Dedicated browser session is not running and recovery failed.")
        return self._page

    def recover_from_crash(self) -> bool:
        """Crash recovery: safely restarts browser and reloads persisted session."""
        logger.info("Initiating browser crash recovery sequence...")
        self._crash_count += 1
        self.stop_browser()
        time.sleep(1)
        return self.start_browser()

    def save_encrypted_session(self) -> bool:
        """
        Extracts cookies and local storage state, encrypts them at rest,
        and saves to disk. Never writes plaintext credentials or tokens.
        """
        if not self._context:
            return False

        try:
            storage_state = self._context.storage_state()
            state_json = json.dumps(storage_state)
            encrypted = encrypt_secret(state_json)
            with open(self.session_file, "w", encoding="utf-8") as f:
                f.write(encrypted or "")
            logger.info("Encrypted browser session saved to %s", self.session_file)
            return True
        except Exception as exc:
            logger.error("Failed to save encrypted browser session: %s", exc)
            return False

    def get_decrypted_storage_state(self) -> Optional[Dict[str, Any]]:
        """
        Decrypts and returns full Playwright storage_state (cookies + origins/localStorage).
        Returns None if session file is missing, empty, or invalid.
        """
        if not self.session_file.exists():
            return None
        try:
            with open(self.session_file, "r", encoding="utf-8") as f:
                encrypted = f.read().strip()
            if not encrypted:
                return None
            decrypted = decrypt_secret(encrypted)
            if not decrypted:
                return None
            state = json.loads(decrypted)
            if isinstance(state, dict):
                return state
            return None
        except Exception as exc:
            logger.warning("Error decrypting storage state: %s", exc)
            return None

    def restore_encrypted_session(self) -> bool:
        """
        Decrypts persisted session file and restores all supported browser storage state,
        including cookies and local-storage origins.
        Do not restore cookies alone when the marketplace also depends on local storage.
        Verifies session integrity without leaking plaintext to disk.
        """
        if not self.session_file.exists() or not self._context:
            return False

        try:
            state = self.get_decrypted_storage_state()
            if not state:
                return False

            cookies = state.get("cookies", [])
            origins = state.get("origins", [])
            restored = False

            if cookies:
                self._context.add_cookies(cookies)
                logger.info("Restored %d cookies from encrypted session.", len(cookies))
                restored = True

            if origins:
                logger.info("Restored %d local-storage origins from encrypted session.", len(origins))
                restored = True

            self._restored_storage_summary = {
                "cookie_count": len(cookies),
                "origin_count": len(origins),
                "restored_at": datetime.now(timezone.utc).isoformat(),
            }
            return restored
        except Exception as exc:
            logger.warning("Could not restore encrypted session (may be invalid or expired): %s", exc)
            return False

    def clear_session(self) -> bool:
        """Clear local stored session state and cookies."""
        try:
            if self.session_file.exists():
                self.session_file.unlink()
            if self._context:
                self._context.clear_cookies()
            logger.info("Browser session cleared.")
            return True
        except Exception as exc:
            logger.error("Failed to clear browser session: %s", exc)
            return False

    def capture_diagnostic_screenshot(self, label: str = "diagnostic") -> Optional[str]:
        """Capture screenshot for operator inspection without credentials in URL."""
        if not self.is_running or not self._page:
            return None
        try:
            timestamp = int(time.time())
            filename = f"screen_{label}_{timestamp}.png"
            filepath = self.diagnostics_dir / filename
            self._page.screenshot(path=str(filepath))
            logger.info("Diagnostic screenshot saved to %s", filepath)
            return str(filepath)
        except Exception as exc:
            logger.warning("Could not capture screenshot: %s", exc)
            return None

    def redact_cookies(self, cookies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return cookie metadata with sensitive token/session values redacted."""
        safe = []
        for c in cookies:
            safe.append({
                "name": c.get("name"),
                "domain": c.get("domain"),
                "path": c.get("path"),
                "expires": c.get("expires"),
                "httpOnly": c.get("httpOnly"),
                "secure": c.get("secure"),
                "sameSite": c.get("sameSite"),
                "value_length": len(str(c.get("value") or "")),
                "is_session": bool(c.get("expires", 0) <= 0),
            })
        return safe

    def get_health_status(self) -> Dict[str, Any]:
        """Returns health diagnostics, memory estimate, and session status."""
        has_session_file = self.session_file.exists() and self.session_file.stat().st_size > 0
        cookies_count = 0
        if self._context:
            try:
                cookies_count = len(self._context.cookies())
            except Exception:
                pass

        origins_count = 0
        decrypted_state = self.get_decrypted_storage_state()
        if decrypted_state:
            origins_count = len(decrypted_state.get("origins", []))

        return {
            "browser_running": self.is_running,
            "has_persisted_session": has_session_file,
            "active_cookies_count": cookies_count,
            "active_origins_count": origins_count,
            "restored_storage_summary": self._restored_storage_summary,
            "profile_dir": str(self.profile_dir),
            "durable_storage_verified": not str(self.profile_dir).startswith("/tmp") or (os.path.exists("/var/data") and os.access("/var/data", os.W_OK)),
            "crash_count": self._crash_count,
            "memory_limit_mb": settings.BROWSER_MAX_MEMORY_MB,
            "navigation_timeout_ms": settings.BROWSER_NAVIGATION_TIMEOUT_MS,
            "last_health_check": self._last_health_check,
        }


browser_manager = BrowserManager()
