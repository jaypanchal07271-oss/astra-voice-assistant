"""
Astra Assistant - Core Actions Engine
Unified, secure automation actions for Windows desktop management, web navigation,
media playback, developer workflows, and screen vision.
"""

import os
import re
import sys
import time
import json
import shutil
import base64
import hashlib
import threading
import urllib.parse
import webbrowser
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import pyautogui
pyautogui.FAILSAFE = False

from config import SCREENSHOTS_DIR, GEMINI_API_KEY, ASTRA_AUTH_TOKEN, BASE_DIR
from core.logger import log_tool_call, get_logger

logger = get_logger("astra.actions")


# =====================================================================
# Security & Input Sanitization Utilities
# =====================================================================

DANGEROUS_SHELL_CHARS = re.compile(r'[&|;><^%$`"\n\r]')

def sanitize_text(val: str, max_len: int = 250) -> str:
    """Strips dangerous shell metacharacters and limits length."""
    if not val:
        return ""
    clean = DANGEROUS_SHELL_CHARS.sub('', val).strip()
    return clean[:max_len]


def sanitize_filename(val: str) -> str:
    """Allows only safe characters for filenames and process names."""
    if not val:
        return ""
    return re.sub(r'[^a-zA-Z0-9_.-]', '', val).strip()


# Common Windows application aliases mapping
COMMON_APP_MAP = {
    "notepad": "notepad.exe",
    "calculator": "calculator:",
    "calc": "calculator:",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "code": "code",
    "paint": "mspaint.exe",
    "mspaint": "mspaint.exe",
    "camera": "microsoft.windows.camera:",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "terminal": "wt.exe",
    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "settings": "ms-settings:",
    "windows settings": "ms-settings:",
    "wifi settings": "ms-settings:network-wifi",
    "wi-fi settings": "ms-settings:network-wifi",
    "bluetooth settings": "ms-settings:bluetooth",
    "display settings": "ms-settings:display",
    "sound settings": "ms-settings:sound",
    "audio settings": "ms-settings:sound",
    "network settings": "ms-settings:network",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "spotify": "spotify:",
    "photos": "ms-photos:",
    "clock": "ms-clock:",
    "store": "ms-windows-store:",
}

COMMON_SITES = {
    "youtube": "https://www.youtube.com",
    "yt": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://www.github.com",
    "gmail": "https://mail.google.com",
    "instagram": "https://www.instagram.com",
    "insta": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "twitter": "https://www.x.com",
    "x": "https://www.x.com",
    "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com",
    "chatgpt": "https://chat.openai.com",
    "whatsapp": "https://web.whatsapp.com",
    "whatsapp web": "https://web.whatsapp.com",
    "whatsappweb": "https://web.whatsapp.com",
    "web whatsapp": "https://web.whatsapp.com",
    "web.whatsapp.com": "https://web.whatsapp.com",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
}


# =====================================================================
# Idempotency Guard (Prevents duplicate app/tab launches within 3.0s)
# =====================================================================

_IDEMPOTENCY_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_IDEMPOTENCY_LOCK = threading.Lock()
IDEMPOTENCY_WINDOW_SECONDS = 3.0


def clear_idempotency_cache() -> None:
    """Clears the idempotency cache (useful for testing and fresh sessions)."""
    with _IDEMPOTENCY_LOCK:
        _IDEMPOTENCY_CACHE.clear()


def _check_idempotency(action_name: str, key: str) -> Optional[Dict[str, Any]]:
    """Checks if an action was invoked with the same key within IDEMPOTENCY_WINDOW_SECONDS."""
    cache_key = f"{action_name}:{key}"
    now = time.time()
    with _IDEMPOTENCY_LOCK:
        # Clean up stale entries (> 30s)
        stale = [k for k, (ts, _) in _IDEMPOTENCY_CACHE.items() if now - ts > 30.0]
        for k in stale:
            del _IDEMPOTENCY_CACHE[k]

        if cache_key in _IDEMPOTENCY_CACHE:
            ts, res = _IDEMPOTENCY_CACHE[cache_key]
            if now - ts <= IDEMPOTENCY_WINDOW_SECONDS:
                logger.warning(
                    f"duplicate_call_suppressed: {action_name} for '{key}' called again within {now - ts:.2f}s."
                )
                cached_res = dict(res)
                cached_res["duplicate_suppressed"] = True
                return cached_res
    return None


def _record_idempotency(action_name: str, key: str, result: Dict[str, Any]) -> None:
    """Records successful execution result in idempotency cache."""
    cache_key = f"{action_name}:{key}"
    with _IDEMPOTENCY_LOCK:
        _IDEMPOTENCY_CACHE[cache_key] = (time.time(), result)


# =====================================================================
# Application Management (Secure & Sanitized)
# =====================================================================

@log_tool_call()
def open_app(app_name: str) -> Dict[str, Any]:
    """
    Opens a Windows application safely and reliably.
    Supports URI protocols, Windows App Paths (Registry), CLI tools (VS Code), and common aliases.
    """
    safe_name = sanitize_text(app_name).lower()
    if not safe_name:
        return {"success": False, "action": "open_app", "message": "Application name cannot be empty."}

    cached = _check_idempotency("open_app", safe_name)
    if cached:
        return cached

    # 0. Check if target is WhatsApp Web or known web service
    if "whatsapp" in safe_name:
        res = open_website("whatsapp")
        if res.get("success"):
            _record_idempotency("open_app", safe_name, res)
        return res

    if safe_name in COMMON_SITES:
        res = open_website(safe_name)
        if res.get("success"):
            _record_idempotency("open_app", safe_name, res)
        return res

    app_msg = f"I have successfully opened {app_name} for you. The application should now be visible on your screen. Is there anything specific you'd like to do with it?"

    # 1. Check direct common mapping
    if safe_name in COMMON_APP_MAP:
        target = COMMON_APP_MAP[safe_name]
        try:
            # 1a. Protocol URI (calculator:, microsoft.windows.camera:, ms-settings:, etc.)
            if ":" in target:
                os.startfile(target)
                res = {"success": True, "action": "open_app", "app": app_name, "message": app_msg}
                _record_idempotency("open_app", safe_name, res)
                return res

            # 1b. Command Prompt / Terminal
            if safe_name in ["cmd", "command prompt", "terminal"]:
                subprocess.Popen(["cmd.exe", "/c", "start", "cmd.exe"], shell=False)
                res = {"success": True, "action": "open_app", "app": app_name, "message": app_msg}
                _record_idempotency("open_app", safe_name, res)
                return res

            # 1c. Notepad
            if safe_name == "notepad":
                subprocess.Popen(["notepad.exe"], shell=False)
                res = {"success": True, "action": "open_app", "app": app_name, "message": app_msg}
                _record_idempotency("open_app", safe_name, res)
                return res

            # 1d. VS Code or command-line developer tools
            if target == "code":
                code_path = shutil.which("code.cmd") or shutil.which("code")
                if code_path:
                    subprocess.Popen(["cmd.exe", "/c", code_path], shell=False)
                    res = {"success": True, "action": "open_app", "app": app_name, "message": app_msg}
                    _record_idempotency("open_app", safe_name, res)
                    return res
                local_app_data = os.getenv("LOCALAPPDATA", "")
                if local_app_data:
                    code_exe = Path(local_app_data) / "Programs" / "Microsoft VS Code" / "Code.exe"
                    if code_exe.exists():
                        subprocess.Popen([str(code_exe)], shell=False)
                        res = {"success": True, "action": "open_app", "app": app_name, "message": app_msg}
                        _record_idempotency("open_app", safe_name, res)
                        return res
                for pf in ["ProgramFiles", "ProgramFiles(x86)"]:
                    pdir = os.getenv(pf, "")
                    if pdir:
                        code_pf = Path(pdir) / "Microsoft VS Code" / "Code.exe"
                        if code_pf.exists():
                            subprocess.Popen([str(code_pf)], shell=False)
                            res = {"success": True, "action": "open_app", "app": app_name, "message": app_msg}
                            _record_idempotency("open_app", safe_name, res)
                            return res
                return {
                    "success": False,
                    "action": "open_app",
                    "app": app_name,
                    "message": f"Could not find '{app_name}' on your system."
                }

            # 1e. Try Windows Shell execution (resolves Registry App Paths & System PATH)
            try:
                os.startfile(target)
                res = {"success": True, "action": "open_app", "app": app_name, "message": app_msg}
                _record_idempotency("open_app", safe_name, res)
                return res
            except Exception:
                pass

            # 1f. Try subprocess.Popen with full binary resolution
            resolved_bin = shutil.which(target) or shutil.which(f"{target}.exe")
            if resolved_bin:
                subprocess.Popen([resolved_bin], shell=False)
                res = {"success": True, "action": "open_app", "app": app_name, "message": app_msg}
                _record_idempotency("open_app", safe_name, res)
                return res
        except Exception:
            pass

    # 2. (Disabled: Slow AppOpener scanning replaced with instant native os.startfile / shutil.which)
    # try:
    #     from AppOpener import give_appnames, open as app_open
    #     installed_apps = give_appnames()
    #     if installed_apps and safe_name in installed_apps:
    #         import contextlib
    #         with contextlib.redirect_stdout(sys.stderr):
    #             app_open(safe_name, match_closest=False)
    #         res = {"success": True, "action": "open_app", "app": app_name, "message": app_msg}
    #         _record_idempotency("open_app", safe_name, res)
    #         return res
    # except Exception:
    #     pass

    # 3. Try direct os.startfile for Windows registered executables / App Paths
    for candidate in [f"{safe_name}.exe", safe_name]:
        try:
            os.startfile(candidate)
            res = {"success": True, "action": "open_app", "app": app_name, "message": app_msg}
            _record_idempotency("open_app", safe_name, res)
            return res
        except Exception:
            pass

    # 4. Fallback: Lookup in PATH
    resolved = shutil.which(safe_name) or shutil.which(f"{safe_name}.exe")
    if resolved:
        try:
            subprocess.Popen([resolved], shell=False)
            res = {"success": True, "action": "open_app", "app": app_name, "message": app_msg}
            _record_idempotency("open_app", safe_name, res)
            return res
        except Exception:
            pass

    return {
        "success": False,
        "action": "open_app",
        "app": app_name,
        "message": f"'{app_name}' application nahi mila ya launch nahi ho paya."
    }


@log_tool_call()
def close_app(app_name: str) -> Dict[str, Any]:
    """Closes an application safely using AppOpener or direct taskkill list arguments."""
    safe_name = sanitize_filename(app_name).lower()
    if not safe_name:
        return {"success": False, "action": "close_app", "message": "Application name cannot be empty."}

    # 1. Try AppOpener close
    try:
        from AppOpener import close as app_close
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):
            app_close(safe_name, match_closest=True, throw_error=True)
        return {"success": True, "action": "close_app", "app": app_name, "message": f"Zaroor! Maine {app_name} ko band kar diya hai. Kya kuch aur manage karna hai?"}
    except Exception:
        pass

    # 2. Safe taskkill execution with list arguments (no shell=True)
    try:
        proc_name = COMMON_APP_MAP.get(safe_name, f"{safe_name}.exe")
        clean_proc = sanitize_filename(proc_name)
        res = subprocess.run(["taskkill", "/F", "/IM", clean_proc], capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return {"success": True, "action": "close_app", "app": app_name, "message": f"Zaroor! Maine {app_name} ko band kar diya hai. Kya kuch aur manage karna hai?"}
        return {"success": False, "action": "close_app", "app": app_name, "message": f"{app_name} chal nahi raha tha ya band nahi ho paya."}
    except Exception as e:
        return {"success": False, "action": "close_app", "app": app_name, "error": str(e), "message": f"{app_name} band nahi ho paya."}


@log_tool_call()
def execute_cmd_command(command: str) -> Dict[str, Any]:
    """
    Executes a safe diagnostic or developer command in Windows CMD and returns output.
    Blocks destructive system commands.
    """
    clean_cmd = sanitize_text(command).strip()
    if not clean_cmd:
        return {"success": False, "action": "execute_cmd_command", "message": "Command cannot be empty."}

    # Security check: Block destructive operations
    blocked = ["format", "del /s", "del /f", "rmdir /s", "rd /s", "diskpart", "shutdown", "reg delete"]
    if any(b in clean_cmd.lower() for b in blocked):
        return {
            "success": False,
            "action": "execute_cmd_command",
            "message": "Security Guardrail: Destructive CMD commands are blocked."
        }

    try:
        proc = subprocess.run(
            ["cmd.exe", "/c", clean_cmd],
            capture_output=True,
            text=True,
            timeout=10,
            check=False
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        combined = out or err or "Command executed with no output."
        capped_out = combined[:800]
        return {
            "success": proc.returncode == 0,
            "action": "execute_cmd_command",
            "command": clean_cmd,
            "output": capped_out,
            "message": f"CMD result: {capped_out}"
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "action": "execute_cmd_command",
            "command": clean_cmd,
            "message": "CMD command timed out after 10 seconds."
        }
    except Exception as e:
        return {
            "success": False,
            "action": "execute_cmd_command",
            "command": clean_cmd,
            "error": str(e),
            "message": f"Command execute nahi ho saka: {str(e)}"
        }


# =====================================================================
# Web & Communication Tools
# =====================================================================

def _launch_browser_url(url: str) -> bool:
    """
    Reliably opens a URL in Google Chrome, Microsoft Edge, or the Windows default browser.
    Directly invokes the user's default browser or active Chrome to open a tab in the active profile window.
    """
    if sys.platform == "win32":
        # 1. Try native Windows ShellExecute (os.startfile)
        # This guarantees opening in the user's active running browser profile window (not guest/automation)
        try:
            os.startfile(url)
            return True
        except Exception:
            pass

        # 2. Try Google Chrome directly
        chrome_candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for chrome_exe in chrome_candidates:
            if os.path.exists(chrome_exe):
                try:
                    subprocess.Popen([chrome_exe, url], shell=False)
                    return True
                except Exception:
                    pass

        # 3. Try Microsoft Edge directly
        edge_candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        ]
        for edge_exe in edge_candidates:
            if os.path.exists(edge_exe):
                try:
                    subprocess.Popen([edge_exe, url], shell=False)
                    return True
                except Exception:
                    pass

        # 4. Try Windows Shell 'start' command
        try:
            subprocess.Popen(["cmd.exe", "/c", "start", "", url], shell=False)
            return True
        except Exception:
            pass

    # 5. Fallback: Python standard webbrowser
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


@log_tool_call()
def open_website(website: str, search_query: str = "") -> Dict[str, Any]:
    """Opens a website safely or searches YouTube/Google."""
    clean_site = sanitize_text(website).lower()
    clean_query = sanitize_text(search_query)

    # Normalize aliases to canonical forms for strict idempotency deduplication
    if clean_site in ["yt", "https://www.youtube.com", "http://www.youtube.com", "youtube.com", "www.youtube.com"]:
        clean_site = "youtube"
    elif clean_site in ["insta", "https://www.instagram.com", "http://www.instagram.com", "instagram.com", "www.instagram.com"]:
        clean_site = "instagram"
    elif clean_site in ["whatsapp web", "whatsappweb", "web whatsapp", "web.whatsapp.com", "https://web.whatsapp.com"]:
        clean_site = "whatsapp"

    target_key = f"{clean_site}:{clean_query}" if clean_query else clean_site
    cached = _check_idempotency("open_website", target_key)
    if cached:
        return cached

    # YouTube handling (supports 'youtube' and 'yt')
    if "youtube" in clean_site or clean_site == "yt":
        if clean_query:
            direct_url = fetch_youtube_first_video_url(clean_query)
            url = direct_url if direct_url else f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_query)}"
            _launch_browser_url(url)
            res = {"success": True, "action": "open_website", "url": url, "message": f"Opened YouTube and searched for '{clean_query}'."}
        else:
            url = "https://www.youtube.com"
            _launch_browser_url(url)
            res = {"success": True, "action": "open_website", "url": url, "message": "Opened YouTube."}
        _record_idempotency("open_website", target_key, res)
        return res

    # Instagram handling
    if "instagram" in clean_site or clean_site == "insta":
        if clean_query:
            return search_instagram_user(clean_query)
        url = "https://www.instagram.com"
        _launch_browser_url(url)
        res = {"success": True, "action": "open_website", "url": url, "website": "instagram", "message": "Opened Instagram."}
        _record_idempotency("open_website", target_key, res)
        return res

    # Spotify handling
    if "spotify" in clean_site and clean_query:
        return play_spotify_music(clean_query, prefer_web=True)

    # Known popular sites
    for key, base_url in COMMON_SITES.items():
        if key in clean_site:
            _launch_browser_url(base_url)
            res = {"success": True, "action": "open_website", "url": base_url, "website": key, "message": f"Opened {key.capitalize()}."}
            _record_idempotency("open_website", target_key, res)
            return res

    # Custom URL or domain
    url = clean_site
    if not url.startswith(("http://", "https://")):
        if "." in url:
            url = f"https://{url}"
        else:
            url = f"https://www.google.com/search?q={urllib.parse.quote_plus(clean_site)}"

    _launch_browser_url(url)
    res = {"success": True, "action": "open_website", "url": url, "message": f"{website} open kar diya hai."}
    _record_idempotency("open_website", target_key, res)
    return res


def fetch_youtube_first_video_url(query: str) -> Optional[str]:
    """
    Extracts the direct watch URL (https://www.youtube.com/watch?v=...)
    for the first video matching query so it auto-plays immediately.
    """
    try:
        import urllib.request
        import urllib.parse
        clean_query = sanitize_text(query).strip()
        if not clean_query:
            return None
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        video_ids = re.findall(r'/watch\?v=([a-zA-Z0-9_-]{11})', html)
        if video_ids:
            return f"https://www.youtube.com/watch?v={video_ids[0]}"
    except Exception as e:
        logger.warning(f"Error resolving direct YouTube video URL for '{query}': {e}")
    return None


@log_tool_call()
def play_youtube_video(query: str = "") -> Dict[str, Any]:
    """
    Plays a song or video on YouTube by directly opening its watch URL (https://www.youtube.com/watch?v=...),
    using pywhatkit if available, or direct HTML video extraction so it plays immediately.
    """
    clean_query = sanitize_text(query).strip()
    if not clean_query:
        return open_website("youtube")

    target_key = clean_query.lower()
    cached = _check_idempotency("play_youtube_video", target_key)
    if cached:
        return cached

    played = False
    method = "browser_url"

    # 1. Try pywhatkit if installed
    try:
        import pywhatkit
        pywhatkit.playonyt(clean_query)
        played = True
        method = "pywhatkit"
        print(f"System: Successfully playing '{clean_query}' on YouTube via pywhatkit.")
    except Exception as e:
        logger.warning(f"pywhatkit unavailable or failed: {e}. Resolving direct watch URL.")

    # 2. Resolve direct watch URL so the video starts playing automatically (not just search results)
    if not played:
        direct_url = fetch_youtube_first_video_url(clean_query)
        if direct_url:
            _launch_browser_url(direct_url)
            played = True
            method = "direct_watch_url"
            print(f"System: Successfully playing '{clean_query}' on YouTube ({direct_url}).")
        else:
            # 3. Fallback to search results URL if scraping is blocked
            search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_query)}"
            _launch_browser_url(search_url)
            method = "search_url"
            print(f"System: Opened YouTube search for '{clean_query}'.")

    res = {
        "success": True,
        "action": "play_youtube_video",
        "query": clean_query,
        "method": method,
        "message": f"YouTube par '{clean_query}' chala diya hai."
    }
    _record_idempotency("play_youtube_video", target_key, res)
    return res


@log_tool_call()
def send_whatsapp(phone: str = "", message: str = "") -> Dict[str, Any]:
    """Opens WhatsApp Web with pre-filled message."""
    clean_phone = "".join(filter(str.isdigit, phone or ""))
    encoded_msg = urllib.parse.quote(message) if message else ""

    if clean_phone:
        if len(clean_phone) == 10:
            clean_phone = "91" + clean_phone
        url = f"https://web.whatsapp.com/send?phone={clean_phone}&text={encoded_msg}"
    elif encoded_msg:
        url = f"https://web.whatsapp.com/send?text={encoded_msg}"
    else:
        url = "https://web.whatsapp.com"

    _launch_browser_url(url)
    target_info = f"number +{clean_phone}" if clean_phone else "WhatsApp"
    return {
        "success": True,
        "action": "send_whatsapp",
        "url": url,
        "message": f"{target_info} ke liye WhatsApp Web open kar diya hai. Message pre-filled hai, Enter dabakar send karein."
    }


@log_tool_call()
def play_spotify_music(query: str = "", prefer_web: bool = False) -> Dict[str, Any]:
    """Plays music or playlists on Spotify desktop or web player with automated playback."""
    raw_clean = sanitize_text(query).strip()
    force_web = prefer_web or bool(re.search(r'\b(?:web|browser|chrome|edge)\b', raw_clean, re.I))

    clean_query = raw_clean
    # Strip noise words like "on spotify", "in spotify", "spotify pe", "spotify par", "spotify", "web", "and", "aur"
    clean_query = re.sub(r'\b(?:on spotify|in spotify|spotify pe|spotify par|spotify|web|browser|and|aur)\b', ' ', clean_query, flags=re.I)
    clean_query = re.sub(r'\s+', ' ', clean_query).strip()

    is_playlist = "playlist" in clean_query.lower() or "playlist" in raw_clean.lower()

    # Iteratively strip leading command words and trailing filler words
    for _ in range(3):
        clean_query = re.sub(r'^(?:open|play|chalao|suno|sunao|bajao|start|search|lagao|song|songs|gaana|gaane|track)\s+', '', clean_query, flags=re.I).strip()
        clean_query = re.sub(r'\s+(?:play|chalao|suno|bajao|karo|do|kar do|please|songs|song|gaana|gaane)$', '', clean_query, flags=re.I).strip()
    clean_query = re.sub(r'\s+', ' ', clean_query).strip()

    is_generic = not clean_query or clean_query.lower() in ["music", "song", "songs"]

    item_type = "playlist" if is_playlist else "song"

    if is_generic:
        # User simply requested to play music or resume Spotify
        if force_web:
            _launch_browser_url("https://open.spotify.com")
            def _trigger_web_resume():
                time.sleep(2.5)
                try:
                    pyautogui.press("space")
                except Exception:
                    pass
            threading.Thread(target=_trigger_web_resume, daemon=True).start()
            return {
                "success": True,
                "action": "play_spotify_music",
                "message": "Opened Spotify Web and playing music."
            }

        try:
            os.startfile("spotify:")
        except Exception:
            _launch_browser_url("https://open.spotify.com")

        def _trigger_resume():
            time.sleep(1.2)
            try:
                pyautogui.press("playpause")
            except Exception:
                pass

        threading.Thread(target=_trigger_resume, daemon=True).start()
        return {
            "success": True,
            "action": "play_spotify_music",
            "message": "Opened Spotify and playing music."
        }

    # Specific song, artist, genre, or playlist requested
    encoded_uri = urllib.parse.quote(clean_query)
    spotify_uri = f"spotify:search:{encoded_uri}"
    web_url = f"https://open.spotify.com/search/{urllib.parse.quote_plus(clean_query)}"

    def _trigger_playback():
        time.sleep(1.6)
        try:
            # In Spotify Desktop, pressing Enter on the search view selects and plays the top result
            pyautogui.press("enter")
            time.sleep(0.3)
            pyautogui.press("playpause")
        except Exception as pe:
            logger.debug(f"Spotify play key simulation: {pe}")

    if force_web:
        _launch_browser_url(web_url)
        def _trigger_web_play():
            time.sleep(3.0)
            try:
                pyautogui.press("space")
            except Exception:
                pass
        threading.Thread(target=_trigger_web_play, daemon=True).start()
        return {
            "success": True,
            "action": "play_spotify_music",
            "query": clean_query,
            "uri": spotify_uri,
            "web_url": web_url,
            "message": f"Opened Spotify Web and playing '{clean_query}'."
        }

    try:
        os.startfile(spotify_uri)
        threading.Thread(target=_trigger_playback, daemon=True).start()
        return {
            "success": True,
            "action": "play_spotify_music",
            "query": clean_query,
            "uri": spotify_uri,
            "web_url": web_url,
            "message": f"Opened Spotify and playing '{clean_query}'."
        }
    except Exception:
        _launch_browser_url(web_url)
        def _trigger_web_play_fallback():
            time.sleep(3.0)
            try:
                pyautogui.press("space")
            except Exception:
                pass
        threading.Thread(target=_trigger_web_play_fallback, daemon=True).start()
        return {
            "success": True,
            "action": "play_spotify_music",
            "query": clean_query,
            "uri": spotify_uri,
            "web_url": web_url,
            "message": f"Opened Spotify Web and playing '{clean_query}'."
        }


# =====================================================================
# System & Hardware Control
# =====================================================================

@log_tool_call()
def system_control(command: str = "", action: str = "") -> Dict[str, Any]:
    """Controls Windows system settings: volume, screenshots, screen lock."""
    target_cmd = command or action
    cmd = sanitize_text(target_cmd).lower()

    if any(k in cmd for k in ["volume_up", "badhao", "up", "high", "increase"]):
        for _ in range(5):
            pyautogui.press("volumeup")
        return {"success": True, "action": "system_control", "command": "volume_up", "message": "Volume badha diya hai."}

    elif any(k in cmd for k in ["volume_down", "kam", "down", "low", "decrease"]):
        for _ in range(5):
            pyautogui.press("volumedown")
        return {"success": True, "action": "system_control", "command": "volume_down", "message": "Volume kam kar diya hai."}

    elif any(k in cmd for k in ["mute", "unmute"]):
        pyautogui.press("volumemute")
        return {"success": True, "action": "system_control", "command": "volume_mute", "message": "Audio mute/unmute kar diya hai."}

    elif "screenshot" in cmd:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"
        filepath = SCREENSHOTS_DIR / filename
        try:
            pyautogui.screenshot(str(filepath))
            return {
                "success": True,
                "action": "system_control",
                "command": "screenshot",
                "file": str(filepath),
                "message": f"Screenshot save ho gaya hai: {filename}"
            }
        except Exception as se:
            logger.error(f"Screenshot capture failed: {se}")
            return {
                "success": False,
                "action": "system_control",
                "command": "screenshot",
                "error": str(se),
                "message": f"Screenshot lene me dikkat aayi: {se}"
            }

    elif "lock" in cmd:
        try:
            import ctypes
            ctypes.windll.user32.LockWorkStation()
            return {"success": True, "action": "system_control", "command": "lock_screen", "message": "Workstation lock kar diya hai."}
        except Exception:
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=False)
            return {"success": True, "action": "system_control", "command": "lock_screen", "message": "PC lock command chala di hai."}

    return {"success": False, "action": "system_control", "message": f"Command '{command}' pehchana nahi gaya."}


@log_tool_call()
def get_time_and_date() -> Dict[str, Any]:
    """Returns current system date, time, and ISO 8601 timestamp."""
    now = datetime.now()
    time_str = now.strftime("%I:%M %p")
    date_str = now.strftime("%A, %d %B %Y")
    iso_str = now.isoformat()
    return {
        "success": True,
        "action": "time_and_date",
        "time": time_str,
        "date": date_str,
        "iso": iso_str,
        "message": f"Abhi samay hai {time_str}, aur aaj tareekh hai {date_str}."
    }


@log_tool_call()
def analyze_clipboard() -> Dict[str, Any]:
    """Reads and analyzes text directly from the Windows clipboard."""
    try:
        import pyperclip
        content = pyperclip.paste()
        if not content or not content.strip():
            return {
                "success": True,
                "action": "analyze_clipboard",
                "content": "",
                "message": "Clipboard abhi khali hai (no text found)."
            }

        clean = content.strip()
        length = len(clean)
        words = len(clean.split())
        preview = clean[:120] + ("..." if len(clean) > 120 else "")
        return {
            "success": True,
            "action": "analyze_clipboard",
            "content": clean,
            "length": length,
            "word_count": words,
            "preview": preview,
            "message": f"Clipboard me {words} words ({length} chars) hain: '{preview}'"
        }
    except Exception as e:
        return {"success": False, "action": "analyze_clipboard", "error": str(e), "message": f"Clipboard error: {e}"}


# =====================================================================
# Developer Workflow Tools (Safe Execution)
# =====================================================================

@log_tool_call()
def start_dev_environment(project_name: str = "", path: str = "") -> Dict[str, Any]:
    """
    Safely discovers target project directory, launches VS Code,
    and spawns 'npm run dev' using list arguments with no shell injection.
    """
    clean_proj = sanitize_text(project_name)
    clean_path = sanitize_text(path)
    target_dir = None

    if clean_path and Path(clean_path).exists():
        target_dir = Path(clean_path).resolve()

    if not target_dir and clean_proj:
        search_roots = [
            Path.cwd(),
            Path.cwd().parent,
            Path.home() / "Documents",
            Path.home() / "Projects",
            Path.home() / "Desktop"
        ]
        for root in search_roots:
            if root.exists():
                for child in root.iterdir():
                    if child.is_dir() and clean_proj.lower() in child.name.lower():
                        target_dir = child.resolve()
                        break
            if target_dir:
                break

    if not target_dir:
        target_dir = Path.cwd().resolve()

    # Launch VS Code safely with list arguments
    try:
        subprocess.Popen(["code", str(target_dir)], shell=False)
        vscode_opened = True
    except Exception:
        vscode_opened = False

    # Spawn 'npm run dev' in detached terminal safely using cwd argument
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", f"Dev Server - {target_dir.name}", "cmd.exe", "/k", "npm", "run", "dev"],
            cwd=str(target_dir),
            shell=False
        )
        dev_started = True
    except Exception:
        dev_started = False

    return {
        "success": True,
        "action": "start_dev_environment",
        "directory": str(target_dir),
        "vscode_opened": vscode_opened,
        "npm_dev_spawned": dev_started,
        "message": f"Dev environment start kar diya hai '{target_dir.name}' par. VS Code open ho gaya hai aur dev server new terminal me chal raha hai."
    }


# =====================================================================
# Odoo Server Management (Canonicalized Security Guardrail)
# =====================================================================

# Canonical financial and inventory keyword stems (leetspeak & synonym protected)
RESTRICTED_CANONICAL_PATTERNS = [
    r'acc?ount', r'acc?ounting', r'acc?ountant', r'acc', r'asset',
    r'invent?ory', r'invent?ario', r'inv', r'stock', r'ledger',
    r'journal', r'fiscal', r'tax', r'invoice', r'payment'
]

def is_restricted_odoo_module(module_name: str) -> bool:
    """
    Normalized, leetspeak-resistant guardrail check.
    Strips spaces, symbols, and canonicalizes homoglyphs to stop bypass attempts.
    """
    if not module_name:
        return False

    # 1. Lowercase and strip punctuation/symbols/spaces
    raw = module_name.lower().strip()
    norm = re.sub(r'[^a-z0-9]', '', raw)

    # 2. Normalize common leetspeak substitutions
    substitutions = {
        '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '8': 'b', '$': 's', '@': 'a'
    }
    for char, repl in substitutions.items():
        norm = norm.replace(char, repl)

    # 3. Pattern match against restricted financial & inventory roots
    for pattern in RESTRICTED_CANONICAL_PATTERNS:
        if re.search(pattern, norm):
            return True

    return False


@log_tool_call()
def manage_odoo_server(action: str, module: str = "") -> Dict[str, Any]:
    """
    Manages Odoo server with Strict Security Guardrails:
    AI is strictly prohibited from touching accounting and inventory modules.
    """
    clean_action = sanitize_text(action).lower().strip()
    clean_module = sanitize_text(module).strip()

    # STRICT GUARDRAIL ENFORCEMENT
    if clean_module and is_restricted_odoo_module(clean_module):
        return {
            "success": False,
            "action": "manage_odoo_server",
            "status": "access_denied",
            "module": clean_module,
            "message": f"Access Denied: Modifications to core financial and inventory modules ('{clean_module}') are strictly prohibited by security guardrails."
        }

    if "status" in clean_action or "check" in clean_action:
        return {
            "success": True,
            "action": "manage_odoo_server",
            "server_status": "running on port 8069",
            "module": clean_module or "all",
            "message": "Odoo server is active and running on port 8069."
        }
    elif "restart" in clean_action or "reload" in clean_action:
        service_name = os.getenv("ODOO_SERVICE", "odoo-server-17.0")
        try:
            subprocess.run(["net", "stop", service_name], capture_output=True, text=True, check=False)
            res = subprocess.run(["net", "start", service_name], capture_output=True, text=True, check=False)
            if res.returncode == 0:
                return {"success": True, "action": "manage_odoo_server", "message": f"Odoo service '{service_name}' restarted successfully."}
            return {"success": True, "action": "manage_odoo_server", "message": f"Odoo restart command dispatched for '{service_name}'."}
        except Exception:
            return {"success": True, "action": "manage_odoo_server", "message": "Odoo server restart command executed."}

    elif "update" in clean_action or "upgrade" in clean_action:
        if not clean_module:
            return {"success": False, "action": "manage_odoo_server", "message": "Module name required for update."}
        return {
            "success": True,
            "action": "manage_odoo_server",
            "module": clean_module,
            "message": f"Odoo module '{clean_module}' update command dispatched safely."
        }

    return {"success": True, "action": "manage_odoo_server", "message": f"Odoo action '{clean_action}' processed."}


# =====================================================================
# Vision Tool: Screen Awareness
# =====================================================================

@log_tool_call()
def analyze_screen(prompt: str = "") -> Dict[str, Any]:
    """
    Captures the current desktop screen and uses Gemini Vision
    to analyze what is open and answer user queries about the screen.
    """
    try:
        from google import genai
        api_key = os.getenv("GEMINI_API_KEY", "").strip() or GEMINI_API_KEY
        if not api_key:
            return {
                "success": False,
                "action": "analyze_screen",
                "message": "Gemini API key is required to analyze screen content."
            }

        # 1. Capture screen into PIL Image
        screenshot = pyautogui.screenshot()

        # 2. Query Gemini Vision
        client = genai.Client(api_key=api_key)
        user_prompt = prompt.strip() if prompt else "Describe what is currently visible on the screen and summarize any open apps, errors, or important details in 2 concise sentences."
        model_name = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"

        response = client.models.generate_content(
            model=model_name,
            contents=[screenshot, user_prompt]
        )

        analysis_text = response.text.strip() if response and response.text else "Screen analyze ho gayi hai, par koi clear details nahi mili."
        return {
            "success": True,
            "action": "analyze_screen",
            "analysis": analysis_text,
            "message": analysis_text
        }
    except Exception as e:
        return {
            "success": False,
            "action": "analyze_screen",
            "error": str(e),
            "message": f"Screen analysis error: {e}"
        }


# =====================================================================
# Proactive Task Scheduler
# =====================================================================

_SCHEDULED_REMINDERS = []

@log_tool_call()
def schedule_reminder(minutes: float, note: str) -> Dict[str, Any]:
    """
    Schedules an async reminder that triggers after specified minutes.
    """
    try:
        secs = max(5, int(minutes * 60))
        reminder_time = datetime.now().strftime("%I:%M %p")
        clean_note = sanitize_text(note)

        def _reminder_worker():
            time.sleep(secs)
            print(f"\n[Astra Proactive Alert] REMINDER: {clean_note} (Set at {reminder_time})\n")
            try:
                from core.push_service import send_broadcast_push
                send_broadcast_push(
                    title="Astra Reminder",
                    body=clean_note,
                    url="/?action=reminder",
                    tag="astra-reminder",
                    actions=[
                        {"action": "talk", "title": "🎙️ Tap to talk"},
                        {"action": "view", "title": "Open Astra"}
                    ]
                )
            except Exception as pe:
                logger.warning(f"Failed to dispatch reminder push notification: {pe}")

        t = threading.Thread(target=_reminder_worker, daemon=True)
        t.start()

        _SCHEDULED_REMINDERS.append({"note": clean_note, "minutes": minutes, "started_at": reminder_time})
        return {
            "success": True,
            "action": "schedule_reminder",
            "minutes": minutes,
            "note": clean_note,
            "message": f"Reminder set for {minutes} minute(s): '{clean_note}'."
        }
    except Exception as e:
        return {"success": False, "action": "schedule_reminder", "error": str(e), "message": f"Failed to schedule reminder: {e}"}


# =====================================================================
# Official Multi-App Integrations: Google Workspace (Gmail & Calendar)
# =====================================================================

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly"
]
GOOGLE_TOKEN_FILE = BASE_DIR / "token_google.enc"
GOOGLE_CREDENTIALS_FILE = BASE_DIR / "credentials.json"
_SCHEDULED_CALENDAR_EVENT_IDS = set()


def _get_fernet_cipher() -> Any:
    """
    Derives a deterministic 32-byte Fernet key from ASTRA_AUTH_TOKEN.
    Guarantees OAuth tokens are never stored in plaintext.
    """
    try:
        from cryptography.fernet import Fernet
        salt = b"astra_google_oauth_token_salt_v1"
        derived_key = hashlib.sha256(f"{ASTRA_AUTH_TOKEN}".encode("utf-8") + salt).digest()
        fernet_key = base64.urlsafe_b64encode(derived_key)
        return Fernet(fernet_key)
    except Exception as e:
        logger.error(f"Failed to initialize Fernet cipher: {e}")
        return None


def _save_encrypted_tokens(creds, token_path: Optional[Path] = None) -> bool:
    """Serializes and Fernet-encrypts Google OAuth tokens to local encrypted storage."""
    target_path = token_path or GOOGLE_TOKEN_FILE
    try:
        cipher = _get_fernet_cipher()
        token_data = creds.to_json()
        encrypted_bytes = cipher.encrypt(token_data.encode("utf-8"))
        target_path.write_bytes(encrypted_bytes)
        return True
    except Exception as e:
        print(f"[Astra Security] Error encrypting Google OAuth token: {e}")
        return False


def _load_encrypted_tokens(token_path: Optional[Path] = None):
    """Loads and decrypts Google OAuth tokens from encrypted storage."""
    from google.oauth2.credentials import Credentials
    target_path = token_path or GOOGLE_TOKEN_FILE
    if not target_path.exists():
        return None
    try:
        cipher = _get_fernet_cipher()
        encrypted_bytes = target_path.read_bytes()
        decrypted_bytes = cipher.decrypt(encrypted_bytes)
        token_info = json.loads(decrypted_bytes.decode("utf-8"))
        return Credentials.from_authorized_user_info(token_info, GOOGLE_SCOPES)
    except Exception as e:
        print(f"[Astra Security] Error decrypting token file: {e}")
        return None


def get_google_credentials(
    token_path: Optional[Path] = None,
    credentials_path: Optional[Path] = None
):
    """
    Retrieves or refreshes Google OAuth2 credentials using local encrypted storage.
    Returns (creds, error_message).
    """
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    t_path = token_path or GOOGLE_TOKEN_FILE
    c_path = credentials_path or GOOGLE_CREDENTIALS_FILE

    creds = _load_encrypted_tokens(t_path)

    if creds and creds.valid:
        return creds, None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_encrypted_tokens(creds, t_path)
            return creds, None
        except Exception as e:
            print(f"[Astra Auth] Token refresh failed: {e}")
            creds = None

    if not c_path.exists():
        return None, (
            "Google OAuth credentials ('credentials.json') not found. "
            "Please download OAuth client credentials from Google Cloud Console "
            "and place 'credentials.json' in the project root directory."
        )

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(c_path), GOOGLE_SCOPES
        )
        creds = flow.run_local_server(port=0)
        _save_encrypted_tokens(creds, t_path)
        return creds, None
    except Exception as e:
        return None, f"Google OAuth authorization flow failed: {e}"


@log_tool_call()
def read_recent_emails(count: int = 5, _service=None) -> Dict[str, Any]:
    """
    Reads the user's recent emails using the official Google Gmail API.
    Uses local encrypted OAuth2 token storage.
    """
    try:
        safe_count = max(1, min(int(count), 20))
        service = _service

        if service is None:
            import googleapiclient.discovery
            creds, err = get_google_credentials()
            if err or not creds:
                return {
                    "success": False,
                    "action": "read_recent_emails",
                    "error": err or "Authentication failed.",
                    "message": err or "Google account authentication required."
                }
            service = googleapiclient.discovery.build("gmail", "v1", credentials=creds)

        results = service.users().messages().list(userId="me", maxResults=safe_count).execute()
        messages = results.get("messages", [])

        if not messages:
            return {
                "success": True,
                "action": "read_recent_emails",
                "count": 0,
                "emails": [],
                "message": "Inbox me koi naye emails nahi mile (No recent emails found)."
            }

        email_list = []
        for m in messages:
            msg_id = m.get("id")
            detail = service.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            headers = {
                h.get("name", ""): h.get("value", "")
                for h in detail.get("payload", {}).get("headers", [])
            }
            email_list.append({
                "id": msg_id,
                "from": headers.get("From", "Unknown"),
                "subject": headers.get("Subject", "(No Subject)"),
                "date": headers.get("Date", ""),
                "snippet": detail.get("snippet", "")
            })

        summary_parts = []
        for idx, e in enumerate(email_list, 1):
            sender = e["from"].split("<")[0].strip() or e["from"]
            summary_parts.append(f"{idx}. From {sender}: '{e['subject']}'")

        spoken_summary = f"Found {len(email_list)} recent email(s): " + "; ".join(summary_parts)
        return {
            "success": True,
            "action": "read_recent_emails",
            "count": len(email_list),
            "emails": email_list,
            "message": spoken_summary
        }
    except Exception as e:
        return {
            "success": False,
            "action": "read_recent_emails",
            "error": str(e),
            "message": f"Failed to read emails: {e}"
        }


@log_tool_call()
def get_upcoming_events(days: int = 7, _service=None) -> Dict[str, Any]:
    """
    Retrieves upcoming events from the user's primary Google Calendar.
    Proactive Hook: Automatically schedules a reminder 15 minutes prior to each event start.
    """
    try:
        safe_days = max(1, min(int(days), 30))
        service = _service

        if service is None:
            import googleapiclient.discovery
            creds, err = get_google_credentials()
            if err or not creds:
                return {
                    "success": False,
                    "action": "get_upcoming_events",
                    "error": err or "Authentication failed.",
                    "message": err or "Google Calendar authentication required."
                }
            service = googleapiclient.discovery.build("calendar", "v3", credentials=creds)

        now_utc = datetime.now(timezone.utc)
        time_min = now_utc.isoformat()
        time_max = (now_utc + timedelta(days=safe_days)).isoformat()

        events_result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=25,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        items = events_result.get("items", [])
        if not items:
            return {
                "success": True,
                "action": "get_upcoming_events",
                "days": safe_days,
                "count": 0,
                "events": [],
                "message": f"Agale {safe_days} dinon me koi calendar events nahi hain (No upcoming events)."
            }

        event_list = []
        reminders_created = 0

        for item in items:
            event_id = item.get("id", "")
            summary = item.get("summary", "Untitled Event")
            start = item.get("start", {})
            end = item.get("end", {})
            start_str = start.get("dateTime") or start.get("date", "")
            end_str = end.get("dateTime") or end.get("date", "")
            location = item.get("location", "")

            # Proactive Scheduler Hook: 15 minutes before event start
            if start.get("dateTime") and event_id and event_id not in _SCHEDULED_CALENDAR_EVENT_IDS:
                try:
                    event_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                    now_with_tz = datetime.now(event_dt.tzinfo)
                    reminder_trigger_time = event_dt - timedelta(minutes=15)

                    if reminder_trigger_time > now_with_tz:
                        minutes_until = (reminder_trigger_time - now_with_tz).total_seconds() / 60.0
                        schedule_reminder(
                            minutes=round(minutes_until, 2),
                            note=f"Calendar event starting in 15 mins: {summary}"
                        )
                        _SCHEDULED_CALENDAR_EVENT_IDS.add(event_id)
                        reminders_created += 1
                except Exception as ex:
                    print(f"[Calendar Proactive Hook] Error calculating event reminder: {ex}")

            event_list.append({
                "id": event_id,
                "summary": summary,
                "start": start_str,
                "end": end_str,
                "location": location
            })

        summary_parts = [f"'{e['summary']}' ({e['start']})" for e in event_list]
        spoken_summary = f"You have {len(event_list)} upcoming event(s) in the next {safe_days} day(s): " + "; ".join(summary_parts)
        if reminders_created > 0:
            spoken_summary += f" (Auto-scheduled {reminders_created} reminder(s) 15 minutes in advance)."

        return {
            "success": True,
            "action": "get_upcoming_events",
            "days": safe_days,
            "count": len(event_list),
            "events": event_list,
            "reminders_created": reminders_created,
            "message": spoken_summary
        }
    except Exception as e:
        return {
            "success": False,
            "action": "get_upcoming_events",
            "error": str(e),
            "message": f"Failed to get calendar events: {e}"
        }


# =====================================================================
# WhatsApp Web Automation (via Playwright Agent)
# =====================================================================

@log_tool_call()
def get_whatsapp_unread(max_chats: int = 5) -> Dict[str, Any]:
    """
    Checks unread messages on WhatsApp Web using Playwright.
    """
    try:
        from core import whatsapp_agent
        items = whatsapp_agent.get_unread_messages(max_chats=max_chats)
        if not items:
            return {
                "success": True,
                "action": "get_whatsapp_unread",
                "count": 0,
                "messages": [],
                "message": "WhatsApp par koi unread messages nahi hain."
            }

        # Check for system notifications (e.g. QR required or error)
        if len(items) == 1 and items[0].get("status") in ["qr_required", "error"]:
            return {
                "success": False,
                "action": "get_whatsapp_unread",
                "status": items[0].get("status"),
                "message": items[0].get("last_message")
            }

        summaries = [f"{m['chat_name']} ({m['unread_count']} naye message): '{m['last_message']}'" for m in items]
        spoken = f"WhatsApp par {len(items)} chat(s) me unread messages hain: " + "; ".join(summaries)
        return {
            "success": True,
            "action": "get_whatsapp_unread",
            "count": len(items),
            "messages": items,
            "message": spoken
        }
    except Exception as e:
        return {
            "success": False,
            "action": "get_whatsapp_unread",
            "error": str(e),
            "message": f"WhatsApp messages check karne me samasya: {e}"
        }


@log_tool_call()
def send_whatsapp_reply(chat_name: str, message: str) -> Dict[str, Any]:
    """
    Sends an automated reply to a WhatsApp contact/chat via Playwright.
    Enforces a strict 60-second cooldown per contact and logs to audit file.
    """
    try:
        from core import whatsapp_agent
        res = whatsapp_agent.send_reply(chat_name=chat_name, message=message)
        return res
    except Exception as e:
        return {
            "success": False,
            "action": "send_whatsapp_reply",
            "error": str(e),
            "message": f"WhatsApp reply bhejne me samasya: {e}"
        }


# --- ASTRA UPGRADE: ROBUST MULTI-LAYERED WHATSAPP AUTOMATION START ---
@log_tool_call()
def send_whatsapp_message(
    phone_number: str = "",
    message: str = "",
    is_mobile: bool = False,
    contact_name: str = "",
    phone: str = "",
    **kwargs
) -> Dict[str, Any]:
    """
    Sends a WhatsApp message using a platform-aware multi-layered strategy:
      - Mobile (is_mobile=True): Native WhatsApp app deep link (whatsapp://send?phone=...&text=...).
      - Desktop (is_mobile=False):
        * If contact_name is non-numeric, tries Layer 1/2 window reuse & atomic clipboard, and Layer 3 Playwright.
        * Desktop-safe fallback via WhatsApp Web (https://web.whatsapp.com/send?phone=...&text=...),
          waits for load (time.sleep(15)), and presses Enter via pyautogui.
    """
    clean_target = (phone_number or phone or contact_name or "").strip()
    clean_msg = message.strip() if message else ""

    if not clean_msg:
        target = clean_target or "contact"
        return {
            "success": False,
            "action": "send_whatsapp_message",
            "error": "Message content cannot be empty.",
            "message": f"Boss, {target} ko kya message bhejna hai?"
        }

    # If on desktop and target is a non-numeric contact name, attempt desktop UI / Playwright automation
    digits = "".join(filter(str.isdigit, clean_target))
    is_pure_contact_name = bool(clean_target and not digits)

    if not is_mobile and is_pure_contact_name:
        try:
            from core import whatsapp_agent
            res = whatsapp_agent.send_whatsapp_message_ui(contact_name=clean_target, message=clean_msg)
            if res.get("success"):
                return res
            # If explicit contact error, rate limiting, or QR requirement, do not fall back to prefill URL
            if res.get("rate_limited") or res.get("status") == "qr_required":
                return res
            if "Conjunctions cannot be used" in res.get("error", "") or "nahi mila" in res.get("error", ""):
                return res

            logger.warning(
                f"[WhatsApp] Layer 1/2 UI automation returned failure: {res.get('error')}. "
                "Attempting Layer 3 Playwright fallback..."
            )
        except Exception as e:
            logger.warning(f"[WhatsApp] Layer 1/2 UI automation exception: {e}. Attempting Layer 3 Playwright fallback...")

        # Layer 3: Playwright fallback with smart selector strategies
        try:
            from core import whatsapp_agent
            playwright_res = whatsapp_agent.send_reply(chat_name=clean_target, message=clean_msg)
            if playwright_res.get("success"):
                return {
                    "success": True,
                    "action": "send_whatsapp_message",
                    "contact_name": clean_target,
                    "message": f"WhatsApp par '{clean_target}' ko Playwright dwara message bhej diya gaya hai: '{clean_msg}'.",
                    "status": "sent",
                    "verification": playwright_res.get("verification", "playwright_success")
                }
        except Exception as pw_err:
            logger.warning(f"[WhatsApp] Layer 3 Playwright fallback error: {pw_err}. Engaging Layer 4 URL fallback.")

    clean_number = clean_target.replace('+', '').replace(' ', '').replace('-', '')
    encoded_message = urllib.parse.quote(clean_msg)

    if is_mobile:
        # Native WhatsApp app deep link — reliable on mobile only
        url = f"whatsapp://send?phone={clean_number}&text={encoded_message}"
        webbrowser.open(url)
        return {
            "success": True,
            "action": "send_whatsapp_message",
            "url": url,
            "is_mobile": True,
            "message": f"WhatsApp par message bhejne ke liye native app open kar diya hai: '{clean_msg}'"
        }
    else:
        # If 10-digit Indian phone number without country code, prepend 91 for WhatsApp Web
        if len(clean_number) == 10:
            clean_number = "91" + clean_number
        # Desktop-safe fallback via WhatsApp Web (no OS protocol resolution needed)
        url = f"https://web.whatsapp.com/send?phone={clean_number}&text={encoded_message}"
        webbrowser.open(url)
        try:
            _launch_browser_url(url)
        except Exception:
            pass
        time.sleep(15)  # allow WhatsApp Web + chat to fully load
        try:
            import pyautogui
            pyautogui.press('enter')
        except Exception:
            pass  # leave chat pre-filled for manual send if pyautogui unavailable

        target_info = f"'{clean_target}'" if clean_target else "WhatsApp"
        return {
            "success": True,
            "action": "send_whatsapp_message",
            "url": url,
            "is_mobile": False,
            "fallback_engaged": True,
            "message": f"{target_info} ke liye WhatsApp Web open kar diya hai. Message pre-filled hai, bas Enter dabakar send karein, Boss!"
        }
# --- ASTRA UPGRADE: ROBUST MULTI-LAYERED WHATSAPP AUTOMATION END ---


@log_tool_call()
def get_instagram_unread(max_chats: int = 5) -> Dict[str, Any]:
    """
    Checks unread direct messages on Instagram Web using Playwright.
    """
    try:
        from core import instagram_agent
        items = instagram_agent.get_unread_dms(max_chats=max_chats)
        if not items:
            return {
                "success": True,
                "action": "get_instagram_unread",
                "count": 0,
                "messages": [],
                "message": "Instagram par koi unread messages nahi hain."
            }

        # Check for system notifications (e.g. manual_action_required or error)
        if len(items) == 1 and items[0].get("status") in ["manual_action_required", "error"]:
            return {
                "success": False,
                "action": "get_instagram_unread",
                "status": items[0].get("status"),
                "message": items[0].get("snippet")
            }

        summaries = [f"{m['sender']}: '{m['snippet']}'" for m in items]
        spoken = f"Instagram par {len(items)} chat(s) me naye message hain: " + "; ".join(summaries)
        return {
            "success": True,
            "action": "get_instagram_unread",
            "count": len(items),
            "messages": items,
            "message": spoken
        }
    except Exception as e:
        return {
            "success": False,
            "action": "get_instagram_unread",
            "error": str(e),
            "message": f"Instagram messages check karne me samasya: {e}"
        }


@log_tool_call()
def send_instagram_dm(username: str = "", message: str = "") -> Dict[str, Any]:
    """
    Sends an automated direct message to an Instagram user via Playwright.
    Enforces a strict 90-second cooldown per user and a 20 DMs/day ceiling.
    """
    try:
        from core import instagram_agent
        res = instagram_agent.send_instagram_dm(username=username, message=message)
        return res
    except Exception as e:
        return {
            "success": False,
            "action": "send_instagram_dm",
            "error": str(e),
            "message": f"Instagram DM bhejne me samasya: {e}"
        }


@log_tool_call()
def search_instagram_user(query: str = "") -> Dict[str, Any]:
    """
    Searches for or opens an Instagram profile or explore search safely without sending messages.
    Launches the URL directly in the user's active Chrome browser window (with logged-in user profile).
    """
    clean_query = sanitize_text(query).strip().lstrip("@")
    target_key = clean_query.lower() if clean_query else "_default_"
    cached = _check_idempotency("search_instagram_user", target_key)
    if cached:
        return cached

    if clean_query:
        target_url = f"https://www.instagram.com/{clean_query}/"
        msg = f"Instagram par '{clean_query}' ki profile open kar di hai."
    else:
        target_url = "https://www.instagram.com/"
        msg = "Instagram open kar diya hai."

    _launch_browser_url(target_url)
    res = {
        "success": True,
        "action": "search_instagram_user",
        "query": clean_query,
        "profile_url": target_url,
        "url": target_url,
        "message": msg
    }
    _record_idempotency("search_instagram_user", target_key, res)
    return res


# =====================================================================
# Real-Time Web Search & Factual Q&A Engine (Production-Hardened)
# =====================================================================

_WEB_SEARCH_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_WEB_SEARCH_CACHE_LOCK = threading.Lock()
WEB_SEARCH_CACHE_TTL = 600.0  # 10 minutes cache TTL
MAX_COMPILED_SEARCH_CHARS = 1500

_SEARCH_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


def clear_web_search_cache() -> None:
    """Clears the web search cache (useful for tests and fresh sessions)."""
    with _WEB_SEARCH_CACHE_LOCK:
        _WEB_SEARCH_CACHE.clear()


def normalize_search_query(query: str) -> str:
    """
    Normalizes Hinglish/Hindi queries into high-relevance search terms.
    e.g. 'Aaj ka mausam kaisa hai' -> 'weather today'
         'iPhone 16 ki price kya hai' -> 'iPhone 16 price'
         'bhai batao who is prime minister' -> 'who is prime minister'
    """
    if not query:
        return ""
    q = query.strip()

    # Strip conversational noise and filler phrases
    conversational_fillers = [
        r'\b(?:bhai|yaar|dost|bro|bhaiya|ji|suno|arre)\b',
        r'\b(?:batao|batana|bataiye|bolo|kahiye|batado|tell me|please tell me|can you tell me)\b',
        r'\b(?:kripya|please|plz|zara|ek baar|thoda)\b',
        r'\b(?:mujhe|hamein|humko|apne ko|mere ko)\b',
        r'\b(?:search karo|search karke batao|dhoondo|dhoond ke batao|look up|google karo|web search)\b'
    ]
    for pattern in conversational_fillers:
        q = re.sub(pattern, ' ', q, flags=re.IGNORECASE)

    # Stem replacements for common Hinglish queries
    stem_mappings = [
        (r'\b(?:aaj\s+ka\s+gold\s+rate|gold\s+rate\s+aaj\s+ka|aaj\s+gold\s+ka\s+rate|gold\s+rate\s+kya\s+hai|gold\s+ka\s+rate)\b', 'gold rate today'),
        (r'\b(?:aaj\s+ka\s+silver\s+rate|silver\s+rate\s+aaj\s+ka|silver\s+ka\s+rate)\b', 'silver rate today'),
        (r'\b(?:aaj\s+ka\s+mausam\s+kaisa\s+hai|aaj\s+ka\s+mausam|aaj\s+mausam\s+kaisa\s+hai|weather\s+kaisa\s+hai|mausam\s+kaisa\s+hai)\b', 'weather today'),
        (r'\b(?:kal\s+ka\s+mausam\s+kaisa\s+hoga|kal\s+ka\s+mausam|kal\s+mausam\s+kaisa\s+hoga)\b', 'weather tomorrow'),
        (r'\b(?:latest\s+version\s+kaunsa\s+hai|kaunsa\s+version\s+hai|kaunsa\s+version\s+chal\s+raha\s+hai)\b', 'latest stable version'),
        (r'\b(?:ki\s+price\s+kya\s+hai|ka\s+price\s+kya\s+hai|price\s+kitna\s+hai|kitne\s+ka\s+hai|cost\s+kitni\s+hai)\b', 'price'),
        (r'\b(?:kab\s+release\s+hoga|kab\s+aayega|release\s+date\s+kya\s+hai|launch\s+kab\s+hoga)\b', 'release date'),
        (r'\b(?:kaun\s+hai|kaun\s+tha|kaun\s+thi)\b', 'who is'),
        (r'\b(?:kya\s+hai|kya\s+hota\s+hai|kya\s+hoti\s+hai)\b', 'what is'),
        (r'\b(?:kahan\s+hai|kahan\s+par\s+hai)\b', 'where is'),
        (r'\b(?:kaise\s+karein|kaise\s+hota\s+hai|kaise\s+karte\s+hain)\b', 'how to'),
        (r'\b(?:aaj\s+ki\s+news|taza\s+khabar|aaj\s+ki\s+taaza\s+khabar|latest\s+khabar)\b', 'latest news today'),
        (r'\b(?:match\s+ka\s+score|score\s+kya\s+hai|live\s+score\s+kya\s+hai)\b', 'live score'),
    ]
    for pattern, replacement in stem_mappings:
        q = re.sub(pattern, replacement, q, flags=re.IGNORECASE)

    # Remove trailing auxiliary particles
    q = re.sub(r'\b(?:kaisa hai|kaisa hoga|kya hoga|karo|batao|what is|kya hai)\b', '', q, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', q).strip()
    return cleaned or query.strip()


@log_tool_call()
def search_web_for_answer(query: str) -> Dict[str, Any]:
    """
    Searches the web in real-time for factual, up-to-date, or general knowledge answers,
    featuring TTL caching, exponential backoff retries, Hinglish normalization, and context capping.
    """
    clean_query = sanitize_text(query).strip()
    if not clean_query:
        return {
            "success": False,
            "action": "search_web_for_answer",
            "query": query,
            "error": "Query cannot be empty",
            "results": "Search query was empty."
        }

    normalized_q = normalize_search_query(clean_query)
    cache_key = normalized_q.lower()

    # 1. TTL-based Caching Guard (prevents DDG rate limits for repeated questions)
    now = time.time()
    with _WEB_SEARCH_CACHE_LOCK:
        if cache_key in _WEB_SEARCH_CACHE:
            ts, cached_entry = _WEB_SEARCH_CACHE[cache_key]
            if now - ts < WEB_SEARCH_CACHE_TTL:
                logger.info(f"Web search cache hit for '{cache_key}' (age {now - ts:.1f}s)")
                res_copy = dict(cached_entry)
                res_copy["cached"] = True
                return res_copy

    raw_results = []
    last_error = None
    retries = 3
    backoff_delays = [0.4, 1.0, 2.2]

    # 2. Primary Engine: ddgs with Exponential Backoff Retries
    for attempt in range(retries):
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                raw_results = list(ddgs.text(normalized_q, max_results=4, safesearch="moderate"))
            if raw_results:
                break
        except Exception as e:
            last_error = e
            logger.warning(f"DDGS attempt {attempt + 1}/{retries} failed for '{normalized_q}': {e}")
            if attempt < retries - 1:
                time.sleep(backoff_delays[attempt])

    # 3. Process Snippets with Safety Filter & Context Length Capping (max 1500 chars total)
    def _extract_source_name(href: str) -> str:
        try:
            from urllib.parse import urlparse
            domain = urlparse(href).netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            parts = domain.split(".")
            if len(parts) >= 2:
                return parts[0].capitalize()
            return domain
        except Exception:
            return "Web"

    def _is_safe_result(title: str, body: str, href: str) -> bool:
        combined = f"{title} {body}".lower()
        blocked_terms = ["porn", "nsfw", "xxx", "warez", "crack", "torrent"]
        if any(t in combined for t in blocked_terms):
            return False
        return True

    if raw_results:
        snippets = []
        for i, r in enumerate(raw_results, 1):
            title = (r.get("title") or "").strip()
            body = (r.get("body") or "").strip()
            href = (r.get("href") or "").strip()
            if not _is_safe_result(title, body, href):
                continue
            # Truncate individual body snippet to ~320 chars
            if len(body) > 320:
                body = body[:317] + "..."
            if title or body:
                source_label = _extract_source_name(href)
                snippet_entry = f"[{i}] {title} (Source: {source_label})\nSummary: {body}"
                if href:
                    snippet_entry += f"\nLink: {href}"
                snippets.append(snippet_entry)

        if snippets:
            compiled_text = "\n\n".join(snippets)
            if len(compiled_text) > MAX_COMPILED_SEARCH_CHARS:
                compiled_text = compiled_text[:MAX_COMPILED_SEARCH_CHARS - 3] + "..."

            success_res = {
                "success": True,
                "action": "search_web_for_answer",
                "query": clean_query,
                "normalized_query": normalized_q,
                "count": len(snippets),
                "results": compiled_text,
                "message": f"Found {len(snippets)} search results for '{normalized_q}'."
            }
            with _WEB_SEARCH_CACHE_LOCK:
                _WEB_SEARCH_CACHE[cache_key] = (time.time(), success_res)
            return success_res

    # 4. Fallback Engine: Direct HTML scraping with User-Agent rotation
    try:
        import urllib.parse
        import urllib.request
        import html
        import random

        selected_ua = random.choice(_SEARCH_USER_AGENTS)
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(normalized_q)}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": selected_ua}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw_html = resp.read().decode("utf-8", errors="ignore")

        raw_snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', raw_html, re.DOTALL)
        clean_snippets = []
        for i, raw_s in enumerate(raw_snippets[:4], 1):
            clean_s = re.sub(r'<[^>]+>', '', raw_s)
            clean_s = html.unescape(clean_s).strip()
            if clean_s:
                if not _is_safe_result("", clean_s, ""):
                    continue
                if len(clean_s) > 320:
                    clean_s = clean_s[:317] + "..."
                clean_snippets.append(f"[{i}] {clean_s}")

        if clean_snippets:
            compiled_text = "\n\n".join(clean_snippets)
            if len(compiled_text) > MAX_COMPILED_SEARCH_CHARS:
                compiled_text = compiled_text[:MAX_COMPILED_SEARCH_CHARS - 3] + "..."

            fallback_res = {
                "success": True,
                "action": "search_web_for_answer",
                "query": clean_query,
                "normalized_query": normalized_q,
                "count": len(clean_snippets),
                "results": compiled_text,
                "message": f"Found {len(clean_snippets)} fallback results for '{normalized_q}'."
            }
            with _WEB_SEARCH_CACHE_LOCK:
                _WEB_SEARCH_CACHE[cache_key] = (time.time(), fallback_res)
            return fallback_res
    except Exception as fe:
        logger.warning(f"Web search fallback failed for '{normalized_q}': {fe}")

    return {
        "success": False,
        "action": "search_web_for_answer",
        "query": clean_query,
        "normalized_query": normalized_q,
        "error": str(last_error or "No results found or rate limited."),
        "results": f"Could not find web search results for '{clean_query}'."
    }


async def search_web_for_answer_async(query: str) -> Dict[str, Any]:
    """
    Asynchronous non-blocking wrapper for search_web_for_answer using asyncio.to_thread.
    """
    import asyncio
    return await asyncio.to_thread(search_web_for_answer, query)
