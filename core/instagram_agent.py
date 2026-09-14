"""
================================================================================
⚠️ WARNING: UNOFFICIAL INSTAGRAM DM AUTOMATION MODULE
================================================================================
This module automates Instagram Direct Messages (https://www.instagram.com/direct/)
using browser DOM manipulation via Playwright with a persistent browser profile.

CRITICAL NOTICES & ACCOUNT RISK DISCLAIMER:
1. NOT AN OFFICIAL API: This implementation interacts directly with the Instagram Web
   DOM tree. It does NOT use Meta Graph API.
2. SCOPE RESTRICTION: This module STRICTLY implements direct messaging (DM send and
   unread read) automation. Absolutely NO likes, follows, comments, or story views
   are supported or implemented.
3. BAN RISK SAFEGUARDS:
   - Cooldown: Hard limit of INSTAGRAM_RATE_LIMIT_SECONDS (default 90s) between messages
     to the same recipient.
   - Daily Cap: Hard limit of INSTAGRAM_MAX_DMS_PER_DAY (default 20) across all recipients,
     tracked in data/instagram_usage.json with atomic writes.
   - Human Emulation: Randomized pauses (1.5-4.0s) and character typing jitter (30-90ms).
   - DOM Verification: Message delivery is verified in the DOM before confirming success.
   - Challenge Detection: Checkpoints / 2FA trigger manual_action_required immediately.
   - Audit Logging: All events logged to instagram_audit.log.
================================================================================
"""

import os
import json
import time
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from config import (
    INSTAGRAM_PROFILE_DIR,
    INSTAGRAM_AUDIT_LOG,
    INSTAGRAM_USAGE_FILE,
    INSTAGRAM_RATE_LIMIT_SECONDS,
    INSTAGRAM_MAX_DMS_PER_DAY,
    INSTAGRAM_HEADLESS,
    DATA_DIR
)
from core.logger import get_logger

logger = get_logger("astra.instagram")


# =====================================================================
# Rate Limiting & Atomic Daily Cap Safeguards
# =====================================================================

_LAST_DM_TIMESTAMP: Dict[str, float] = {}
_RATE_LIMIT_LOCK = threading.Lock()
_USAGE_LOCK = threading.Lock()


class ActionResult(dict):
    """Dictionary subclass supporting containment checks on message for backward compatibility."""
    def __contains__(self, item):
        if super().__contains__(item):
            return True
        if isinstance(item, str):
            msg = self.get("message", "")
            if isinstance(msg, str) and item in msg:
                return True
        return False


def _get_usage_file() -> Path:
    inst = getattr(InstagramSession, "_instance", None)
    if inst and getattr(inst, "_usage_file", None) is not None:
        return Path(inst._usage_file)
    if "INSTAGRAM_USAGE_FILE" in globals() and globals()["INSTAGRAM_USAGE_FILE"]:
        return Path(globals()["INSTAGRAM_USAGE_FILE"])
    if "DATA_DIR" in globals() and DATA_DIR:
        return Path(DATA_DIR) / "instagram_usage.json"
    return Path("data/instagram_usage.json")


def check_rate_limit(username: str, limit_seconds: int = INSTAGRAM_RATE_LIMIT_SECONDS) -> Tuple[bool, float]:
    """
    Enforces per-recipient rate limiting: Max 1 DM per recipient per limit_seconds (default 90s).
    Returns (is_allowed, seconds_remaining).
    """
    norm_name = username.strip().lower().lstrip("@")
    now = time.time()
    with _RATE_LIMIT_LOCK:
        if norm_name not in _LAST_DM_TIMESTAMP:
            return True, 0.0
        last_time = _LAST_DM_TIMESTAMP[norm_name]
        elapsed = now - last_time
        if elapsed < limit_seconds:
            return False, round(limit_seconds - elapsed, 1)
        return True, 0.0


def record_reply_sent(username: str) -> None:
    """Records the timestamp of an automated DM to enforce the cooldown."""
    norm_name = username.strip().lower().lstrip("@")
    with _RATE_LIMIT_LOCK:
        _LAST_DM_TIMESTAMP[norm_name] = time.time()


def _get_today_date_str() -> str:
    """Returns today's date formatted as YYYY-MM-DD."""
    return datetime.now().strftime("%Y-%m-%d")


def check_daily_cap(max_limit: int = None) -> Tuple[bool, int, int]:
    """
    Checks if the daily DM ceiling has been reached without incrementing.
    Returns (is_allowed, current_count, max_limit).
    """
    if max_limit is None:
        max_limit = globals().get("INSTAGRAM_MAX_DMS_PER_DAY", INSTAGRAM_MAX_DMS_PER_DAY)
    today = _get_today_date_str()
    with _USAGE_LOCK:
        data = _read_usage_data()
        current_count = int(data.get(today, 0))
        if current_count >= max_limit:
            return False, current_count, max_limit
        return True, current_count, max_limit


def increment_daily_cap() -> int:
    """
    Atomically increments today's DM count using a temporary file and os.replace().
    Returns the new count.
    """
    today = _get_today_date_str()
    with _USAGE_LOCK:
        data = _read_usage_data()
        new_count = int(data.get(today, 0)) + 1
        data[today] = new_count
        _write_usage_data_atomic(data)
        return new_count


def _read_usage_data() -> Dict[str, int]:
    """Reads usage data from resolved usage file safely."""
    usage_f = _get_usage_file()
    try:
        if usage_f.exists():
            with open(usage_f, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
    except Exception as e:
        print(f"[Instagram Usage Warning] Failed to read usage file: {e}")
    return {}


def _write_usage_data_atomic(data: Dict[str, int]) -> None:
    """Writes usage data atomically using a temp file + os.replace."""
    usage_f = _get_usage_file()
    try:
        usage_f.parent.mkdir(parents=True, exist_ok=True)
        temp_file = usage_f.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, usage_f)
    except Exception as e:
        print(f"[Instagram Usage Error] Failed atomic write to usage file: {e}")


def log_audit_event(username: str, message: str, status: str, details: str = "") -> None:
    """
    Appends an entry to instagram_audit.log for full auditability.
    Statuses: SUCCESS, FAILED, RATE_LIMITED, DAILY_CAP_REACHED, MANUAL_ACTION_REQUIRED.
    """
    try:
        timestamp = datetime.now().isoformat()
        clean_user = username.strip().lstrip("@")
        clean_msg = message.replace("\n", " ").strip()[:200]
        entry = (
            f"[{timestamp}] STATUS={status.upper()} | "
            f"USER='{clean_user}' | "
            f"MSG_LEN={len(message)} | "
            f"PREVIEW='{clean_msg}'"
        )
        if details:
            entry += f" | DETAILS={details}"
        entry += "\n"

        with open(INSTAGRAM_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"[Instagram Audit Warning] Failed to append to audit log: {e}")


# =====================================================================
# Human Interaction Emulation Helpers
# =====================================================================

def human_delay(min_s: float = 1.5, max_s: float = 4.0) -> None:
    """Introduces randomized human-speed delay between page interactions."""
    time.sleep(random.uniform(min_s, max_s))


def human_type(target: Any, text: str) -> None:
    """
    Types text character-by-character with 30-90ms jitter to mimic human typing
    and prevent triggering automated bot heuristics.
    """
    for char in text:
        if hasattr(target, "type"):
            try:
                target.type(char)
            except TypeError:
                if hasattr(target, "keyboard"):
                    target.keyboard.type(char)
        elif hasattr(target, "keyboard"):
            target.keyboard.type(char)
        time.sleep(random.uniform(0.03, 0.09))


# =====================================================================
# Persistent Browser Session Management
# =====================================================================

class InstagramSession:
    """
    Manages the persistent Playwright Chromium browser session for Instagram.
    Maintains login state across calls with thread safety.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(InstagramSession, cls).__new__(cls)
                cls._instance._init_session()
            return cls._instance

    def _init_session(self):
        self._playwright = None
        self._context = None
        self._page = None
        self.last_sent_time = 0.0
        self._usage_file = None

    @property
    def usage_file(self) -> Path:
        if self._usage_file is not None:
            return self._usage_file
        return _get_usage_file()

    @usage_file.setter
    def usage_file(self, val):
        self._usage_file = Path(val)

    @classmethod
    def get_instance(cls) -> "InstagramSession":
        return cls()

    def get_page(self):
        """Returns the active Instagram page, launching browser if necessary."""
        from playwright.sync_api import sync_playwright

        with self._lock:
            if self._page and not self._page.is_closed():
                return self._page

            if self._playwright is None:
                self._playwright = sync_playwright().start()

            INSTAGRAM_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

            launch_kwargs = {
                "user_data_dir": str(INSTAGRAM_PROFILE_DIR),
                "headless": INSTAGRAM_HEADLESS,
                "viewport": {"width": 1280, "height": 800},
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage"
                ]
            }
            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    channel="chrome", **launch_kwargs
                )
            except Exception:
                self._context = self._playwright.chromium.launch_persistent_context(
                    **launch_kwargs
                )

            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            return self._page

    def close(self):
        """Closes the browser session cleanly."""
        with self._lock:
            try:
                if self._context:
                    self._context.close()
                if self._playwright:
                    self._playwright.stop()
            except Exception:
                pass
            finally:
                self._page = None
                self._context = None
                self._playwright = None

    def _human_delay(self, min_s=1.5, max_s=4.0):
        human_delay(min_s, max_s)

    def _type_humanly(self, selector: str, text: str):
        page = self.get_page() if not self._page else self._page
        if page:
            element = page.locator(selector)
            element.click()
            self._human_delay(0.5, 1.0)
            for char in text:
                page.keyboard.type(char, delay=random.uniform(30, 90))

    def _check_manual_action(self) -> bool:
        page = self.get_page() if not self._page else self._page
        if page:
            is_ch, _ = detect_login_or_challenge(page)
            return is_ch
        return False

    def log_audit_event(self, status: str, recipient: str, details: str):
        log_audit_event(recipient, "", status, details)

    def get_unread_dms(self, max_chats=10):
        page = self._page if (self._page and not self._page.is_closed()) else None
        return get_unread_dms(max_chats=max_chats, _page=page)

    def send_instagram_dm(self, username: str, message: str):
        page = self._page if (self._page and not self._page.is_closed()) else None
        return send_instagram_dm(username, message, _page=page)

    def _open_chat_via_search(self, clean_user: str) -> bool:
        page = self.get_page() if not self._page else self._page
        return _open_chat_via_search(page, clean_user)

    def search_instagram_user(self, query: str):
        page = self._page if (self._page and not self._page.is_closed()) else None
        return search_instagram_user(query, _page=page)


def get_ig_session() -> InstagramSession:
    """Returns the global InstagramSession singleton."""
    return InstagramSession()



# =====================================================================
# Challenge & Security Detection
# =====================================================================

def detect_login_or_challenge(page: Any) -> Tuple[bool, str]:
    """
    Checks if Instagram is prompting for login, 2FA, checkpoint, or security verification.
    Returns (is_challenge_detected, reason_message).
    """
    try:
        url = getattr(page, "url", "") or ""
        url_lower = url.lower()

        # Check URL markers
        challenge_markers = ["/accounts/login", "/challenge/", "/two_factor", "/checkpoint/", "/two_factor/"]
        for marker in challenge_markers:
            if marker in url_lower:
                return True, f"Detected verification URL: {marker}"

        # Check login inputs
        if page.query_selector('input[name="username"]') and page.query_selector('input[name="password"]'):
            return True, "Login form inputs detected"

        # Check 2FA or security challenge elements
        security_selectors = [
            'input[name="verificationCode"]',
            'input[name="security_code"]',
            'button:has-text("Send Security Code")',
            'h2:has-text("Suspicious Activity")',
            'h2:has-text("Help us confirm that you own this account")',
            'h2:has-text("Two-Factor Authentication")',
            'h2:has-text("Confirm your info")'
        ]
        for sel in security_selectors:
            try:
                elem = page.query_selector(sel)
                if elem:
                    return True, f"Security challenge element found: {sel}"
            except Exception:
                pass

    except Exception as e:
        print(f"[Instagram Challenge Check Warning] Error inspecting page: {e}")

    return False, ""


def _dismiss_popups(page: Any) -> None:
    """Dismisses common Instagram dialogs like 'Turn on Notifications' or cookies."""
    dismiss_buttons = [
        'button:has-text("Not Now")',
        'button:has-text("Cancel")',
        'button:has-text("Decline optional cookies")',
        'button:has-text("Only allow essential cookies")',
        'div[role="dialog"] button:has-text("Not Now")'
    ]
    for sel in dismiss_buttons:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                time.sleep(0.5)
        except Exception:
            pass


# =====================================================================
# Core Instagram Direct Automation Functions
# =====================================================================

def get_unread_dms(max_chats: int = 10, _page=None) -> List[Dict[str, Any]]:
    """
    Retrieves unread direct messages from Instagram Web.

    Returns:
        List of dicts: [
            {
                "sender": str,
                "unread_count": int,
                "snippet": str,
                "timestamp": str,
                "status": "unread" | "manual_action_required" | "error"
            },
            ...
        ]
    """
    try:
        page = _page
        if page is None:
            session = InstagramSession.get_instance()
            page = session.get_page()

        # Navigate to direct inbox if not already there
        if "instagram.com/direct" not in getattr(page, "url", ""):
            page.goto("https://www.instagram.com/direct/inbox/", wait_until="domcontentloaded", timeout=45000)
            human_delay(2.0, 3.5)

        # Check for challenge or login requirement
        is_challenge, reason = detect_login_or_challenge(page)
        if is_challenge:
            log_audit_event("SYSTEM", "Check Unread DMs", "MANUAL_ACTION_REQUIRED", reason)
            return [{
                "sender": "SYSTEM",
                "unread_count": 0,
                "snippet": "Instagram par manual login ya security verification chahiye hai. Kripya browser window check karein.",
                "snippet": "Manual login or security verification is required on Instagram. Please check the browser window.",
                "timestamp": datetime.now().strftime("%I:%M %p"),
                "status": "manual_action_required"
            }]

        _dismiss_popups(page)

        # Wait briefly for inbox message list
        try:
            page.wait_for_selector('div[role="listitem"], div[role="list"], div[aria-label*="Chats"], div[aria-label*="Direct"]', timeout=8000)
        except Exception:
            pass

        # Locate inbox conversation items
        rows = page.query_selector_all('div[role="listitem"], div[role="button"][tabindex="0"]')
        unread_list: List[Dict[str, Any]] = []

        for row in rows:
            if len(unread_list) >= max_chats:
                break

            try:
                # Instagram marks unread conversations with blue dot or bold text
                unread_dot = row.query_selector('span[class*="x193iq5w"], div[class*="x193iq5w"], span[aria-label*="unread"]')
                is_bold = False
                bold_span = row.query_selector('span[style*="font-weight: 600"], span[style*="font-weight: 700"]')
                if bold_span:
                    is_bold = True

                if not unread_dot and not is_bold:
                    continue

                # Extract sender name
                sender = ""
                name_elem = row.query_selector('span[class*="x1lliihq"], span[dir="auto"]')
                if name_elem:
                    sender = name_elem.inner_text().strip()

                if not sender:
                    sender = "Instagram User"

                # Extract snippet
                snippet = ""
                snippet_elem = row.query_selector('span[class*="x1rg5ohu"]')
                if snippet_elem:
                    snippet = snippet_elem.inner_text().strip()
                if not snippet:
                    extra_elem = row.query_selector('div[dir="auto"]')
                    if extra_elem and extra_elem.inner_text().strip() != sender:
                        snippet = extra_elem.inner_text().strip()


                timestamp = datetime.now().strftime("%I:%M %p")

                unread_list.append({
                    "sender": sender,
                    "unread_count": 1,
                    "snippet": snippet,
                    "timestamp": timestamp,
                    "status": "unread"
                })
            except Exception:
                continue

        return unread_list

    except Exception as e:
        return [{
            "sender": "SYSTEM",
            "unread_count": 0,
            "snippet": f"Instagram DMs check karne me truti: {e}",
            "timestamp": datetime.now().strftime("%I:%M %p"),
            "status": "error"
        }]


def search_instagram_user(query: str = "", _page=None) -> Dict[str, Any]:
    """
    Searches for or opens an Instagram profile or explore search safely without sending messages.
    If query is a username (e.g., 'jay', '@jay'), navigates directly to their profile.
    If query is a topic or general search, navigates to Instagram search/explore.
    """
    clean_query = query.strip().lstrip("@")
    try:
        session = InstagramSession.get_instance()
        page = _page or session.get_page()

        if not clean_query:
            page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
            human_delay(1.0, 2.0)
            _dismiss_popups(page)
            return {
                "success": True,
                "action": "search_instagram_user",
                "query": "",
                "message": "Instagram open kar diya hai."
            }

        # Route 1: Direct profile navigation
        profile_url = f"https://www.instagram.com/{clean_query}/"
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            human_delay(1.0, 2.0)
            _dismiss_popups(page)

            is_ch, reason = detect_login_or_challenge(page)
            if is_ch:
                return {
                    "success": False,
                    "action": "search_instagram_user",
                    "status": "manual_action_required",
                    "message": "Manual login or security verification is required on Instagram. Please check the browser window."
                }

            is_404 = False
            try:
                if page.query_selector('text="Sorry, this page isn\'t available."') or page.query_selector('h2:has-text("Sorry, this page isn\'t available.")'):
                    is_404 = True
            except Exception:
                pass

            if not is_404:
                return {
                    "success": True,
                    "action": "search_instagram_user",
                    "query": clean_query,
                    "profile_url": profile_url,
                    "message": f"Opened Instagram profile for '{clean_query}'."
                }
        except Exception as pe:
            logger.warning(f"Profile navigation failed for '{clean_query}': {pe}")

        # Route 2: Explore / Search fallback
        search_url = f"https://www.instagram.com/explore/search/keyword/?q={urllib.parse.quote_plus(clean_query)}"
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            human_delay(1.0, 2.0)
            return {
                "success": True,
                "action": "search_instagram_user",
                "query": clean_query,
                "url": search_url,
                "message": f"Searched for '{clean_query}' on Instagram."
            }
        except Exception as se:
            return {
                "success": False,
                "action": "search_instagram_user",
                "error": str(se),
                "message": f"Could not search on Instagram: {se}"
            }
    except Exception as e:
        logger.error(f"search_instagram_user error: {e}")
        return {
            "success": False,
            "action": "search_instagram_user",
            "error": str(e),
            "message": f"Instagram search error: {e}"
        }


def _open_chat_via_search(page: Any, clean_user: str) -> bool:
    """
    Fallback method to find user and open chat thread via https://www.instagram.com/direct/new/.
    Properly handles Instagram's React state and delays for the search modal:
    1. Types username in queryBox and waits specifically for network to idle or search skeleton to disappear.
    2. Targets strictly the matching user's interactive row or checkbox (never broad random suggested checkboxes).
    3. Waits for the 'Chat' / 'Next' button to become is_enabled() before clicking.
    4. Wrapped in robust try/except blocks to safely return False on failure.
    """
    try:
        page.goto("https://www.instagram.com/direct/new/", wait_until="domcontentloaded", timeout=30000)
        human_delay(1.0, 2.0)
        _dismiss_popups(page)

        # 1. Locate search input
        search_input_selector = 'input[name="queryBox"], input[placeholder*="Search"], input[aria-label*="Search"]'
        try:
            page.wait_for_selector(search_input_selector, timeout=8000)
        except Exception:
            pass

        search_input = page.query_selector(search_input_selector)
        if not search_input:
            logger.warning("[Instagram Search] Search input queryBox not found in modal.")
            return False

        search_input.click()
        human_delay(0.3, 0.6)
        human_type(search_input, clean_user)
        human_delay(1.0, 2.0)

        # Wait specifically for network to idle or search skeleton to disappear
        try:
            if hasattr(page, "wait_for_load_state"):
                page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

        # Also wait for search skeleton to disappear if present
        try:
            page.wait_for_selector('div[role="progressbar"], div[data-visualcompletion="loading-state"]', state="detached", timeout=3000)
        except Exception:
            pass

        # 2. Target the interactive row or checkbox strictly for clean_user
        # Strict matching ensures we NEVER click a random account from the "Suggested" list!
        user_selected = False
        for _ in range(12):
            if hasattr(page, "locator"):
                try:
                    row_loc = page.locator(f'div[role="dialog"] div[role="button"]:has-text("{clean_user}")')
                    cnt = row_loc.count() if callable(getattr(row_loc, "count", None)) else 0
                    if isinstance(cnt, int) and cnt == 0:
                        row_loc = page.locator(f'div[role="button"]:has-text("{clean_user}")')
                        cnt = row_loc.count() if callable(getattr(row_loc, "count", None)) else 0

                    if isinstance(cnt, int) and cnt > 0:
                        clicked_cb = False
                        try:
                            cb = row_loc.first.locator('input[type="checkbox"], div[role="checkbox"]')
                            cb_cnt = cb.count() if callable(getattr(cb, "count", None)) else 0
                            if isinstance(cb_cnt, int) and cb_cnt > 0:
                                cb.first.click()
                                clicked_cb = True
                        except Exception:
                            pass
                        if not clicked_cb:
                            row_loc.first.click()
                        user_selected = True
                        human_delay(0.6, 1.2)
                        break
                except Exception:
                    pass

            user_selectors = [
                f'div[role="dialog"] div[role="button"]:has-text("{clean_user}")',
                f'div[role="dialog"] span:has-text("{clean_user}")',
                f'div[role="button"]:has-text("{clean_user}")'
            ]
            for sel in user_selectors:
                try:
                    candidate = page.query_selector(sel)
                    if candidate:
                        cb = candidate.query_selector('input[type="checkbox"], div[role="checkbox"]')
                        target = cb if cb else candidate
                        target.click()
                        user_selected = True
                        human_delay(0.6, 1.2)
                        break
                except Exception:
                    continue

            if user_selected:
                break
            time.sleep(0.3)

        if not user_selected:
            logger.warning(
                f"[Instagram Search] Target user '{clean_user}' was not found in search results. "
                f"Aborting to safely prevent messaging a random suggested ID."
            )
            return False

        # 3. Wait specifically for 'Chat' or 'Next' button to become is_enabled()
        next_button_selectors = [
            'div[role="button"]:has-text("Chat")',
            'button:has-text("Chat")',
            'div[role="button"]:has-text("Next")',
            'button:has-text("Next")',
            'div[aria-label="Next"]'
        ]

        chat_button = None
        for _ in range(15):
            for nsel in next_button_selectors:
                try:
                    btn = page.query_selector(nsel)
                    if btn:
                        enabled = True
                        if hasattr(btn, "is_enabled") and callable(btn.is_enabled):
                            enabled = btn.is_enabled()
                        aria_disabled = btn.get_attribute("aria-disabled") if hasattr(btn, "get_attribute") and callable(btn.get_attribute) else None
                        if enabled and aria_disabled != "true":
                            chat_button = btn
                            break
                except Exception:
                    pass
            if chat_button:
                break
            time.sleep(0.3)

        if chat_button:
            chat_button.click()
            human_delay(1.5, 3.0)
            return True

        # Fallback: check if pressing Enter works or if chat opened
        try:
            if hasattr(page, "keyboard"):
                page.keyboard.press("Enter")
                human_delay(1.5, 2.5)
                return True
        except Exception:
            pass

        return False

    except Exception as e:
        logger.error(f"[Instagram Search Error] Exception during search modal interaction: {e}")
        return False


def send_instagram_dm(username: str, message: str, _page=None) -> Dict[str, Any]:
    """
    Sends a direct message to an Instagram user with safety safeguards:
    - Enforces 90s cooldown per user.
    - Enforces 20 DMs/day ceiling.
    - Types at human speed.
    - Verifies message bubble in DOM before confirming success.
    - Logs to instagram_audit.log.

    Returns:
        Dict[str, Any] with success status, details, and user-facing message.
    """
    clean_user = username.strip().lstrip("@")
    clean_msg = message.strip()

    if not clean_user or not clean_msg:
        return ActionResult({
            "success": False,
            "action": "send_instagram_dm",
            "status": "invalid_input",
            "message": "Username aur message dono pradan karein."
        })

    session = InstagramSession.get_instance()

    # 1. Enforce Session & Per-Recipient Cooldown Safeguard
    rate_limit_secs = globals().get("INSTAGRAM_RATE_LIMIT_SECONDS", INSTAGRAM_RATE_LIMIT_SECONDS)
    time_since_last = time.time() - getattr(session, "last_sent_time", 0.0)
    if getattr(session, "last_sent_time", 0.0) > 0 and time_since_last < rate_limit_secs:
        rem = int(rate_limit_secs - time_since_last)
        log_audit_event(clean_user, clean_msg, "RATE_LIMITED", f"Cooldown active: {rem}s")
        return ActionResult({
            "success": False,
            "action": "send_instagram_dm",
            "status": "rate_limited",
            "seconds_remaining": rem,
            "message": f"Cooldown active. Please wait {rem}s."
        })

    allowed_rate, remaining = check_rate_limit(clean_user, limit_seconds=rate_limit_secs)
    if not allowed_rate:
        log_audit_event(clean_user, clean_msg, "RATE_LIMITED", f"Cooldown active: {remaining}s remaining")
        return ActionResult({
            "success": False,
            "action": "send_instagram_dm",
            "status": "rate_limited",
            "seconds_remaining": remaining,
            "message": f"Cooldown active. Please wait {remaining} seconds before messaging '{clean_user}' again."
        })

    # 2. Enforce Daily Ceiling Safeguard
    allowed_daily, current_count, max_limit = check_daily_cap()
    if not allowed_daily:
        log_audit_event(clean_user, clean_msg, "DAILY_CAP_REACHED", f"Current count: {current_count}/{max_limit}")
        return ActionResult({
            "success": False,
            "action": "send_instagram_dm",
            "status": "daily_cap_reached",
            "count": current_count,
            "limit": max_limit,
            "message": f"Daily Instagram DM limit ({max_limit}) reached. For account security, the message was not sent."
        })

    # 3. Launch Browser Session & Open Chat Thread
    try:
        page = _page
        if page is None:
            page = session.get_page()

        chat_opened = False

        # -----------------------------------------------------------------
        # Route A (Primary): Direct Profile Navigation (https://www.instagram.com/{clean_user}/)
        # -----------------------------------------------------------------
        profile_url = f"https://www.instagram.com/{clean_user}/"
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            human_delay(1.5, 3.0)

            # Check for Login / Security Verification Requirement on profile
            is_challenge, reason = detect_login_or_challenge(page)
            if is_challenge:
                log_audit_event(clean_user, clean_msg, "MANUAL_ACTION_REQUIRED", reason)
                return ActionResult({
                    "success": False,
                    "action": "send_instagram_dm",
                    "status": "manual_action_required",
                    "message": "Manual login or security verification is required on Instagram. Please check the browser window."
                })

            _dismiss_popups(page)

            # Check if profile does not exist (404)
            is_404 = False
            try:
                if page.query_selector('text="Sorry, this page isn\'t available."') or page.query_selector('h2:has-text("Sorry, this page isn\'t available.")'):
                    is_404 = True
            except Exception:
                pass

            if not is_404:
                # Wait for profile hydration and look for Message button
                message_btn_selectors = [
                    'header div[role="button"]:has-text("Message")',
                    'header button:has-text("Message")',
                    'div[role="button"]:has-text("Message")',
                    'button:has-text("Message")',
                    'div[role="button"][tabindex="0"]:has-text("Message")',
                    'a[href*="/direct/t/"]:has-text("Message")',
                    'a:has-text("Message")'
                ]
                try:
                    page.wait_for_selector('div[role="button"]:has-text("Message"), button:has-text("Message")', timeout=5000)
                except Exception:
                    pass

                for m_sel in message_btn_selectors:
                    try:
                        btn = page.query_selector(m_sel)
                        if btn and (not hasattr(btn, "is_visible") or btn.is_visible()):
                            btn.click()
                            chat_opened = True
                            human_delay(2.0, 3.5)
                            break
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"Primary profile navigation failed for '{clean_user}': {e}. Falling back to inbox search.")

        # -----------------------------------------------------------------
        # Route B (Fallback): Inbox Compose Search (https://www.instagram.com/direct/new/)
        # -----------------------------------------------------------------
        if not chat_opened:
            is_challenge, reason = detect_login_or_challenge(page)
            if is_challenge:
                log_audit_event(clean_user, clean_msg, "MANUAL_ACTION_REQUIRED", reason)
                return ActionResult({
                    "success": False,
                    "action": "send_instagram_dm",
                    "status": "manual_action_required",
                    "message": "Manual login or security verification is required on Instagram. Please check the browser window."
                })

            chat_opened = _open_chat_via_search(page, clean_user)
            if not chat_opened:
                log_audit_event(clean_user, clean_msg, "FAILED", f"User '{clean_user}' not found in search results or Chat button not enabled")
                return ActionResult({
                    "success": False,
                    "action": "send_instagram_dm",
                    "status": "failed",
                    "message": f"User '{clean_user}' not found on Instagram."
                })

        # -----------------------------------------------------------------
        # Locate Message Composer
        # -----------------------------------------------------------------
        composer_selectors = [
            'div[role="textbox"][aria-label*="Message"]',
            'div[contenteditable="true"][role="textbox"]',
            'div[role="textbox"][contenteditable="true"]',
            'div[contenteditable="true"]',
            'div[role="textbox"]',
            'div[aria-label*="Message"]',
            'textarea[placeholder*="Message"]'
        ]

        try:
            page.wait_for_selector('div[role="textbox"], div[contenteditable="true"], div[aria-label*="Message"]', timeout=8000)
        except Exception:
            pass

        composer = None
        for csel in composer_selectors:
            try:
                composer = page.query_selector(csel)
                if composer:
                    break
            except Exception:
                continue

        if not composer:
            log_audit_event(clean_user, clean_msg, "FAILED", "Message composer not found in thread")
            return ActionResult({
                "success": False,
                "action": "send_instagram_dm",
                "status": "failed",
                "message": "Instagram chat opened, but message composer could not be found."
            })

        composer.click()
        human_delay(0.5, 1.0)

        # 9. Type Message Character-by-Character
        human_type(composer, clean_msg)
        human_delay(0.6, 1.2)

        # 10. Send Message (Enter key or Send button)
        sent_action = False
        try:
            send_btn = page.query_selector('div[role="button"]:has-text("Send"), button:has-text("Send")')
            if send_btn and send_btn.is_visible():
                send_btn.click()
                sent_action = True
        except Exception:
            pass

        if not sent_action:
            if hasattr(page, "keyboard"):
                page.keyboard.press("Enter")

        # 11. Strict DOM Verification: Verify Message Appears in Chat Thread
        verified = False
        start_verify = time.time()
        snippet_check = clean_msg[:40]

        while time.time() - start_verify < 8.0:
            try:
                text_elem = page.query_selector(f'div[role="row"]:has-text("{snippet_check}"), div[dir="auto"]:has-text("{snippet_check}"), span:has-text("{snippet_check}")')
                if text_elem:
                    verified = True
                    break
            except Exception:
                pass
            time.sleep(0.5)

        if not verified:
            log_audit_event(clean_user, clean_msg, "FAILED", "DOM verification timed out: message bubble not found")
            return ActionResult({
                "success": False,
                "action": "send_instagram_dm",
                "status": "failed",
                "error": "Message bubble did not appear in DOM within timeout.",
                "message": f"Sorry, could not send message to '{clean_user}' on Instagram."
            })

        # 12. Success: Increment Daily Cap, Record Cooldown, and Log Success
        increment_daily_cap()
        record_reply_sent(clean_user)
        session.last_sent_time = time.time()
        log_audit_event(clean_user, clean_msg, "SUCCESS", "Message bubble verified in DOM")

        return ActionResult({
            "success": True,
            "action": "send_instagram_dm",
            "status": "sent",
            "recipient": clean_user,
            "message": f"Message sent to '{clean_user}' on Instagram: '{clean_msg}'"
        })

    except Exception as e:
        log_audit_event(clean_user, clean_msg, "FAILED", f"Exception: {e}")
        return ActionResult({
            "success": False,
            "action": "send_instagram_dm",
            "status": "failed",
            "error": str(e),
            "message": f"Error sending Instagram DM: {e}"
        })

