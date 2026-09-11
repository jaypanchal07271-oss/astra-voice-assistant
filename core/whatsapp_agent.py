"""
================================================================================
⚠️ WARNING: UNOFFICIAL WHATSAPP WEB AUTOMATION MODULE
================================================================================
This module automates WhatsApp Web (https://web.whatsapp.com) using browser DOM
manipulation via Playwright with a persistent browser profile.

CRITICAL NOTICES & ACCOUNT RISK DISCLAIMER:
1. NOT AN OFFICIAL API: This implementation does NOT use the official Meta /
   WhatsApp Business Cloud API. It interacts directly with the WhatsApp Web
   client DOM tree.
2. DOM FRAGILITY: WhatsApp Web interface updates, CSS class modifications, or
   DOM hierarchy restructuring by Meta can break selectors at any time.
3. BAN RISK: Automated messaging, rapid sending, or bot-like patterns carry a
   significant risk of temporary or permanent phone number ban by WhatsApp.

ENFORCED SAFETY SAFEGUARDS:
- Hard Rate Limit: Strictly enforces a maximum of 1 automated reply per chat
  per 60 seconds. Rapid automated bursts are immediately blocked.
- Local Audit Logging: Every automated message send and rate-limit rejection is
  appended to 'whatsapp_audit.log' with timestamps, recipient, and status.
- Single-Device Persistent Profile: Stored locally in '.whatsapp_profile/' so
  QR authentication is performed once.
================================================================================
"""

import os
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from config import (
    WHATSAPP_PROFILE_DIR,
    WHATSAPP_AUDIT_LOG,
    WHATSAPP_RATE_LIMIT_SECONDS,
    WHATSAPP_HEADLESS
)


# =====================================================================
# Rate Limiting & Audit Logging Safeguards
# =====================================================================

_LAST_REPLY_TIMESTAMP: Dict[str, float] = {}
_RATE_LIMIT_LOCK = threading.Lock()


def check_rate_limit(chat_name: str, limit_seconds: int = WHATSAPP_RATE_LIMIT_SECONDS) -> Tuple[bool, float]:
    """
    Enforces hard rate limiting: Max 1 automated message per chat per 60 seconds.
    Returns (is_allowed, seconds_remaining).
    """
    norm_name = chat_name.strip().lower()
    now = time.time()
    with _RATE_LIMIT_LOCK:
        last_time = _LAST_REPLY_TIMESTAMP.get(norm_name, 0.0)
        elapsed = now - last_time
        if elapsed < limit_seconds:
            return False, round(limit_seconds - elapsed, 1)
        return True, 0.0


def record_reply_sent(chat_name: str) -> None:
    """Records the timestamp of an automated reply to enforce the cooldown."""
    norm_name = chat_name.strip().lower()
    with _RATE_LIMIT_LOCK:
        _LAST_REPLY_TIMESTAMP[norm_name] = time.time()


def log_audit_event(chat_name: str, message: str, status: str, details: str = "") -> None:
    """
    Appends an entry to the local audit file (whatsapp_audit.log).
    Ensures complete traceability of all automated interactions.
    """
    try:
        timestamp = datetime.now().isoformat()
        clean_msg = message.replace("\n", " ").strip()[:200]
        entry = (
            f"[{timestamp}] STATUS={status.upper()} | "
            f"CHAT='{chat_name}' | "
            f"MSG_LEN={len(message)} | "
            f"PREVIEW='{clean_msg}'"
        )
        if details:
            entry += f" | DETAILS={details}"
        entry += "\n"

        with open(WHATSAPP_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"[WhatsApp Audit Warning] Failed to append to audit log: {e}")


# =====================================================================
# Persistent Browser Session Management
# =====================================================================

class WhatsAppSession:
    """
    Manages the persistent Playwright Chromium browser session.
    Keeps session active across calls to avoid QR re-scanning.
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._playwright = None
        self._context = None
        self._page = None

    @classmethod
    def get_instance(cls) -> "WhatsAppSession":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def get_page(self):
        """Returns the active WhatsApp Web page, launching the browser if necessary."""
        from playwright.sync_api import sync_playwright

        with self._lock:
            if self._page and not self._page.is_closed():
                return self._page

            if self._playwright is None:
                self._playwright = sync_playwright().start()

            # Ensure profile directory exists
            WHATSAPP_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(WHATSAPP_PROFILE_DIR),
                headless=WHATSAPP_HEADLESS,
                viewport={"width": 1280, "height": 800},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage"
                ]
            )

            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()

            # Navigate to WhatsApp Web if not already loaded
            if "web.whatsapp.com" not in self._page.url:
                self._page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=45000)

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


# =====================================================================
# Core WhatsApp Automation Functions
# =====================================================================

def get_unread_messages(max_chats: int = 10, _page=None) -> List[Dict[str, Any]]:
    """
    Retrieves unread messages from WhatsApp Web chat list.

    Returns:
        List of dicts: [
            {
                "chat_name": str,
                "unread_count": int,
                "last_message": str,
                "timestamp": str,
                "status": "unread" | "qr_required"
            },
            ...
        ]
    """
    try:
        page = _page
        if page is None:
            session = WhatsAppSession.get_instance()
            page = session.get_page()

        # Check if QR code is visible (User needs to scan QR)
        qr_elem = page.query_selector('canvas[aria-label*="Scan"], div[data-ref], [data-testid="qrcode"]')
        if qr_elem:
            return [{
                "chat_name": "SYSTEM",
                "unread_count": 0,
                "last_message": "WhatsApp Web requires QR code scan. Please scan the QR code in the opened browser window.",
                "timestamp": datetime.now().strftime("%I:%M %p"),
                "status": "qr_required"
            }]

        # Wait briefly for chat pane
        try:
            page.wait_for_selector('#pane-side, [data-testid="chat-list"], div[role="grid"]', timeout=8000)
        except Exception:
            pass

        # Locate chat list rows
        chat_rows = page.query_selector_all(
            '#pane-side div[role="listitem"], #pane-side div[tabindex="-1"], div[data-testid="cell-frame-container"]'
        )

        unread_list: List[Dict[str, Any]] = []

        for row in chat_rows:
            if len(unread_list) >= max_chats:
                break

            # Check for unread indicator
            unread_badge = row.query_selector(
                'span[aria-label*="unread"], span[data-icon="unread-count"], [aria-label*="unread message"]'
            )
            if not unread_badge:
                continue

            # Extract unread count
            badge_text = unread_badge.inner_text().strip() if unread_badge else ""
            badge_aria = unread_badge.get_attribute("aria-label") or ""
            count = 1
            if badge_text.isdigit():
                count = int(badge_text)
            elif "unread" in badge_aria:
                import re
                m = re.search(r'(\d+)', badge_aria)
                if m:
                    count = int(m.group(1))

            # Extract Chat Name / Title
            title_elem = row.query_selector('span[title], div[dir="auto"] span[title]')
            chat_name = title_elem.get_attribute("title") if title_elem else ""
            if not chat_name and title_elem:
                chat_name = title_elem.inner_text().strip()
            if not chat_name:
                # Fallback to row text
                name_elem = row.query_selector('div[dir="auto"]')
                chat_name = name_elem.inner_text().strip() if name_elem else "Unknown Contact"

            # Extract last message preview
            preview_elem = row.query_selector(
                'span.selectable-text, span[dir="ltr"], div[dir="ltr"] span, p.selectable-text'
            )
            last_msg = preview_elem.inner_text().strip() if preview_elem else "(No preview)"

            # Extract timestamp
            time_elem = row.query_selector('div[class*="timestamp"], div[dir="auto"]:last-child')
            time_str = time_elem.inner_text().strip() if time_elem else ""

            unread_list.append({
                "chat_name": chat_name,
                "unread_count": count,
                "last_message": last_msg,
                "timestamp": time_str,
                "status": "unread"
            })

        return unread_list

    except Exception as e:
        print(f"[WhatsApp Agent] Error getting unread messages: {e}")
        return [{
            "chat_name": "SYSTEM",
            "unread_count": 0,
            "last_message": f"Error accessing WhatsApp Web: {e}",
            "timestamp": datetime.now().strftime("%I:%M %p"),
            "status": "error"
        }]


# =====================================================================
# Window Management & Multi-Factor Foreground Detection
# =====================================================================

KNOWN_BROWSERS = {
    "chrome.exe", "msedge.exe", "brave.exe", "firefox.exe",
    "opera.exe", "vivaldi.exe", "browser.exe"
}

WHATSAPP_PROCESSES = {
    "whatsapp.exe", "whatsapproot.exe", "applicationframehost.exe"
}


def get_window_info(hwnd: int) -> Dict[str, Any]:
    """Retrieves handle, title, PID, and executable process name for a given Win32 window handle."""
    if not hwnd or os.name != "nt":
        return {"hwnd": 0, "title": "", "process_name": "", "pid": 0}
    title = ""
    proc_name = ""
    pid = 0
    try:
        import win32gui
        title = win32gui.GetWindowText(hwnd).strip()
    except Exception:
        pass

    try:
        import win32process
        import psutil
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid > 0:
            proc_name = psutil.Process(pid).name().lower()
    except Exception:
        pass

    return {
        "hwnd": hwnd,
        "title": title,
        "process_name": proc_name,
        "pid": pid
    }


def get_active_window_info() -> Dict[str, Any]:
    """Retrieves handle, title, PID, and process name of the current active foreground window."""
    if os.name != "nt":
        return {"hwnd": 0, "title": "", "process_name": "", "pid": 0}
    try:
        import win32gui
        fg_hwnd = win32gui.GetForegroundWindow()
        if fg_hwnd:
            return get_window_info(fg_hwnd)
    except Exception:
        pass
    return {"hwnd": 0, "title": "", "process_name": "", "pid": 0}


def is_whatsapp_window(hwnd: int, allow_browser_tab: bool = False) -> bool:
    """
    Evaluates if a window handle corresponds to WhatsApp Desktop or WhatsApp Web.
    Checks:
    1. Window title containing 'whatsapp' (with badge support e.g. '(2) WhatsApp - Google Chrome').
    2. WhatsApp Desktop native process ('whatsapp.exe', etc.).
    3. Active browser instance if allow_browser_tab is True.
    """
    if not hwnd or os.name != "nt":
        return False
    try:
        info = get_window_info(hwnd)
        title_lower = info["title"].lower()
        proc = info["process_name"]

        # Check title containing whatsapp (e.g. WhatsApp, (1) WhatsApp, WhatsApp Web)
        if "whatsapp" in title_lower:
            return True

        # Check native WhatsApp desktop process
        if proc in WHATSAPP_PROCESSES:
            return True

        # Check browser tab if explicitly allowed
        if allow_browser_tab and proc in KNOWN_BROWSERS:
            return True

    except Exception:
        pass
    return False


def is_whatsapp_foreground(allow_browser_tab: bool = False) -> bool:
    """Returns True if the current active foreground window is WhatsApp Desktop or WhatsApp Web."""
    if os.name != "nt":
        return False
    try:
        import win32gui
        fg_hwnd = win32gui.GetForegroundWindow()
        if fg_hwnd:
            return is_whatsapp_window(fg_hwnd, allow_browser_tab=allow_browser_tab)
    except Exception:
        pass
    return False


def bring_window_to_foreground(hwnd: int) -> bool:
    """
    Safely and reliably brings a Win32 window to the foreground.
    Attaches thread input to bypass Windows focus restrictions and restores minimized windows.
    """
    if os.name != "nt" or not hwnd:
        return False
    try:
        import win32gui
        import win32con
        import win32process
        import win32api
        import ctypes

        if not win32gui.IsWindow(hwnd):
            return False

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        fg_hwnd = win32gui.GetForegroundWindow()
        if fg_hwnd == hwnd:
            return True

        cur_tid = win32api.GetCurrentThreadId()
        fg_tid = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0

        attached = False
        if fg_tid != 0 and fg_tid != cur_tid:
            try:
                ctypes.windll.user32.AttachThreadInput(cur_tid, fg_tid, True)
                attached = True
            except Exception:
                pass

        try:
            # Bypass Windows SetForegroundWindow lock by simulating an Alt key event
            user32 = ctypes.windll.user32
            user32.keybd_event(0x12, 0, 0, 0)  # Alt down
            user32.keybd_event(0x12, 0, 2, 0)  # Alt up

            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetActiveWindow(hwnd)
        finally:
            if attached:
                try:
                    ctypes.windll.user32.AttachThreadInput(cur_tid, fg_tid, False)
                except Exception:
                    pass

        time.sleep(0.15)
        return is_whatsapp_window(win32gui.GetForegroundWindow())
    except Exception as e:
        print(f"[WhatsApp Agent] Could not bring hwnd {hwnd} to foreground: {e}")
        try:
            import win32gui
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False


def find_whatsapp_hwnd() -> Optional[int]:
    """Enumerates visible top-level windows to locate WhatsApp Desktop or WhatsApp Web browser windows."""
    if os.name != "nt":
        return None
    try:
        import win32gui
        hwnds = []

        def enum_cb(h, _):
            if win32gui.IsWindowVisible(h):
                if is_whatsapp_window(h):
                    hwnds.append(h)
            return True

        win32gui.EnumWindows(enum_cb, None)
        return hwnds[0] if hwnds else None
    except Exception:
        return None


def focus_whatsapp_window(timeout_seconds: float = 5.0) -> bool:
    """
    Finds and brings WhatsApp Web (in Chrome/Edge/Brave/Firefox) or WhatsApp Desktop to the foreground.
    If not already open or in the active tab, uses tab search or launches web.whatsapp.com.
    Returns True if WhatsApp is verified as the active foreground window, False otherwise.
    """
    if os.name != "nt":
        return False
    try:
        import win32gui

        # 0. If already foreground, return immediately
        if is_whatsapp_foreground():
            return True

        # 1. If WhatsApp window already exists, bring it to the foreground
        target_hwnd = find_whatsapp_hwnd()
        if target_hwnd:
            bring_window_to_foreground(target_hwnd)
            time.sleep(0.4)
            if is_whatsapp_foreground():
                return True

        # 2. If Chrome/Edge/Brave is the active window, try Search Tabs shortcut (Ctrl+Shift+A) to switch to WhatsApp tab instantly
        try:
            fg_info = get_active_window_info()
            fg_t = fg_info["title"].lower()
            fg_proc = fg_info["process_name"]
            if fg_proc in KNOWN_BROWSERS or any(b in fg_t for b in ["chrome", "edge", "brave", "firefox"]):
                import pyautogui
                import pyperclip
                pyautogui.hotkey('ctrl', 'shift', 'a')
                time.sleep(0.3)
                pyperclip.copy("WhatsApp")
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(0.4)
                pyautogui.press('enter')
                time.sleep(0.6)
                if is_whatsapp_foreground():
                    return True
        except Exception:
            pass

        # 3. If WhatsApp is in an inactive browser tab or not open, launch web.whatsapp.com
        from core import actions
        actions._launch_browser_url("https://web.whatsapp.com")

        # 4. Poll adaptively until WhatsApp becomes the active foreground window
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            time.sleep(0.25)
            if is_whatsapp_foreground():
                return True
            target_hwnd = find_whatsapp_hwnd()
            if target_hwnd:
                bring_window_to_foreground(target_hwnd)
                time.sleep(0.25)
                if is_whatsapp_foreground():
                    return True

        return is_whatsapp_foreground()
    except Exception as e:
        print(f"[WhatsApp Agent] Note: Could not focus WhatsApp window: {e}")
        return False


def send_whatsapp_message_ui(contact_name: str, message: str, _page=None) -> Dict[str, Any]:
    """
    Executes Python UI Automation for sending a WhatsApp message:
    1. Click 'New Chat' (if search popup is not already active)
    2. Type the Name into 'Search name, number or @username' input box
    3. Wait/Sleep (Crucial: 2s) for WhatsApp to filter contacts
    4. Press 'Enter' to select top contact and open direct chat window
    5. Type the Message into the main chat box
    6. Press 'Enter' to Send

    Supports both Playwright browser sessions and active desktop browser UI (PyAutoGUI).
    """
    clean_name = contact_name.strip()
    clean_msg = message.strip()

    if not clean_name:
        return {
            "success": False,
            "action": "send_whatsapp_message",
            "error": "Recipient contact name cannot be empty.",
            "message": "Contact name is required."
        }

    if not clean_msg:
        return {
            "success": False,
            "action": "send_whatsapp_message",
            "error": "Message content cannot be empty.",
            "message": f"What message would you like to send to {clean_name}?"
        }

    # 1. Enforce Hard 60-Second Rate Limit
    allowed, wait_sec = check_rate_limit(clean_name)
    if not allowed:
        log_audit_event(
            chat_name=clean_name,
            message=clean_msg,
            status="RATE_LIMITED",
            details=f"Cooldown active. Blocked send attempt. {wait_sec}s remaining."
        )
        return {
            "success": False,
            "action": "send_whatsapp_message",
            "rate_limited": True,
            "retry_after": wait_sec,
            "error": f"Rate limit exceeded: Please wait {wait_sec}s before sending another automated reply to '{clean_name}'.",
            "message": f"Rate limit: '{clean_name}' ko agala message {wait_sec}s baad hi bheja ja sakta hai."
        }

    # 2. Automation Execution
    # If explicit Playwright page provided (e.g. unit tests or active Playwright session)
    if _page is not None or (WhatsAppSession._instance and WhatsAppSession._instance._page and not WhatsAppSession._instance._page.is_closed()):
        try:
            page = _page if _page is not None else WhatsAppSession.get_instance().get_page()

            # Check if QR code is visible
            qr_elem = page.query_selector('canvas[aria-label*="Scan"], div[data-ref], [data-testid="qrcode"]')
            if qr_elem:
                log_audit_event(clean_name, clean_msg, status="FAILED", details="QR code scan required.")
                return {
                    "success": False,
                    "action": "send_whatsapp_message",
                    "status": "qr_required",
                    "message": "WhatsApp Web login required. Please scan the QR code in the browser window first."
                }

            # Step 1: Click "New Chat" if search dialog is not already open
            search_box = page.query_selector(
                'div[role="dialog"] div[contenteditable="true"], div[role="dialog"] input, div[role="textbox"][aria-label*="Search name"], input[placeholder*="Search name"], input[placeholder*="Search"]'
            )
            if not search_box:
                new_chat_btn = page.query_selector(
                    'button[aria-label*="New chat" i], span[data-icon="new-chat-outline"], span[data-icon="chat"], div[title*="New chat" i], div[aria-label*="New chat" i]'
                )
                if new_chat_btn:
                    new_chat_btn.click()
                    time.sleep(0.8)
                    search_box = page.query_selector(
                        'div[role="dialog"] div[contenteditable="true"], div[role="dialog"] input, div[role="textbox"][aria-label*="Search name"], input[placeholder*="Search name"], input[placeholder*="Search"], div[contenteditable="true"][data-tab="3"], div[role="textbox"]'
                    )

            # Step 2: Type the Name
            if search_box:
                search_box.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                search_box.fill(clean_name)
            else:
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.type(clean_name)

            # Step 3: Wait/Sleep (Crucial: 2 seconds for WhatsApp Web to filter contacts)
            time.sleep(2.0)

            # Step 4: Press 'Enter' to select top contact from search results
            chat_item = page.query_selector(
                f'div[role="dialog"] span[title*="{clean_name}" i], div[role="listitem"] span[title*="{clean_name}" i]'
            )
            if chat_item:
                chat_item.click()
            else:
                page.keyboard.press("Enter")

            time.sleep(1.0)

            # Step 5: Type the Message into main chat box
            msg_input = page.query_selector(
                'footer div[contenteditable="true"], div[contenteditable="true"][data-tab="10"], div[role="textbox"][spellcheck="true"], footer div[role="textbox"]'
            )
            if msg_input:
                msg_input.click()
                msg_input.fill(clean_msg)
            else:
                page.keyboard.type(clean_msg)

            time.sleep(0.5)

            # Step 6: Press 'Enter' to Send
            page.keyboard.press("Enter")

            record_reply_sent(clean_name)
            log_audit_event(clean_name, clean_msg, status="SUCCESS", details="Delivered via Playwright UI automation.")
            return {
                "success": True,
                "action": "send_whatsapp_message",
                "contact_name": clean_name,
                "message": f"WhatsApp par '{clean_name}' ko message bhej diya gaya hai: '{clean_msg}'."
            }
        except Exception as e:
            log_audit_event(clean_name, clean_msg, status="FAILED", details=str(e))
            return {
                "success": False,
                "action": "send_whatsapp_message",
                "error": str(e),
                "message": f"WhatsApp message bhejne me samasya aayi: {e}"
            }

    # Desktop / PyAutoGUI Execution (for user's active Chrome / WhatsApp window)
    try:
        import pyautogui
        import pyperclip

        # 1. Bring WhatsApp window to foreground or activate tab
        print(f"[WhatsApp Agent] Step 0: Focusing WhatsApp window for contact '{clean_name}'...")
        window_found = focus_whatsapp_window()
        time.sleep(0.5)

        # If WhatsApp is not focused/foregrounded, open WhatsApp Web safely without blind keystrokes
        if not is_whatsapp_foreground():
            from core import actions
            actions._launch_browser_url("https://web.whatsapp.com")
            active_info = get_active_window_info()
            log_audit_event(clean_name, clean_msg, status="FAILED", details=f"Active window ({active_info.get('title')}) does not contain 'WhatsApp'. Aborted keystrokes.")
            return {
                "success": False,
                "action": "send_whatsapp_message",
                "status": "not_foreground",
                "error": f"Active window '{active_info.get('title')}' does not contain 'WhatsApp' before opening new chat. Aborted keystrokes to prevent typing into unintended window.",
                "message": f"WhatsApp window active nahi thi. Main WhatsApp Web open kar raha hoon, kripya '{clean_name}' ki chat me message bhejein."
            }

        # STRICT GUARD: Verify active foreground window contains 'WhatsApp' with adaptive retry
        def _ensure_whatsapp_foreground(step_desc: str, retries: int = 5, delay: float = 0.2):
            for _ in range(retries):
                if is_whatsapp_foreground():
                    return
                time.sleep(delay)
            if not is_whatsapp_foreground():
                info = get_active_window_info()
                raise RuntimeError(
                    f"Active window '{info.get('title')}' (proc={info.get('process_name')}) does not contain 'WhatsApp' before {step_desc}. "
                    "Aborted keystrokes to prevent typing into unintended window."
                )

        _ensure_whatsapp_foreground("opening new chat")

        # 2. Open New Chat Dialog / Focus Search
        print(f"[WhatsApp Agent] Step 1: Opening New Chat...")
        active_info = get_active_window_info()
        fg_title = active_info.get("title", "").lower()
        fg_proc = active_info.get("process_name", "").lower()

        if fg_proc in KNOWN_BROWSERS or any(b in fg_title for b in ["chrome", "edge", "brave", "firefox", "browser", "web"]):
            pyautogui.hotkey('ctrl', 'alt', 'n')
        else:
            pyautogui.hotkey('ctrl', 'n')

        time.sleep(0.6)

        # Clear any existing text in search box
        _ensure_whatsapp_foreground("clearing search box")
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.1)
        pyautogui.press('backspace')
        time.sleep(0.1)

        # 3. Inject contact name into search input box
        print(f"[WhatsApp Agent] Step 2: Typing contact name '{clean_name}'...")
        _ensure_whatsapp_foreground("typing contact name")
        pyperclip.copy(clean_name)
        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'v')

        # 4. Wait/Sleep (Crucial: 2.0s for WhatsApp Web search filtering)
        print(f"[WhatsApp Agent] Step 3: Waiting 2.0s for WhatsApp search filtering...")
        time.sleep(2.0)

        # 5. Press 'Enter' to open contact's direct chat window
        print(f"[WhatsApp Agent] Step 4: Selecting contact from search results...")
        _ensure_whatsapp_foreground("selecting contact")
        pyautogui.press('enter')

        # 6. Wait for chat window to load and become active
        print(f"[WhatsApp Agent] Step 5: Waiting 1.2s for chat pane...")
        time.sleep(1.2)

        # 7. Type the Message into chat box
        print(f"[WhatsApp Agent] Step 6: Typing message content...")
        _ensure_whatsapp_foreground("typing message")
        pyperclip.copy(clean_msg)
        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.5)

        # 8. Press 'Enter' to Send
        print(f"[WhatsApp Agent] Step 7: Sending message...")
        _ensure_whatsapp_foreground("sending message")
        pyautogui.press('enter')

        record_reply_sent(clean_name)
        log_audit_event(clean_name, clean_msg, status="SUCCESS", details="Delivered via Desktop UI automation.")
        print(f"[WhatsApp Agent] Success: Message sent to '{clean_name}'.")
        return {
            "success": True,
            "action": "send_whatsapp_message",
            "contact_name": clean_name,
            "message": f"WhatsApp par '{clean_name}' ko message bhej diya gaya hai: '{clean_msg}'."
        }
    except Exception as e:
        log_audit_event(clean_name, clean_msg, status="FAILED", details=str(e))
        print(f"[WhatsApp Agent] Failed to send message to '{clean_name}': {e}")
        return {
            "success": False,
            "action": "send_whatsapp_message",
            "error": str(e),
            "message": f"WhatsApp UI automation error: {e}"
        }


def send_reply(chat_name: str, message: str, _page=None) -> Dict[str, Any]:
    """
    Sends an automated reply to a specified WhatsApp chat name or contact.
    Delegates to send_whatsapp_message_ui with rate limiting and audit logging.
    """
    return send_whatsapp_message_ui(contact_name=chat_name, message=message, _page=_page)


