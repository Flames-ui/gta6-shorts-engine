"""
Content Rewards automated marketplace operator utilizing dedicated persistent browser session.
Adheres strictly to authorized operator workflows, human handling of CAPTCHA/2FA,
gaming-only scope, zero fabrication, and encrypted session persistence at rest.
"""
import hashlib
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ...config import settings
from ..browser_manager import browser_manager
from ..campaign_rule_reader import campaign_rule_reader
from ..operator_action_queue import operator_action_queue
from ..opportunity_scorer import opportunity_scorer

logger = logging.getLogger("gta6_shorts.marketplace.content_rewards_operator")


class ContentRewardsOperator:
    """
    Operator engine for Content Rewards (https://contentrewards.com).
    Orchestrates browser discovery, full rule extraction, scoring,
    and operator-controlled campaign participation.
    """

    def __init__(self):
        self.base_url = settings.CONTENT_REWARDS_BASE_URL.rstrip("/")
        self.login_url = f"{self.base_url}/login"
        self.discover_url = f"{self.base_url}/c/discover"

    def check_session_status(self) -> Dict[str, Any]:
        """
        Inspect current browser session state, cookies, and login status.
        Does NOT leak raw session cookies or credentials.
        Detects actual authenticated marketplace indicators; never claims login succeeded
        merely because cookies exist. Detects expired sessions and alerts operator.
        """
        health = browser_manager.get_health_status()
        is_authenticated = False
        captcha_detected = False
        session_expired = False

        if browser_manager.is_running:
            try:
                page = browser_manager.get_page()
                captcha_detected = self._detect_captcha_or_challenge(page)
                is_authenticated = self._check_auth_indicators(page)
                # If persistent session exists or cookies exist but auth indicators fail, session is expired
                if not is_authenticated and (health["has_persisted_session"] or health["active_cookies_count"] > 0):
                    session_expired = True
                    operator_action_queue.push_action(
                        action_type="session_expired",
                        title="Content Rewards Session Expired",
                        message="Stored marketplace session has expired or authentication indicators are missing. Operator re-login required.",
                        marketplace_id="content_rewards",
                        severity="warning",
                    )
            except Exception as exc:
                logger.warning("Error checking live browser session status: %s", exc)

        return {
            "marketplace_id": "content_rewards",
            "base_url": self.base_url,
            "browser_running": health["browser_running"],
            "has_persisted_session": health["has_persisted_session"],
            "active_cookies_count": health["active_cookies_count"],
            "active_origins_count": health.get("active_origins_count", 0),
            "authenticated": is_authenticated,
            "session_expired": session_expired,
            "captcha_or_2fa_detected": captcha_detected,
            "operator_action_required": not is_authenticated or captcha_detected,
            "recommended_action": (
                "operator_relogin_required" if session_expired
                else ("operator_login_required" if not is_authenticated
                else ("captcha_resolution_required" if captcha_detected else "none"))
            ),
        }

    def request_operator_login(self) -> Dict[str, Any]:
        """
        Initiates operator login workflow:
        1. Ensures browser is running.
        2. Navigates to the official Content Rewards login page.
        3. Queues an operator action asking the human operator to complete login.
        4. Detects any CAPTCHA/2FA challenges.
        """
        if not browser_manager.is_running:
            started = browser_manager.start_browser()
            if not started:
                raise RuntimeError("Could not start dedicated browser for operator login.")

        page = browser_manager.get_page()
        logger.info("Navigating to Content Rewards login page: %s", self.login_url)
        try:
            page.goto(self.login_url, wait_until="domcontentloaded", timeout=settings.BROWSER_NAVIGATION_TIMEOUT_MS)
        except Exception as exc:
            logger.warning("Navigation to login URL encountered notice: %s", exc)

        # Check for CAPTCHA/challenge
        captcha = self._detect_captcha_or_challenge(page)
        screenshot_path = browser_manager.capture_diagnostic_screenshot("login_prompt")

        action = operator_action_queue.push_action(
            action_type="login_required" if not captcha else "captcha_or_2fa_required",
            title="Content Rewards Operator Login Required",
            message=(
                f"Please navigate to the browser session and complete login at {self.login_url}. "
                "The system will pause for your credentials, 2FA, and any CAPTCHA verification."
            ),
            marketplace_id="content_rewards",
            severity="warning",
            metadata={"login_url": self.login_url, "screenshot": screenshot_path},
        )

        return {
            "status": "awaiting_operator_login",
            "login_url": self.login_url,
            "action_id": action["id"],
            "captcha_detected": captcha,
            "screenshot_path": screenshot_path,
            "instructions": (
                "1. Complete login in the dedicated browser session.\n"
                "2. Resolve any 2FA or CAPTCHA prompt directly.\n"
                "3. Call POST /api/v1/marketplace/operator/login-resume once authenticated."
            ),
        }

    def resume_after_login(self, operator_id: str = "operator") -> Dict[str, Any]:
        """
        Called after the operator finishes logging in.
        Verifies login indicators, persists encrypted session cookies,
        and resolves pending login action queue items.
        """
        if not browser_manager.is_running:
            return {"authenticated": False, "message": "Browser is not running."}

        page = browser_manager.get_page()
        is_auth = self._check_auth_indicators(page)

        if not is_auth:
            # Refresh current page to double-check
            try:
                page.reload(wait_until="domcontentloaded")
                is_auth = self._check_auth_indicators(page)
            except Exception:
                pass

        if is_auth:
            # Save encrypted session to disk
            browser_manager.save_encrypted_session()
            operator_action_queue.clear_actions_by_type("login_required", "content_rewards")
            operator_action_queue.clear_actions_by_type("captcha_or_2fa_required", "content_rewards")
            logger.info("Operator login verified and encrypted session persisted.")
            return {
                "authenticated": True,
                "operator_id": operator_id,
                "message": "Content Rewards operator session is authenticated and encrypted at rest.",
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            return {
                "authenticated": False,
                "operator_id": operator_id,
                "message": "Login indicators not detected on page. Please ensure you have completed login.",
            }

    def discover_live_campaigns(self, limit: int = 15) -> List[Dict[str, Any]]:
        """
        Navigates to Content Rewards discover catalog via the authorized browser session.
        Discovers campaigns, opens detail views, extracts complete rules,
        filters strictly for gaming & TikTok/YouTube Shorts, and scores opportunities.
        """
        if not browser_manager.is_running:
            logger.info("Browser not active; returning empty discovery list.")
            return []

        page = browser_manager.get_page()
        # Verify authenticated session
        if not self._check_auth_indicators(page):
            logger.warning("Attempted discovery with unauthenticated session; queuing login action.")
            operator_action_queue.push_action(
                action_type="login_required",
                title="Session Authentication Required for Live Discovery",
                message="Cannot discover campaigns without authenticated Content Rewards session.",
                marketplace_id="content_rewards",
            )
            return []

        logger.info("Navigating to discover catalog: %s", self.discover_url)
        try:
            page.goto(self.discover_url, wait_until="domcontentloaded", timeout=settings.BROWSER_NAVIGATION_TIMEOUT_MS)
        except Exception as exc:
            logger.error("Navigation error navigating to discover catalog: %s", exc)
            return []

        # Check for CAPTCHA
        if self._detect_captcha_or_challenge(page):
            logger.warning("Human verification detected on discover catalog.")
            operator_action_queue.push_action(
                action_type="captcha_or_2fa_required",
                title="Human Verification Required on Discover Page",
                message="A CAPTCHA or verification prompt was detected. Please resolve it in the browser.",
                marketplace_id="content_rewards",
            )
            return []

        # Extract campaign cards from page DOM
        discovered_campaigns: List[Dict[str, Any]] = []
        try:
            # Query campaign cards or links matching campaign paths
            links = page.query_selector_all("a[href*='/c/'], a[href*='/campaign/']")
            seen_urls = set()

            for link in links[:limit * 2]:
                href = link.get_attribute("href")
                if not href or href in seen_urls:
                    continue
                full_url = href if href.startswith("http") else f"{self.base_url}{href}"
                seen_urls.add(href)

                # Extract campaign details by navigating or inspecting
                card_text = link.inner_text() or ""
                # Preliminary gaming filter to avoid opening non-gaming campaigns
                low_text = card_text.lower()
                if not any(kw in low_text for kw in ["gta", "game", "gaming", "stream", "clipping", "clips"]):
                    continue

                details = self.extract_campaign_details(full_url)
                if details and details.get("is_gaming"):
                    discovered_campaigns.append(details)
                    if len(discovered_campaigns) >= limit:
                        break

        except Exception as exc:
            logger.error("Error extracting campaign links: %s", exc)

        return discovered_campaigns

    def extract_campaign_details(self, campaign_url: str) -> Optional[Dict[str, Any]]:
        """
        Opens campaign detail page, extracts complete original rules, reward info,
        deadlines, brand approval requirements, and source video instructions.
        Applies campaign_rule_reader without fabricating missing fields.
        """
        if not browser_manager.is_running:
            return None

        page = browser_manager.get_page()
        logger.info("Extracting live campaign details from: %s", campaign_url)
        try:
            page.goto(campaign_url, wait_until="domcontentloaded", timeout=settings.BROWSER_NAVIGATION_TIMEOUT_MS)
        except Exception as exc:
            logger.error("Failed to load campaign URL %s: %s", campaign_url, exc)
            return None

        # Capture complete page text for auditing
        page_text = page.inner_text("body") or ""
        title = page.title() or "Discovered Campaign"

        # Check for brand approval requirement
        low_page = page_text.lower()
        requires_brand_approval = any(
            kw in low_page
            for kw in [
                "requires approval",
                "manual approval",
                "apply to join",
                "application pending",
                "brand must approve",
            ]
        )

        # Run rule reader on complete text
        analysis = campaign_rule_reader.analyze_rules(
            raw_rules=page_text,
            campaign_title=title,
            campaign_url=campaign_url,
        )

        # Generate stable, deterministic ID based on campaign URL or external ID to prevent duplicates
        url_normalized = campaign_url.strip().lower()
        stable_hash = hashlib.sha256(url_normalized.encode("utf-8")).hexdigest()[:12]
        stable_id = f"camp_ext_{stable_hash}"

        now_str = datetime.now(timezone.utc).isoformat()
        campaign_record = {
            "id": stable_id,
            "external_id": stable_hash,
            "marketplace_id": "content_rewards",
            "marketplace_name": "Content Rewards",
            "name": title,
            "url": campaign_url,
            "raw_rules": page_text,
            "status": "eligible" if (analysis.is_confident and analysis.accepted_platforms and analysis.accepted_games) else "needs_review",
            "accepted_platforms": analysis.accepted_platforms,
            "eligible_games": analysis.accepted_games,
            "content_categories": ["gaming"],
            "required_hashtags": analysis.required_hashtags,
            "required_mentions": analysis.required_mentions,
            "required_captions": analysis.required_captions,
            "min_clip_duration_seconds": analysis.min_clip_duration_seconds,
            "max_clip_duration_seconds": analysis.max_clip_duration_seconds,
            "reward_model": analysis.reward_model,
            "payout_amount": analysis.payout_amount,
            "cpm": analysis.cpm,
            "reward_per_view": analysis.reward_per_view,
            "view_qualification_rules": analysis.view_qualification_rules,
            "source_video_requirements": analysis.allowed_footage or "See campaign detail rules",
            "prohibited_editing_techniques": analysis.disqualification_reasons,
            "requires_brand_approval": requires_brand_approval,
            "application_status": "not_applied",
            "operator_approved": False,
            "ambiguities": analysis.ambiguities,
            "date_last_verified": now_str,
            "created_at": now_str,
            "updated_at": now_str,
        }

        # Opportunity score
        scoring = opportunity_scorer.score_campaign(campaign_record)
        campaign_record["opportunity_score"] = scoring["overall_score"]
        campaign_record["opportunity_tier"] = scoring["tier"]
        campaign_record["opportunity_explanations"] = scoring["explanations"]
        campaign_record["is_gaming"] = bool(analysis.accepted_games or "gaming" in title.lower() or "gta" in title.lower())

        return campaign_record

    def apply_to_campaign(
        self,
        campaign_id: str,
        campaign_url: str,
        operator_approved_terms: bool = False,
        campaign_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Submits application or joins campaign where permitted.
        Verification checklist before clicking Join or Apply:
        1. Verify campaign exists and URL is valid.
        2. Verify campaign eligibility and non-expired / non-exhausted status.
        3. Strictly require explicit operator confirmation before accepting terms.
        4. Inspect resulting page to confirm genuine marketplace success state.
        5. Distinguish joined, applied, awaiting_brand_approval, rejected, and manual_action_required.
        Never infer successful enrollment merely because a button was clicked.
        """
        if not operator_approved_terms:
            raise ValueError(
                "Explicit operator approval is strictly required before submitting an application "
                "or accepting campaign terms."
            )

        if not campaign_url or not campaign_url.startswith("http"):
            raise ValueError("Valid campaign URL is required for application submission.")

        if campaign_data:
            if campaign_data.get("status") in ("expired", "exhausted", "ineligible"):
                raise ValueError(f"Cannot apply to campaign with status: {campaign_data.get('status')}")

        if not browser_manager.is_running:
            raise RuntimeError("Browser session not active; cannot submit campaign application.")

        page = browser_manager.get_page()
        try:
            page.goto(campaign_url, wait_until="domcontentloaded", timeout=settings.BROWSER_NAVIGATION_TIMEOUT_MS)
        except Exception as exc:
            raise RuntimeError(f"Could not load campaign URL: {exc}")

        # Check for expired/exhausted banner on page
        page_text = page.inner_text("body").lower()
        if any(term in page_text for term in ["campaign ended", "budget exhausted", "applications closed", "campaign expired"]):
            return {
                "campaign_id": campaign_id,
                "application_status": "rejected",
                "reason": "campaign_closed_or_exhausted",
                "message": "Marketplace indicates the campaign has ended or allowance is exhausted.",
            }

        # Locate Apply/Join button
        button = page.query_selector("button:has-text('Join'), button:has-text('Apply'), a:has-text('Join Campaign'), button:has-text('Participate')")
        if not button:
            # Check if already enrolled
            if "already enrolled" in page_text or "leave campaign" in page_text or "your submission" in page_text:
                return {
                    "campaign_id": campaign_id,
                    "application_status": "joined",
                    "already_enrolled": True,
                    "message": "Marketplace indicates creator is already enrolled in this campaign.",
                }
            return {
                "campaign_id": campaign_id,
                "application_status": "manual_action_required",
                "message": "Join or Apply button not found. Operator must review and apply manually in session.",
            }

        # Click apply button safely
        button.click()
        time.sleep(2)

        # Inspect resulting page for genuine marketplace success state
        resulting_text = page.inner_text("body").lower()

        if any(term in resulting_text for term in ["application submitted", "pending approval", "under review", "awaiting brand approval"]):
            app_status = "awaiting_brand_approval"
            success_verified = True
        elif any(term in resulting_text for term in ["you are enrolled", "joined successfully", "ready to submit", "campaign joined", "upload video"]):
            app_status = "joined"
            success_verified = True
        elif any(term in resulting_text for term in ["rejected", "not eligible", "requirements not met"]):
            app_status = "rejected"
            success_verified = False
        else:
            # Fallback if no clear confirmation message appeared
            app_status = "manual_action_required"
            success_verified = False

        screenshot_path = browser_manager.capture_diagnostic_screenshot(f"application_{campaign_id}")

        return {
            "campaign_id": campaign_id,
            "application_status": app_status,
            "success_verified": success_verified,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "operator_approved_terms": True,
            "diagnostic_screenshot": screenshot_path,
            "message": f"Application processed with verified marketplace status: '{app_status}'.",
        }

    def _check_auth_indicators(self, page) -> bool:
        """
        Detect actual authenticated marketplace indicators.
        Never claim login succeeded merely because cookies exist.
        Requires presence of positive authenticated elements (account, logout, profile, avatar)
        and absence of unauthenticated login buttons.
        """
        try:
            # Check for visible login/sign-in buttons -> definitely unauthenticated
            unauth_btn = page.query_selector(
                "a[href*='/login'], a[href*='/signin'], button:has-text('Sign In'), button:has-text('Log In')"
            )
            if unauth_btn and unauth_btn.is_visible():
                return False

            curr_url = (page.url or "").lower()
            if "/login" in curr_url or "/signup" in curr_url or "/auth" in curr_url:
                return False

            # Positive indicators of authentic login
            auth_selectors = [
                "a[href*='/logout']",
                "a[href*='/signout']",
                "a[href*='/account']",
                "a[href*='/profile']",
                "[data-testid='user-avatar']",
                "[data-testid='user-profile']",
                ".user-logged-in",
                "button:has-text('Sign Out')",
                "button:has-text('Log Out')",
            ]
            has_positive_elem = any(bool(page.query_selector(sel)) for sel in auth_selectors)
            if has_positive_elem:
                return True

            # If user is on an authenticated page like dashboard or earnings and cookies exist
            if any(path in curr_url for path in ["/dashboard", "/account", "/my-campaigns", "/earnings"]):
                context_cookies = page.context.cookies()
                auth_cookie_names = ["session", "auth", "token", "jwt", "cr_auth", "user_session"]
                has_cookie = any(
                    any(c_name in c.get("name", "").lower() for c_name in auth_cookie_names)
                    for c in context_cookies
                )
                if has_cookie:
                    return True

            return False
        except Exception:
            return False

    def _detect_captcha_or_challenge(self, page) -> bool:
        """Detects presence of Cloudflare Turnstile, hCaptcha, reCAPTCHA, or human challenges."""
        try:
            selectors = [
                "iframe[src*='cloudflare']",
                "iframe[src*='recaptcha']",
                "iframe[src*='hcaptcha']",
                "div#cf-turnstile",
                "div.g-recaptcha",
                "div.h-captcha",
                "input[name='cf-turnstile-response']",
            ]
            for sel in selectors:
                if page.query_selector(sel):
                    return True

            title = (page.title() or "").lower()
            if "just a moment" in title or "attention required" in title or "security challenge" in title:
                return True

            return False
        except Exception:
            return False


content_rewards_operator = ContentRewardsOperator()
