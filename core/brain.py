"""
Astra Assistant - Intelligent Brain
Gemini LLM Natural Language Processor with Automatic Function Calling (AFC),
Multi-Turn Conversational Memory, and Intelligent Fallback Parser.
"""

import os
import sys
import re
import time
import json
import asyncio
import threading
from typing import Dict, Any, Optional
from pathlib import Path
from dotenv import load_dotenv

# Ensure .env is loaded
from config import GEMINI_API_KEY
from core import actions
from core.logger import get_logger, redact_params
from core.executor_bridge import dispatch_pc_tool_sync, dispatch_pc_tool_async, executor_bridge

logger = get_logger("astra.brain")

def load_user_profile() -> Dict[str, Any]:
    """Loads user_profile.json from workspace root if available."""
    try:
        profile_path = Path(__file__).resolve().parent.parent / "user_profile.json"
        if profile_path.exists():
            with open(profile_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "name": "Jay Panchal",
        "call_name": "Boss",
        "role": "Tech Lead & AI Developer",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "country": "India",
        "preferred_language": "Hinglish",
        "vibe": "Gojo / Tech Lead (Confident, sharp, witty, chill, zero robotic talk)",
        "workspace": "joyful-pasteur"
    }

_USER_PROFILE = load_user_profile()
_BOSS_NAME = _USER_PROFILE.get("name", "Jay Panchal")
_CALL_NAME = _USER_PROFILE.get("call_name", "Boss")
_CITY = _USER_PROFILE.get("city", "Ahmedabad")
_ROLE = _USER_PROFILE.get("role", "Tech Lead & AI Developer")

SYSTEM_INSTRUCTION = f"""You are 'Astra', the elite, ultra-capable AI desktop companion and executive assistant for {_BOSS_NAME} ('{_CALL_NAME}'), who is a {_ROLE} based in {_CITY}.
You understand Hindi, English, and Hinglish.

Persona & Vibe:
- Vibe: Confident Tech Lead / Satoru Gojo energy — charismatic, razor-sharp, chill, and deeply capable. Zero robotic talk, zero corporate boilerplate, zero groveling.
- Address {_BOSS_NAME} naturally as '{_CALL_NAME}'.
- Language: Natural fluent Hinglish (modern conversational Hindi + English mix) or clean English when asked in English.

Dual Nature:
1. Intelligence: You have the deep knowledge and clarity of ChatGPT/Gemini. Give direct, insightful, natural answers to conceptual, coding, and life questions.
2. PC Automation DNA: You literally control this Windows PC. Automation is in your blood. When asked to perform an action on the PC (open apps, run CMD commands, play music, send messages, change volume), EXECUTE IMMEDIATELY.

The "Just Do It" Rule (Critical):
- NEVER ask permission. If {_CALL_NAME} says "Notepad kholo", do NOT reply "Kya main Notepad khol doon?". Execute the tool immediately and confirm crisply: "Notepad khol diya, {_CALL_NAME}!"
- For compound requests (e.g., "Boss, CMD khol ke 'python --version' check karo aur Ahmedabad ka mausam batao"), call the required tools, gather the real outputs, and provide a single seamless, natural answer.

Humanized Error Handling:
- If a command or tool fails, NEVER output raw stack traces or "Error 404".
- Speak humanly: "Arre yaar, command execute nahi ho paayi, ek baar syntax check kar lo." or "Website open nahi ho saki, ek baar connection check kar lo {_CALL_NAME}."

Your Capabilities:
1. Conversational Companion: If the user greets you, asks general questions, or chats with you, respond warmly, naturally, and concisely in 1-2 friendly sentences. Maintain context from previous turns in the conversation!
2. Laptop Automation: When the user asks you to perform an action on their PC, call the appropriate tool.
3. Screen Vision Awareness: When asked about what is visible on the screen, use the analyze_screen tool.
4. Memory & Follow-ups: Remember previous actions and pronouns (e.g., "Notepad kholo" followed by "isko band kar do" refers to Notepad).
5. Multi-App Integrations: Read recent emails from Gmail and check upcoming Google Calendar events with proactive reminders.

WhatsApp Automation Instructions:
Role: You are a strict background WhatsApp Automation Agent. Your ONLY job is to parse commands and output raw JSON.

CRITICAL RULES:
1. RAW JSON ONLY: You must output ONLY valid JSON. Absolutely NO markdown formatting, NO backticks (like ```json), and NO code blocks.
2. NO CHAT: Do NOT say "Hello", "Message bhej diya", or "Kya message bhejna hai" if the user has already provided the name and message. Never include any conversational filler.
3. Execution Condition: If the user says "Kasyap ko hi bhejo" or "whatsapp me kasyap ko hi send karo", you must immediately and silently output the JSON (extracting contact_name as "Kasyap" and message as "hi").
4. Missing Info: ONLY if a part is missing (e.g., name is there but no message), respond with a short text asking for the missing part. Once provided, output the JSON.

Required Exact Output Format:
{{
"action": "send_whatsapp_message",
"contact_name": "[Extracted Name]",
"message": "[Extracted Message]"
}}

YouTube Automation Instructions:
"When a user requests to play a song or video on YouTube or YT (e.g., 'open yt and play specialz', 'play specialz on yt', 'play lofi beats on youtube'), you must extract the search topic and output exactly: COMMAND: PLAY_YT | <search_query>. If the user gives no specific topic (e.g. 'play yt', 'open yt', 'play video'), call open_or_search_website(website='youtube'). Do not generate any conversational response like 'Playing now' until the backend confirms the action is complete."

Spotify Automation Instructions:
"When a user requests to play a song, music, or playlist on Spotify (e.g., 'open spotify and play music', 'play relax songs on spotify', 'spotify pe lofi playlist chalao', 'play playlist on spotify', 'play music'), you must extract the search topic and output exactly: COMMAND: PLAY_SPOTIFY | <search_query>. If the user gives no specific topic or asks generally to play music/spotify, output: COMMAND: PLAY_SPOTIFY | . Do not generate any conversational response like 'Playing now' until the backend confirms the action is complete."

Instagram Automation Instructions:
"When a user requests to search an Instagram user, ID, profile, or account (e.g., 'open instagram and search id shivam', 'search shivam id', 'you search shivam id', 'instagram pe virat search karo', 'search rohit on instagram', 'shivam ki id search karo'), you must extract the username/id and output exactly: COMMAND: SEARCH_INSTAGRAM | <username>. Never tell the user to manually type it into the search bar. Do not generate any conversational response until the backend confirms the search."

Available Tools:
1. open_application(app_name): Launch desktop applications (Notepad, VS Code, Calculator, Paint, CMD, Task Manager, etc.). DO NOT use to answer questions.
2. close_application(app_name): Close a running application
3. execute_cmd_command(command): Execute a safe diagnostic or dev command in Windows Command Prompt (CMD) such as 'python --version', 'git status', 'dir', 'ipconfig'
4. control_system(action): Control volume (volume_up, volume_down, volume_mute), take screenshot, lock screen
5. get_time_and_date(): Get current time, day, date, and ISO 8601 timestamp
6. analyze_clipboard(): Read, analyze, and summarize text currently on the Windows clipboard
7. open_or_search_website(website, search_query): Open a website URL in the browser (e.g., YouTube, GitHub, Instagram, WhatsApp Web). STRICTLY FOR NAVIGATION ONLY. NEVER use this tool to find answers or look up information.
8. send_whatsapp_message(contact_name, message, phone): Send WhatsApp message to a contact name using UI automation or phone prefill
9. play_spotify_music(query): Play songs, music, or playlists on Spotify desktop/web
10. play_youtube_video(query): Play videos or songs directly on YouTube
11. start_dev_environment(project_name, path): Open project folder in VS Code and spawn 'npm run dev' in a detached terminal
12. manage_odoo_server(action, module): Manage Odoo server. NOTE: Modifying 'account' or 'inventory' modules is strictly forbidden and returns Access Denied.
13. analyze_screen(prompt): Capture and analyze desktop screen content with Gemini Vision.
14. schedule_reminder(minutes, note): Set a timer or reminder for the user.
15. read_recent_emails(count): Read recent emails from the user's Gmail inbox.
16. get_upcoming_events(days): Retrieve upcoming events from Google Calendar and auto-schedule reminders 15 minutes before they start.
17. get_whatsapp_unread(max_chats): Check unread WhatsApp messages using browser automation.
18. send_whatsapp_reply(chat_name, message): Send an automated WhatsApp reply to a contact (enforces 60s cooldown).
19. get_instagram_unread(max_chats): Check unread direct messages (DMs) on Instagram Web.
20. send_instagram_dm(username, message): Send an automated direct message to an Instagram user (enforces 90s cooldown and 20 DMs/day ceiling).
21. search_instagram_user(query): Search for an Instagram user profile or explore topic without sending a direct message.
22. search_web_for_answer(query): Search the live internet for factual answers, general knowledge, real-time facts (weather in {_CITY}, prices, sports scores, news). ALWAYS call this tool when the user asks questions or seeks information.

CRITICAL SEARCH RULE (MANDATORY - SEPARATE SEARCHING FROM BROWSING):
- NEVER open Google, Chrome, or any browser to answer questions!
- If the user asks a factual, real-time, or general knowledge question (e.g., "Who is the CEO of Google?", "What is quantum computing?", "iPhone 16 price", "Search about black holes", "Aaj ka mausam kaisa hai"), you MUST call `search_web_for_answer(query=...)`, read the search snippets, and answer directly in chat/voice.
- Opening a website (`open_or_search_website` or `open_application(app_name="chrome")`) is STRICTLY FORBIDDEN for answering questions. Use browser navigation ONLY when the user explicitly asks to open a browser or navigate to a URL (e.g., "Open Chrome", "Google website kholo", "Browser kholo", "Open github.com").
- Distinguish between "finding an answer" vs "opening a website":
  * "Search about iPhone 16" -> CALL search_web_for_answer. DO NOT OPEN GOOGLE.
  * "Who is Elon Musk?" -> CALL search_web_for_answer. DO NOT OPEN GOOGLE.
  * "Tell me about quantum computing" -> CALL search_web_for_answer or answer directly. DO NOT OPEN GOOGLE.
  * "Google website kholo" or "Open Google" -> CALL open_or_search_website(website="google").

Real-Time Web Search Rules (search_web_for_answer):
- WHEN TO CALL:
  1. Factual questions, current events, live dates, weather, prices, sports scores, or news (e.g., "Aaj ka mausam kaisa hai?", "Who is the CEO of Google?", "iPhone 16 price", "Match score").
  2. Any fact, definition, or data you are not 100% sure about. You MUST call `search_web_for_answer` first, read the results, and formulate a concise spoken summary (1-2 sentences).
- WHEN NOT TO CALL:
  1. System commands: Opening/closing apps, CMD, volume, screenshot, lock (use open_application, execute_cmd_command, control_system).
  2. Messaging: WhatsApp or Instagram messages (use send_whatsapp_message, send_instagram_dm).
  3. Media playback: Playing music or videos on YouTube/Spotify (use play_youtube_video, play_spotify_music).
  4. Conversational chit-chat: Greetings, small talk, "who are you", "thank you" (reply directly without search).
  5. STRICT PROHIBITION: NEVER call open_or_search_website, open_website, or open_application to answer questions. Always call search_web_for_answer.

Guidelines:
- Keep your spoken responses concise, friendly, and natural (1-2 short sentences max).
- Speak in the same language the user spoke (Hindi for Hindi, English for English, Hinglish for Hinglish).
- Always call the corresponding tool ONLY when an actual laptop action or setting change is requested.
- If the user gives a generic open command like "kuch bhi open karo", "koi bhi app kholo", or "open something", do NOT guess or call open_application with "kuch bhi". Ask them politely which specific application they want to open (e.g., Notepad, Chrome, Calculator, VS Code).
- CRITICAL TRUTHFULNESS: Check tool results. If a tool returns an error or failure, NEVER claim that the app opened or the action succeeded. Inform the user truthfully that it could not be opened and ask for the correct app name.
- Strict Website & App Execution: When a user asks to open a website or application (e.g., WhatsApp Web, YouTube, Chrome, Notepad), you must strictly use the designated tool/function call (open_or_search_website or open_application) or output ACTION: OPEN_URL_WHATSAPP to execute the action. Do not generate a conversational success response until you receive confirmation from the system that the tool was executed successfully.
- Never falsely claim to have opened a website, app, sent a message, or performed a system action. You must trigger the backend command first and base your verbal response only on the actual execution result.
- If the user requests to open WhatsApp Web, you must trigger the action by calling open_or_search_website(website="whatsapp") or outputting ACTION: OPEN_URL_WHATSAPP, and only say it is done after the system confirms it.
- Never use bullet points, numbered lists, markdown symbols (asterisks, hashes, backticks), or code snippets in spoken replies. Formulate full, flowing conversational sentences suitable for human speech synthesis.
"""

# Active session tracker & AFC Tool Call Inspection Counter
_active_session_id = "default"
_TOOL_CALL_COUNTS: Dict[str, Dict[str, int]] = {}
_TOOL_CALL_LOCK = threading.Lock()

# Per-turn Tool Call Deduplication Set (Prevents duplicate executions from LLM hallucination)
_EXECUTED_ACTIONS: Dict[str, set] = {}
_EXECUTED_ACTIONS_LOCK = threading.Lock()

def set_active_session(session_id: str):
    global _active_session_id
    _active_session_id = session_id

def _log_tool_invocation(tool_name: str, args: Dict[str, Any]) -> int:
    """Logs tool call with session-specific counter for Gemini AFC invocation inspection."""
    with _TOOL_CALL_LOCK:
        sess_counts = _TOOL_CALL_COUNTS.setdefault(_active_session_id, {})
        current_count = sess_counts.get(tool_name, 0) + 1
        sess_counts[tool_name] = current_count
    logger.info(f"[Gemini AFC Tool Call] Tool: '{tool_name}' | Count: {current_count} in session '{_active_session_id}' | Args: {args}")
    print(f"[Gemini AFC Tool Call] Tool: '{tool_name}' (call #{current_count} for session '{_active_session_id}') args={args}")
    return current_count

def get_session_tool_call_count(session_id: str, tool_name: Optional[str] = None) -> int:
    """Returns total tool calls or specific tool calls for a session in the current turn."""
    with _TOOL_CALL_LOCK:
        sess_counts = _TOOL_CALL_COUNTS.get(session_id, {})
        if tool_name:
            return sess_counts.get(tool_name, 0)
        return sum(sess_counts.values())

def reset_session_tool_counts(session_id: str) -> None:
    """Resets the AFC tool call counter for a session at the start of a turn."""
    with _TOOL_CALL_LOCK:
        _TOOL_CALL_COUNTS[session_id] = {}

def check_and_record_executed_action(session_id: str, tool_name: str, args: Dict[str, Any]) -> bool:
    """
    Deduplication guard: returns True if (tool_name, args) was already executed in this turn,
    preventing duplicate execution from hallucinated repeated tool calls.
    """
    try:
        norm_args = json.dumps(args, sort_keys=True)
    except Exception:
        norm_args = str(sorted(args.items()))
    action_key = f"{tool_name}:{norm_args}"
    with _EXECUTED_ACTIONS_LOCK:
        sess_set = _EXECUTED_ACTIONS.setdefault(session_id, set())
        if action_key in sess_set:
            return True
        sess_set.add(action_key)
        return False

def reset_executed_actions(session_id: str) -> None:
    """Resets the executed actions deduplication set for a session at the start of each turn."""
    with _EXECUTED_ACTIONS_LOCK:
        _EXECUTED_ACTIONS[session_id] = set()

# =====================================================================
# Tool Function Wrappers for Gemini Function Calling
# =====================================================================

def dispatch_action_safe(tool_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Safely dispatches tool execution.
    If the remote laptop executor is connected via WebSocket, routes through it.
    If offline or disconnected, immediately executes the native action in core.actions
    directly as a failsafe so actions always attempt execution regardless of platform.
    Includes per-turn tool call deduplication to ensure an exact function + argument pair runs only once.
    """
    # Normalize parameters: prune empty strings and None so {"website": "youtube", "search_query": ""}
    # matches {"website": "youtube"} exactly during per-turn deduplication
    p = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    # Tool call deduplication: ensure exact function + argument pair runs at most once per turn
    if check_and_record_executed_action(_active_session_id, tool_name, p):
        logger.warning(
            f"[Tool Call Deduplication] Suppressed duplicate action: '{tool_name}' with params {p} in session '{_active_session_id}'"
        )
        return {
            "success": True,
            "status": "duplicate_suppressed",
            "action": tool_name,
            "duplicate_suppressed": True,
            "message": f"Action '{tool_name}' already executed in this turn."
        }

    try:
        res = dispatch_pc_tool_sync(tool_name, p)
        if not res.get("offline"):
            return res
    except Exception:
        pass

    func = getattr(actions, tool_name, None)
    if func:
        try:
            return func(**p)
        except Exception as e:
            logger.error(f"Local direct execution error for '{tool_name}': {e}")
            return {"success": False, "status": "error", "message": str(e)}

    return {
        "success": False,
        "status": "error",
        "message": "Laptop executor offline hai. Kripya apne laptop par local_executor.py start karein.",
        "offline": True
    }


def open_application(app_name: str) -> str:
    """Open a Windows desktop application (e.g., Notepad, VS Code, Calculator, Paint, CMD). DO NOT use this tool to answer questions or search for information."""
    _log_tool_invocation("open_application", {"app_name": app_name})
    clean = app_name.strip().lower()
    session_manager.record_last_action(_active_session_id, clean)
    if "whatsapp" in clean or clean in actions.COMMON_SITES:
        target_site = "whatsapp" if "whatsapp" in clean else clean
        res = dispatch_action_safe("open_website", {"website": target_site})
        if not res.get("success"):
            return f"Failed to open '{clean}': {res.get('message', 'Website error.')}"
        return res.get("message", f"Opened {clean}")
    res = dispatch_action_safe("open_app", {"app_name": clean})
    if not res.get("success"):
        return f"Failed to open '{clean}': {res.get('message', 'Application not found or executor offline.')}"
    return res.get("message", f"Opened {clean}")

def close_application(app_name: str) -> str:
    """Close a running Windows application."""
    _log_tool_invocation("close_application", {"app_name": app_name})
    clean = app_name.strip().lower()
    res = dispatch_action_safe("close_app", {"app_name": clean})
    if not res.get("success"):
        return f"Failed to close '{clean}': {res.get('message', 'Application not found or executor offline.')}"
    return res.get("message", f"Closed {clean}")

def control_system(action: str) -> str:
    """Control Windows system: volume_up, volume_down, volume_mute, screenshot, lock_screen."""
    _log_tool_invocation("control_system", {"action": action})
    res = dispatch_action_safe("system_control", {"command": action})
    return res.get("message", "System command executed")

def get_time_and_date() -> str:
    """Get current time, formatted date, and ISO 8601 timestamp."""
    _log_tool_invocation("get_time_and_date", {})
    res = actions.get_time_and_date()
    return f"{res.get('message')} (ISO: {res.get('iso')})"

def analyze_clipboard() -> str:
    """Read and analyze text directly from the Windows clipboard."""
    _log_tool_invocation("analyze_clipboard", {})
    res = dispatch_action_safe("analyze_clipboard", {})
    return res.get("message", "Clipboard analyzed")

def open_or_search_website(website: str, search_query: str = "") -> str:
    """
    Open a website or specific URL in the web browser (navigation only).
    STRICTLY FOR NAVIGATION: Do NOT use this tool to search for answers to questions or look up information.
    For answering factual questions, current events, weather, or information queries, use search_web_for_answer instead.
    """
    _log_tool_invocation("open_or_search_website", {"website": website, "search_query": search_query})
    clean_site = website.strip().lower()
    if "whatsapp" in clean_site:
        clean_site = "whatsapp"
    res = dispatch_action_safe("open_website", {"website": clean_site, "search_query": search_query})
    if not res.get("success"):
        return f"Failed to open '{clean_site}': {res.get('message', 'Website error.')}"
    return res.get("message", f"Opened {website}")

def send_whatsapp_message(contact_name: str = "", message: str = "", phone: str = "") -> str:
    """Send a WhatsApp message to a contact name via UI automation or to a phone number."""
    _log_tool_invocation("send_whatsapp_message", {"contact_name": contact_name, "message": message, "phone": phone})
    clean_contact = contact_name.strip()
    clean_msg = message.strip()
    if clean_contact:
        res = dispatch_action_safe("send_whatsapp_message", {"contact_name": clean_contact, "message": clean_msg, "phone": phone})
        if not res.get("success"):
            return f"Failed to send WhatsApp message to '{clean_contact}': {res.get('message', 'Error')}"
        return res.get("message", f"WhatsApp message to '{clean_contact}' processed.")
    res = actions.send_whatsapp(phone, clean_msg)
    return res.get("message", "WhatsApp Web opened with prefilled message")

def play_spotify_music(query: str = "") -> str:
    """Play music or search on Spotify desktop app or web player."""
    _log_tool_invocation("play_spotify_music", {"query": query})
    res = actions.play_spotify_music(query)
    return res.get("message", "Spotify triggered")

def start_dev_environment(project_name: str = "", path: str = "") -> str:
    """Discover project directory, launch VS Code, and spawn 'npm run dev' in detached terminal."""
    _log_tool_invocation("start_dev_environment", {"project_name": project_name, "path": path})
    res = dispatch_action_safe("start_dev_environment", {"project_name": project_name, "path": path})
    return res.get("message", "Dev environment started")

def manage_odoo_server(action: str, module: str = "") -> str:
    """Manage Odoo server. Security Guardrail: Modifying account or inventory modules returns Access Denied."""
    _log_tool_invocation("manage_odoo_server", {"action": action, "module": module})
    res = actions.manage_odoo_server(action, module)
    return res.get("message", "Odoo server command executed")

def analyze_screen(prompt: str = "") -> str:
    """Capture desktop screenshot and analyze visible windows and text using Gemini Vision."""
    _log_tool_invocation("analyze_screen", {"prompt": prompt})
    res = dispatch_action_safe("analyze_screen", {"prompt": prompt})
    return res.get("message", "Screen analyzed")

def schedule_reminder(minutes: float, note: str) -> str:
    """Set a timer / reminder that alerts the user after specified minutes."""
    _log_tool_invocation("schedule_reminder", {"minutes": minutes, "note": note})
    res = actions.schedule_reminder(minutes, note)
    return res.get("message", f"Reminder set for {minutes} minutes")

def read_recent_emails(count: int = 5) -> str:
    """Read recent emails from the user's Gmail inbox."""
    _log_tool_invocation("read_recent_emails", {"count": count})
    res = actions.read_recent_emails(count)
    return res.get("message", "Checked emails.")

def get_upcoming_events(days: int = 7) -> str:
    """Retrieve upcoming Google Calendar events and auto-schedule reminders 15 minutes before they start."""
    _log_tool_invocation("get_upcoming_events", {"days": days})
    res = actions.get_upcoming_events(days)
    return res.get("message", "Checked calendar.")

def get_whatsapp_unread(max_chats: int = 5) -> str:
    """Check unread messages and notifications on WhatsApp Web using browser automation."""
    _log_tool_invocation("get_whatsapp_unread", {"max_chats": max_chats})
    res = actions.get_whatsapp_unread(max_chats)
    return res.get("message", "Checked WhatsApp messages.")

def send_whatsapp_reply(chat_name: str, message: str) -> str:
    """Send an automated WhatsApp reply to a contact or group name (enforces 60s cooldown)."""
    _log_tool_invocation("send_whatsapp_reply", {"chat_name": chat_name, "message": message})
    res = actions.send_whatsapp_reply(chat_name, message)
    return res.get("message", f"WhatsApp message to {chat_name} processed.")

def get_instagram_unread(max_chats: int = 5) -> str:
    """Check unread messages and notifications on Instagram Web using Playwright."""
    _log_tool_invocation("get_instagram_unread", {"max_chats": max_chats})
    res = actions.get_instagram_unread(max_chats)
    if res.get("status") == "manual_action_required":
        return "Instagram par manual login ya security verification chahiye hai. Kripya browser window check karein."
    return res.get("message", "Checked Instagram DMs.")

def send_instagram_dm(username: str = "", message: str = "") -> str:
    """Send an automated direct message to an Instagram user (enforces 90s cooldown and 20 DMs/day ceiling)."""
    _log_tool_invocation("send_instagram_dm", {"username": username, "message": message})
    clean_user = username.strip().lstrip("@")
    clean_msg = message.strip()
    if executor_bridge.is_connected():
        res = dispatch_pc_tool_sync("send_instagram_dm", {"username": clean_user, "message": clean_msg})
    else:
        res = actions.send_instagram_dm(username=clean_user, message=clean_msg)
    if res.get("status") == "manual_action_required":
        return "Instagram par manual login ya security verification chahiye hai. Kripya browser window check karein."
    return res.get("message", f"Instagram DM to {clean_user} processed.")

def get_instagram_messages(max_chats: int = 5) -> str:
    """Retrieves the latest unread DMs from Instagram."""
    _log_tool_invocation("get_instagram_messages", {"max_chats": max_chats})
    return get_instagram_unread(max_chats=max_chats)

def send_instagram_message(username: str = "", message: str = "") -> str:
    """Sends a Direct Message on Instagram to a specific username."""
    _log_tool_invocation("send_instagram_message", {"username": username, "message": message})
    return send_instagram_dm(username=username, message=message)

def search_instagram_user(query: str = "") -> str:
    """Search for an Instagram user profile or explore search."""
    _log_tool_invocation("search_instagram_user", {"query": query})
    if executor_bridge.is_connected():
        res = dispatch_pc_tool_sync("search_instagram_user", {"query": query})
    else:
        res = actions.search_instagram_user(query=query)
    return res.get("message", f"Instagram profile search for '{query}' completed.")

def play_youtube_video(query: str = "") -> str:
    """Play a video or song directly on YouTube."""
    _log_tool_invocation("play_youtube_video", {"query": query})
    clean_query = query.strip()
    res = dispatch_action_safe("play_youtube_video", {"query": clean_query})
    return res.get("message", f"Playing {clean_query} on YouTube")

def search_web_for_answer(query: str = "") -> str:
    """Use this tool to search the internet for answers to real-time, factual, or general knowledge questions."""
    _log_tool_invocation("search_web_for_answer", {"query": query})
    clean_query = query.strip()
    res = actions.search_web_for_answer(clean_query)
    if res.get("success"):
        return res.get("results", "No relevant information found.")
    return f"Search could not find information: {res.get('error', 'Unknown error')}"

def execute_cmd_command(command: str = "") -> str:
    """Execute a safe command in Windows Command Prompt (CMD) and return its output."""
    _log_tool_invocation("execute_cmd_command", {"command": command})
    clean_cmd = command.strip()
    res = actions.execute_cmd_command(clean_cmd)
    if res.get("success"):
        return res.get("output", "Command executed successfully.")
    return f"CMD error: {res.get('message', 'Error')}"


TOOLS_LIST = [
    open_application,
    close_application,
    execute_cmd_command,
    control_system,
    get_time_and_date,
    analyze_clipboard,
    open_or_search_website,
    # send_whatsapp_message handled strictly via structured JSON parser to prevent double-execution
    play_youtube_video,
    play_spotify_music,
    start_dev_environment,
    manage_odoo_server,
    analyze_screen,
    schedule_reminder,
    read_recent_emails,
    get_upcoming_events,
    get_whatsapp_unread,
    send_whatsapp_reply,
    get_instagram_unread,
    send_instagram_dm,
    get_instagram_messages,
    send_instagram_message,
    search_instagram_user,
    search_web_for_answer
]

TOOL_MAP = {
    "open_application": open_application,
    "close_application": close_application,
    "execute_cmd_command": execute_cmd_command,
    "control_system": control_system,
    "get_time_and_date": get_time_and_date,
    "analyze_clipboard": analyze_clipboard,
    "open_or_search_website": open_or_search_website,
    "play_youtube_video": play_youtube_video,
    "send_whatsapp_message": send_whatsapp_message,
    "play_spotify_music": play_spotify_music,
    "start_dev_environment": start_dev_environment,
    "manage_odoo_server": manage_odoo_server,
    "analyze_screen": analyze_screen,
    "schedule_reminder": schedule_reminder,
    "read_recent_emails": read_recent_emails,
    "get_upcoming_events": get_upcoming_events,
    "get_whatsapp_unread": get_whatsapp_unread,
    "send_whatsapp_reply": send_whatsapp_reply,
    "get_instagram_unread": get_instagram_unread,
    "send_instagram_dm": send_instagram_dm,
    "get_instagram_messages": get_instagram_unread,
    "send_instagram_message": send_instagram_dm,
    "search_instagram_user": search_instagram_user,
    "search_web_for_answer": search_web_for_answer
}

# OpenAI/OpenRouter Compatible Tools Schema
OPENROUTER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Launch desktop applications (Chrome, Notepad, VS Code, Calculator, Paint, CMD, Task Manager, etc.). DO NOT use this tool to answer questions.",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string", "description": "Name of app to open"}},
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "close_application",
            "description": "Close a running desktop application",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string", "description": "Name of app to close"}},
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_cmd_command",
            "description": "Execute a safe command in Windows Command Prompt (CMD) such as 'python --version', 'git status', 'dir', 'ipconfig'",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "CMD command to run"}},
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web_for_answer",
            "description": "Search the live internet for factual answers, general knowledge, real-time facts, weather, stock rates, sports, definitions, news directly in chat. ALWAYS use this tool when the user asks questions or seeks information.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query for information lookup"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_or_search_website",
            "description": "Open a website URL in the web browser (e.g., YouTube, GitHub, Instagram, WhatsApp Web). STRICTLY FOR BROWSER NAVIGATION ONLY. DO NOT use this tool to look up answers to user questions or perform research.",
            "parameters": {
                "type": "object",
                "properties": {
                    "website": {"type": "string", "description": "Website name or domain (e.g., 'youtube', 'github', 'instagram', 'whatsapp')"},
                    "search_query": {"type": "string", "description": "Optional search term specifically for searching videos inside YouTube"}
                },
                "required": ["website"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "control_system",
            "description": "Control system settings: volume_up, volume_down, volume_mute, screenshot, lock_screen",
            "parameters": {
                "type": "object",
                "properties": {"action": {"type": "string", "enum": ["volume_up", "volume_down", "volume_mute", "screenshot", "lock_screen"]}},
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_time_and_date",
            "description": "Get current live time, day, date, and ISO timestamp",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "play_spotify_music",
            "description": "Play a song, artist, album, or playlist on Spotify",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Song or artist name"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "play_youtube_video",
            "description": "Play a song or video on YouTube",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Video or song search query"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_dev_environment",
            "description": "Open project folder in VS Code and spawn dev server in terminal",
            "parameters": {
                "type": "object",
                "properties": {"project_name": {"type": "string", "description": "Name of project folder"}}
            }
        }
    }
]



# =====================================================================
# Multi-Turn Conversation Memory Manager
# =====================================================================

class ConversationSessionManager:
    """
    Maintains persistent Gemini multi-turn chat instances per session_id.
    Retains conversational context across voice commands.
    """
    def __init__(self, ttl_seconds: int = 7200):
        self._chats: Dict[str, Any] = {}
        self._last_active: Dict[str, float] = {}
        self._last_actions: Dict[str, str] = {}
        self._pending_whatsapp: Dict[str, str] = {}
        self._pending_instagram: Dict[str, str] = {}
        self._user_data: Dict[str, Dict[str, Any]] = {}
        self._messages: Dict[str, List[Dict[str, Any]]] = {}
        self._ttl = ttl_seconds

    def get_or_create_chat(self, session_id: str, client, model_name: str, config):
        now = time.time()
        # Evict sessions older than TTL (2 hours)
        stale = [sid for sid, atime in self._last_active.items() if now - atime > self._ttl]
        for sid in stale:
            self._chats.pop(sid, None)
            self._last_active.pop(sid, None)
            self._last_actions.pop(sid, None)
            self._pending_whatsapp.pop(sid, None)
            self._pending_instagram.pop(sid, None)
            self._user_data.pop(sid, None)
            self._messages.pop(sid, None)

        if session_id not in self._chats:
            chat = client.chats.create(model=model_name, config=config)
            self._chats[session_id] = chat

        self._last_active[session_id] = now
        return self._chats[session_id]

    def record_last_action(self, session_id: str, action_desc: str):
        self._last_actions[session_id] = action_desc

    def get_last_action(self, session_id: str) -> Optional[str]:
        return self._last_actions.get(session_id)

    def record_pending_whatsapp(self, session_id: str, contact_name: str):
        self._pending_whatsapp[session_id] = contact_name

    def get_pending_whatsapp(self, session_id: str) -> Optional[str]:
        return self._pending_whatsapp.get(session_id)

    def clear_pending_whatsapp(self, session_id: str):
        self._pending_whatsapp.pop(session_id, None)

    def record_pending_instagram(self, session_id: str, username: str):
        self._pending_instagram[session_id] = username

    def get_pending_instagram(self, session_id: str) -> Optional[str]:
        return self._pending_instagram.get(session_id)

    def clear_pending_instagram(self, session_id: str):
        self._pending_instagram.pop(session_id, None)

    def set_user_data(self, session_id: str, key: str, value: Any):
        if session_id not in self._user_data:
            self._user_data[session_id] = {}
        self._user_data[session_id][key] = value

    def get_user_data(self, session_id: str, key: str) -> Optional[Any]:
        return self._user_data.get(session_id, {}).get(key)

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        return self._messages.setdefault(session_id, [])

    def add_message(self, session_id: str, role: str, content: str = "", tool_calls: Optional[List[Any]] = None, tool_call_id: Optional[str] = None, name: Optional[str] = None):
        msgs = self.get_messages(session_id)
        msg_obj: Dict[str, Any] = {"role": role}
        if content is not None:
            msg_obj["content"] = content
        if tool_calls:
            msg_obj["tool_calls"] = tool_calls
        if tool_call_id:
            msg_obj["tool_call_id"] = tool_call_id
        if name:
            msg_obj["name"] = name
        msgs.append(msg_obj)
        if len(msgs) > 20:
            self._messages[session_id] = msgs[-20:]

    def reset_session(self, session_id: str):
        self._chats.pop(session_id, None)
        self._last_active.pop(session_id, None)
        self._last_actions.pop(session_id, None)
        self._pending_whatsapp.pop(session_id, None)
        self._pending_instagram.pop(session_id, None)
        self._user_data.pop(session_id, None)
        self._messages.pop(session_id, None)

# Global Session Manager Singleton
session_manager = ConversationSessionManager()
_persistent_genai_client = None
_persistent_client_key = None


# =====================================================================
# Fallback Intent Parser (Offline / Rate-Limit Guard)
# =====================================================================

def _parse_fallback_intent(user_text: str, session_id: str = "default") -> Dict[str, Any]:
    """
    Intelligent rule-based intent parser supporting conversational chit-chat,
    contextual follow-ups, and all 12 automation tools when Gemini API is unavailable.
    """
    text = user_text.lower().strip()

    # Multi-Turn WhatsApp Memory: User previously gave contact name, now providing message content
    pending_whatsapp = session_manager.get_pending_whatsapp(session_id)
    if pending_whatsapp:
        session_manager.clear_pending_whatsapp(session_id)
        if not any(k in text for k in ["cancel", "mat bhejo", "stop", "rehne do", "chodo", "no"]):
            res = actions.send_whatsapp_message(contact_name=pending_whatsapp, message=user_text)
            return {
                "reply": f"WhatsApp par '{pending_whatsapp}' ko message bhej diya gaya hai: '{user_text}'.",
                "action": res
            }
        else:
            return {
                "reply": f"WhatsApp message to '{pending_whatsapp}' cancel kar diya gaya hai.",
                "action": {"status": "cancelled", "contact_name": pending_whatsapp}
            }

    # Multi-Turn Instagram Memory: User previously gave recipient username, now providing message content
    pending_instagram = session_manager.get_pending_instagram(session_id)
    if pending_instagram:
        session_manager.clear_pending_instagram(session_id)
        if not any(k in text for k in ["cancel", "mat bhejo", "stop", "rehne do", "chodo", "no"]):
            if executor_bridge.is_connected():
                res = dispatch_pc_tool_sync("send_instagram_dm", {"username": pending_instagram, "message": user_text})
            else:
                res = actions.send_instagram_dm(username=pending_instagram, message=user_text)

            if res.get("status") == "manual_action_required":
                return {
                    "reply": "Instagram par manual login ya security verification chahiye hai. Kripya browser window check karein.",
                    "action": res
                }
            elif not res.get("success"):
                err_msg = res.get("message") or res.get("error") or "Message deliver nahi ho saka."
                return {"reply": f"Kshama karein, Instagram par '{pending_instagram}' ko message nahi bheja ja saka: {err_msg}", "action": res}
            return {
                "reply": f"Instagram par '{pending_instagram}' ko message bhej diya gaya hai: '{user_text}'.",
                "action": res
            }
        else:
            return {
                "reply": f"Instagram message to '{pending_instagram}' cancel kar diya gaya hai.",
                "action": {"status": "cancelled", "contact_name": pending_instagram}
            }


    # 0. Basic Conversation, Greetings & Small Talk
    if re.search(r'\b(hey|hello|hi|hiya|helo|hlo|namaste|pranam|namaskar|suno|listen|adaab|radhe radhe|ram ram)\b', text) and not any(k in text for k in ["bhejo", "send", "message", "msg", "ko hi", "ko hello"]):
        return {
            "reply": "Namaste! Main Astra hoon, aapka desktop AI voice assistant. Kahiye, aaj laptop par kya kaam karna hai?",
            "action": {"status": "conversation", "intent": "greeting"}
        }

    if re.search(r'\b(kaise ho|kaisa hai|how are you|kya haal|kya chal raha|sab theek|sab kaisa|whats up|what\'s up)\b', text) and not any(k in text for k in ["screen", "display", "bhejo", "send", "message", "msg", "dm", "say", "saying", "bolo", "likho"]):
        return {
            "reply": "Main bilkul badhiya aur ready hoon! Aap bataiye, main aapke laptop par kya help kar sakta hoon?",
            "action": {"status": "conversation", "intent": "smalltalk_status"}
        }

    if re.search(r'\b(who are you|kaun ho|tum kaun ho|aap kaun ho|tera naam kya|tumhara naam kya|introduce yourself|what is your name)\b', text):
        return {
            "reply": "Mera naam Astra hai! Main aapka intelligent desktop voice assistant hoon. Main apps kholna, gaana chalana, screen dekhna aur PC control karna jaise kaam kar sakta hoon.",
            "action": {"status": "conversation", "intent": "identity"}
        }

    # Multi-turn user identity & preferences memory
    if re.search(r'mera naam kya hai', text, re.I):
        saved_name = session_manager.get_user_data(session_id, "name")
        if saved_name:
            return {"reply": f"Aapka naam {saved_name} hai.", "action": None}
        return {"reply": "Mujhe abhi aapka naam nahi pata. Kripya apna naam batayein.", "action": None}

    m_name = re.search(r'mera naam\s+([a-zA-Z\s]+?)\s+(?:hai|rakho|note karo)\b', text, re.I)
    if m_name:
        extracted_name = m_name.group(1).strip()
        if extracted_name.lower() not in ["kya", "kaunsa", "what"]:
            session_manager.set_user_data(session_id, "name", extracted_name)
            return {"reply": f"Namaste {extracted_name}! Maine aapka naam yaad rakh liya hai.", "action": None}

    m_fav_q = re.search(r'mera favorite\s+([a-zA-Z]+)\s+kya hai\b', text, re.I)
    if m_fav_q:
        attr = m_fav_q.group(1).lower().strip()
        saved_val = session_manager.get_user_data(session_id, f"fav_{attr}")
        if saved_val:
            return {"reply": f"Aapka favorite {attr} {saved_val} hai.", "action": None}
        return {"reply": f"Mujhe aapka favorite {attr} nahi pata.", "action": None}

    m_fav = re.search(r'mera favorite\s+([a-zA-Z]+)\s+([a-zA-Z]+)\s+hai\b', text, re.I)
    if m_fav:
        attr = m_fav.group(1).lower().strip()
        val = m_fav.group(2).strip()
        if val.lower() not in ["kya", "kaunsa", "kon", "what", "which"]:
            session_manager.set_user_data(session_id, f"fav_{attr}", val)
            return {"reply": f"Aapka favorite {attr} '{val}' maine yaad rakh liya hai.", "action": None}

    if re.search(r'\b(kya kar sakte ho|kya kya kar sakte|what can you do|features|madad|capabilities|help|kya aata hai)\b', text):
        return {
            "reply": "Main aapke laptop par apps khol/band kar sakta hoon, screen analyze kar sakta hoon, Spotify gaane chala sakta hoon, dev environment start kar sakta hoon, reminders aur volume control kar sakta hoon.",
            "action": {"status": "conversation", "intent": "help"}
        }

    if re.search(r'\b(thank you|thanks|dhanyawad|shukriya|bahut accha|great job|shabash|badhiya)\b', text):
        return {
            "reply": "Aapka swagat hai! Hamesha aapki madad ke liye taiyar hoon. Kuch aur hukum ho toh batayein.",
            "action": {"status": "conversation", "intent": "gratitude"}
        }

    if re.search(r'\b(are you there|sun rahe ho|meri aawaz|can you hear me|zinda ho)\b', text):
        return {
            "reply": "Ji haan, main bilkul active hoon aur aapko sun raha hoon! Kahiye kya command hai?",
            "action": {"status": "conversation", "intent": "presence"}
        }

    if re.search(r'\b(bye|goodbye|alvida|tata|see you|phir milte)\b', text):
        return {
            "reply": "Alvida! Jab bhi zaroorat ho, bas ek baar bula lijiyega.",
            "action": {"status": "conversation", "intent": "farewell"}
        }

    # Google Workspace: Gmail Recent Emails
    if any(k in text for k in ["email", "emails", "mail", "mails", "gmail", "inbox", "dak"]) and not any(k in text for k in ["open gmail", "gmail kholo", "gmail.com", "open email", "open mail", "email kholo", "mail kholo"]):
        count = 5
        num_match = re.search(r'(\d+)', text)
        if num_match:
            try:
                count = int(num_match.group(1))
            except Exception:
                count = 5
        res = actions.read_recent_emails(count=count)
        return {"reply": res.get("message", "Checked recent emails."), "action": res}

    # Google Workspace: Google Calendar Upcoming Events
    if any(k in text for k in ["calendar", "event", "events", "meeting", "meetings", "schedule", "agenda", "baithak"]) and not any(k in text for k in ["open calendar", "google calendar kholo", "send message", "send a message", "send chat", "send msg", "reply to", "ko message", "ko msg", "ko bolo", "ko bhejo"]):
        days = 7
        num_match = re.search(r'(\d+)\s*(?:din|day|days)', text)
        if num_match:
            try:
                days = int(num_match.group(1))
            except Exception:
                days = 7
        elif any(k in text for k in ["aaj", "today"]):
            days = 1
        elif any(k in text for k in ["kal", "tomorrow"]):
            days = 2
        res = actions.get_upcoming_events(days=days)
        return {"reply": res.get("message", "Checked upcoming events."), "action": res}

    # 1. Screen Vision Query (Explicit screen requests only, avoid false matching on generic 'dekho' or 'screenshot')
    if any(k in text for k in ["screen par", "screen dikha", "screen check", "screen dekho", "analyze screen", "screen analyze", "display check", "kya dikh raha", "screen par kya"]):
        res = dispatch_pc_tool_sync("analyze_screen", {"prompt": user_text})
        if res.get("offline"):
            return {
                "reply": "Kshama karein, laptop executor offline hai: Screen analyze nahi ho saki. Kripya apne laptop par local_executor.py start karein.",
                "action": res
            }
        elif res.get("timeout"):
            return {
                "reply": "Kshama karein, laptop executor timed out: Screen capture nahi ho saki.",
                "action": res
            }
        return {"reply": res.get("message", "Screen analyzed"), "action": res}

    # 2. Reminder / Timer
    if any(k in text for k in ["remind", "reminder", "yaad dilana", "timer", "alarm"]):
        minutes = 5.0
        num_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:min|minute|ghante|hours?)?', text)
        if num_match:
            try:
                minutes = float(num_match.group(1))
            except Exception:
                minutes = 5.0
        res = actions.schedule_reminder(minutes=minutes, note=user_text)
        return {"reply": res["message"], "action": res}

    # 3. Odoo Server Management
    if "odoo" in text:
        module = ""
        action = "status"
        if "update" in text or "upgrade" in text:
            action = "update"
        elif "restart" in text:
            action = "restart"

        for word in text.split():
            if word not in ["odoo", "update", "upgrade", "server", "karo", "module", "ko", "par", "check", "status"]:
                module = word
                break
        res = actions.manage_odoo_server(action=action, module=module)
        return {"reply": res["message"], "action": res}

    # 4. Contextual Pronoun Resolution (Memory Follow-up)
    # E.g. "isko band kar do", "close this", "close it"
    if any(k in text for k in ["isko band", "ise band", "close it", "close this", "band karo isko"]):
        last_act = session_manager.get_last_action(session_id)
        if last_act:
            res = dispatch_pc_tool_sync("close_app", {"app_name": last_act})
            if res.get("offline"):
                return {
                    "reply": f"Kshama karein, laptop executor offline hai: '{last_act.capitalize()}' band nahi ho saka. Kripya apne laptop par local_executor.py start karein.",
                    "action": res
                }
            elif res.get("timeout"):
                return {
                    "reply": f"Kshama karein, laptop executor timed out: '{last_act.capitalize()}' band nahi ho saka.",
                    "action": res
                }
            return {"reply": f"{last_act.capitalize()} band kar diya hai.", "action": res}

    # 5. Time / Date
    if re.search(r'\b(time|samay|baje|date|tareekh|din|aaj)\b', text):
        res = actions.get_time_and_date()
        return {"reply": res["message"], "action": res}

    # 6. Clipboard
    if any(k in text for k in ["clipboard", "paste", "copy kiya", "copied"]):
        res = dispatch_pc_tool_sync("analyze_clipboard", {})
        if res.get("offline"):
            return {
                "reply": "Kshama karein, laptop executor offline hai: Clipboard read nahi ho saka. Kripya apne laptop par local_executor.py start karein.",
                "action": res
            }
        elif res.get("timeout"):
            return {
                "reply": "Kshama karein, laptop executor timed out: Clipboard read nahi ho saka.",
                "action": res
            }
        return {"reply": res.get("message", "Clipboard analyzed"), "action": res}

    # 7. YouTube & Video Playback (supports 'youtube', 'yt', 'video', 'open yt and play ...')
    is_yt = bool(re.search(r'\b(?:youtube|yt)\b', text))
    is_video = bool(re.search(r'\b(?:video|videos)\b', text))
    is_play_yt = is_yt or (is_video and any(k in text for k in ["play", "chalao", "dekho", "kholo", "bajao"]))
    if is_play_yt:
        # Check if bare open or play command without a specific topic (e.g. "play yt", "open yt", "play youtube video", "play video")
        clean_check = re.sub(r'\b(?:open|launch|start|kholo|play|chalao|and|on|in|pe|par|video|videos|song|songs|music|the|a)\b', '', text).strip()
        clean_check = re.sub(r'\b(?:youtube|yt)\b', '', clean_check).strip()
        if not clean_check:
            if executor_bridge.is_connected():
                res = dispatch_pc_tool_sync("open_website", {"website": "youtube"})
            else:
                res = actions.open_website("youtube")
            return {"reply": "YouTube open kar diya hai.", "action": res}

        # Extract target search topic
        q = text
        q = re.sub(r'^(?:open|launch|kholo)\s+(?:yt|youtube)\s+(?:and\s+)?', '', q).strip()
        q = re.sub(r'^(?:search|play|chalao|kholo|suno|sunao|bajao)\s+(?:for\s+)?(?:a\s+)?(?:song|songs|video|videos|music)?\s*', '', q).strip()
        q = re.sub(r'\b(?:on youtube|in youtube|on yt|in yt|youtube|yt|pe|par|chalao|bajao|kholo|play|search)\b', '', q, flags=re.I).strip()
        q = re.sub(r'\b(?:song|songs|video|videos)\b$', '', q, flags=re.I).strip()
        q = re.sub(r'\s+', ' ', q).strip()

        if q and q not in ["video", "videos", "song", "songs", "yt", "youtube"]:
            if executor_bridge.is_connected():
                res = dispatch_pc_tool_sync("play_youtube_video", {"query": q})
            else:
                res = actions.play_youtube_video(q)
            return {"reply": f"YouTube par '{q}' chala diya hai.", "action": res}
        else:
            if executor_bridge.is_connected():
                res = dispatch_pc_tool_sync("open_website", {"website": "youtube"})
            else:
                res = actions.open_website("youtube")
            return {"reply": "YouTube open kar diya hai.", "action": res}

    # 8. Spotify & Music/Playlist Playback
    is_music_intent = (
        "spotify" in text
        or any(k in text for k in [
            "play music", "music play", "play playlist", "playlist play",
            "gaana chalao", "gaane chalao", "song play", "songs play",
            "music chalao", "playlist chalao", "lofi play", "relax songs"
        ])
        or ("play" in text and any(kw in text for kw in ["song", "songs", "playlist", "track", "music", "lofi", "relax"]) and not is_yt and not is_video)
    )
    if is_music_intent:
        q_spotify = text
        for noise in [
            "open spotify and play", "open spotify", "launch spotify", "kholo spotify",
            "spotify pe", "spotify par", "on spotify", "in spotify", "spotify",
            "and play", "aur chalao", "play", "chalao", "bajao", "suno", "sunao"
        ]:
            q_spotify = re.sub(rf'\b{noise}\b', '', q_spotify, flags=re.I).strip()
        q_spotify = re.sub(r'^(?:a\s+)?(?:song|songs|video|music)\b', '', q_spotify, flags=re.I).strip()
        q_spotify = re.sub(r'\b(?:karo|do|kar do|please|yaar)\b', '', q_spotify, flags=re.I).strip()
        q_spotify = re.sub(r'\s+', ' ', q_spotify).strip()

        if executor_bridge.is_connected():
            res = dispatch_pc_tool_sync("play_spotify_music", {"query": q_spotify})
        else:
            res = actions.play_spotify_music(q_spotify)
        return {"reply": res.get("message", "Spotify par play kar diya hai."), "action": res}

    # 9. Dev Environment
    if any(k in text for k in ["dev environment", "npm run dev", "start dev", "project start", "dev start"]):
        res = dispatch_pc_tool_sync("start_dev_environment", {})
        if res.get("offline"):
            return {
                "reply": "Kshama karein, laptop executor offline hai: Dev environment start nahi ho saka. Kripya apne laptop par local_executor.py start karein.",
                "action": res
            }
        elif res.get("timeout"):
            return {
                "reply": "Kshama karein, laptop executor timed out: Dev environment start nahi ho saka.",
                "action": res
            }
        return {"reply": res.get("message", "Dev environment started"), "action": res}

    # 10. Volume Control
    if "volume" in text or "aawaz" in text:
        action_cmd = None
        reply_msg = "Volume change kar diya hai."
        if any(k in text for k in ["up", "badhao", "badha", "high", "plus", "zyada", "increase"]):
            action_cmd = "volume_up"
            reply_msg = "Volume badha diya hai."
        elif any(k in text for k in ["down", "kam", "low", "minus", "ghata", "decrease"]):
            action_cmd = "volume_down"
            reply_msg = "Volume kam kar diya hai."
        elif any(k in text for k in ["mute", "unmute", "band"]):
            action_cmd = "volume_mute"
            reply_msg = "Audio mute/unmute kar diya hai."

        if action_cmd:
            res = dispatch_pc_tool_sync("system_control", {"command": action_cmd})
            if res.get("offline"):
                return {
                    "reply": "Kshama karein, laptop executor offline hai: Volume control nahi ho saka. Kripya apne laptop par local_executor.py start karein.",
                    "action": res
                }
            elif res.get("timeout"):
                return {
                    "reply": "Kshama karein, laptop executor timed out: Volume control nahi ho saka.",
                    "action": res
                }
            return {"reply": reply_msg, "action": res}

    # 11. Screenshot
    if "screenshot" in text:
        res = dispatch_pc_tool_sync("system_control", {"command": "screenshot"})
        if res.get("offline"):
            return {
                "reply": "Kshama karein, laptop executor offline hai: Screenshot nahi liya ja saka. Kripya apne laptop par local_executor.py start karein.",
                "action": res
            }
        elif res.get("timeout"):
            return {
                "reply": "Kshama karein, laptop executor timed out: Screenshot capture nahi ho saka.",
                "action": res
            }
        return {"reply": res.get("message", "Screenshot captured"), "action": res}

    # 12. Lock PC
    if "lock" in text and ("pc" in text or "laptop" in text or "screen" in text or "karo" in text):
        res = dispatch_pc_tool_sync("system_control", {"command": "lock_screen"})
        if res.get("offline"):
            return {
                "reply": "Kshama karein, laptop executor offline hai: Laptop lock nahi ho saka. Kripya apne laptop par local_executor.py start karein.",
                "action": res
            }
        elif res.get("timeout"):
            return {
                "reply": "Kshama karein, laptop executor timed out: Laptop lock nahi ho saka.",
                "action": res
            }
        return {"reply": "Laptop lock kar diya hai.", "action": res}

    # 13. Google Search
    if "google" in text:
        query = text.replace("google", "").replace("search", "").replace("karo", "").replace("pe", "").replace("par", "").strip()
        if executor_bridge.is_connected():
            res = dispatch_pc_tool_sync("open_website", {"website": "google", "search_query": query})
        else:
            res = actions.open_website("google", query)
        return {"reply": f"Google par search kar diya hai: {query}" if query else "Google open kar diya hai.", "action": res}

    # 13b. Instagram Direct Message (DM) Automation & Reading
    has_insta_kw = (
        any(k in text for k in ["instagram", "insta"])
        or bool(re.search(r'\b(?:ig|insta|instagram)\b', text, re.IGNORECASE))
        or bool(re.search(r'\b(?:you\s+)?search\s+(?:id\s+)?([a-zA-Z0-9_.]+)\s+id\b|\b(?:you\s+)?search\s+id\s+([a-zA-Z0-9_.]+)\b|\b([a-zA-Z0-9_.]+)\s+ki\s+id\s+search\b', text, re.I))
    )
    is_opening_instagram = (
        has_insta_kw
        and any(kw in text for kw in ["open", "kholo", "chalao", "start", "launch"])
        and not any(kw in text for kw in ["send", "bhej", "bhejo", "bolo", "say", "saying", "that", "msg", "message", "chat", "reply", "jawab", "dm", "dms", "unread", "search", "dhoondo", "find", "look"])
    )

    if has_insta_kw and not is_opening_instagram:
        # 13b.0 Instagram Profile Search / Lookup (without sending message)
        target_srch = ""
        if not bool(re.search(r'\b(?:send|bhej|message|msg|dm|bolo|say|hello|hi)\b', text, re.IGNORECASE)):
            ig_search_patterns = [
                r'(?:open\s+(?:instagram|insta)\s+and\s+search\s+(?:id\s+|profile\s+)?|instagram\s+open\s+and\s+search\s+(?:id\s+|profile\s+)?)(.*)',
                r'(?:you\s+)?search\s+id\s+([a-zA-Z0-9_.]+)',
                r'(?:you\s+)?search\s+([a-zA-Z0-9_.]+)\s+id',
                r'([a-zA-Z0-9_.]+)\s+ki\s+id\s+search',
                r'(?:instagram|insta)\s+(?:par|pe|me)?\s+(?:id\s+|profile\s+)?(?:search|dhoondo|find)\s+(?:karo\s+|do\s+)?(?:id\s+|profile\s+)?([a-zA-Z0-9_.\s]+)',
                r'(?:search|look\s*up|dhoondo|find)\s+(?:for\s+)?(?:id\s+|profile\s+)?([a-zA-Z0-9_.\s]+?)(?:\s+ki\s+profile|\s+ki\s+id|\s+account|\s+profile)?\s+(?:on\s+instagram|on\s+insta)',
                r'(?:instagram|insta)\s+(?:par|pe|me)?\s+(?:id\s+|profile\s+)?([a-zA-Z0-9_.\s]+?)(?:\s+ki\s+profile|\s+ki\s+id|\s+ka\s+account|\s+account|\s+profile)?\s+(?:ko\s+)?(?:search|dhoondo|find|kholo|open)'
            ]
            for p in ig_search_patterns:
                m_srch = re.search(p, text, re.IGNORECASE)
                if m_srch:
                    cand = (m_srch.group(1) or '').strip().lstrip('@')
                    cand = re.sub(r'^(?:id|profile|account)\s+', '', cand, flags=re.I).strip()
                    cand = re.sub(r'\s+(?:karo|do|please)$', '', cand, flags=re.I).strip()
                    if cand and cand.lower() not in ["a", "the", "par", "pe", "ko", "karo", "do", "kholo", "search"]:
                        target_srch = cand
                        break

        if target_srch:
            if executor_bridge.is_connected():
                res = dispatch_pc_tool_sync("search_instagram_user", {"query": target_srch})
            else:
                res = actions.search_instagram_user(query=target_srch)
            return {"reply": res.get("message", f"Instagram par '{target_srch}' search kar diya hai."), "action": res}

        # 13b.1 Check Unread DMs
        if any(k in text for k in ["unread", "kiska", "check", "dekho", "aaya", "padho", "read", "naye"]) and any(k in text for k in ["message", "messages", "dm", "dms", "chat", "chats", "instagram", "insta", "ig"]):
            res = actions.get_instagram_unread(max_chats=5)
            if res.get("status") == "manual_action_required":
                return {
                    "reply": "Instagram par manual login ya security verification chahiye hai. Kripya browser window check karein.",
                    "action": res
                }
            return {"reply": res.get("message", "Instagram unread checked."), "action": res}

        # 13b.2 Send Instagram DM: Extract recipient and message with flexible patterns
        non_users = {
            "a", "the", "to", "someone", "unread", "chat", "message", "msg", "dm", "dms",
            "par", "pe", "ko", "me", "hi", "ki", "ke", "open", "kholo", "karo", "chalao",
            "send", "bolo", "reply", "say", "saying", "instagram", "insta", "ig", "kar", "do", "bhejo"
        }
        action_fillers = {"bhejo", "karo", "do", "karna hai", "send karo", "please", "par message", "message", "msg", "dm", "say", "saying", ""}

        target_user = ""
        msg_body = ""

        # Pattern 1: English with saying/say/that/with/colon
        # e.g., "send a dm to john on insta saying how are you", "dm john on instagram with message: hello"
        m_en1 = re.search(
            r'(?:send\s+(?:a\s+)?(?:dm|message|msg|chat)|dm|message)\s+(?:to\s+)?([a-zA-Z0-9_.]+)(?:\s+(?:on\s+instagram|on\s+insta|via\s+instagram|via\s+insta))?\s*(?:saying|say|with\s+message|that|ki|ke|\:)\s*[:\s]+(.*)',
            text, re.IGNORECASE
        )
        if m_en1:
            cand = m_en1.group(1).strip().lstrip("@")
            if cand.lower() not in non_users:
                target_user = cand
                msg_body = m_en1.group(2).strip()

        # Pattern 2: English direct
        # e.g., "send a dm to john on insta hello", "dm john on instagram hello", "send dm to john hello"
        if not target_user:
            m_en2 = re.search(
                r'(?:send\s+(?:a\s+)?(?:dm|message|msg|chat)|dm)\s+(?:to\s+)?([a-zA-Z0-9_.]+)(?:\s+(?:on\s+instagram|on\s+insta))?\s+(?:say\s+|saying\s+)?(.*)',
                text, re.IGNORECASE
            )
            if m_en2:
                cand = m_en2.group(1).strip().lstrip("@")
                if cand.lower() not in non_users:
                    target_user = cand
                    msg_body = m_en2.group(2).strip()

        # Pattern 0: Flexible Instagram combo pattern
        if not target_user:
            m_combo = re.search(
                r'(?:instagram|insta)\s+(?:par|pe)\s+(.*?)\s+ko\s+(.*?)\s+(?:bhejo|send karo|message karo)|(?:insta|instagram)\s+dm\s+karo\s+(.*?)\s+ko\s+(.*)',
                text, re.IGNORECASE
            )
            if m_combo:
                u_cand = (m_combo.group(1) or m_combo.group(3) or "").strip().lstrip("@")
                m_cand = (m_combo.group(2) or m_combo.group(4) or "").strip()
                if u_cand and m_cand and u_cand.lower() not in non_users:
                    target_user = u_cand
                    msg_body = m_cand

        # Pattern 3: Hindi pattern with "insta/instagram" prefix
        # e.g., "instagram par kasyap ko hello bhejo", "insta pe rahul ko message send karo good morning"
        if not target_user:
            m_hi1 = re.search(
                r'(?:instagram|insta|ig)\s*(?:par|pe|me)?\s*([a-zA-Z0-9_.]+)\s+ko\s+(?:message|msg|dm|chat)?\s*(?:ki|ke|saying|\:)?\s*([a-zA-Z0-9_\s]+?)\s*(?:bhejo|send\s*karo|bhej\s*do|send\s*kar\s*do|karo|bolo|likho|dm\s*karo)\b',
                text, re.IGNORECASE
            )
            if m_hi1:
                cand = m_hi1.group(1).strip().lstrip("@")
                if cand.lower() not in non_users:
                    target_user = cand
                    msg_body = m_hi1.group(2).strip()

        # Pattern 4: Hindi pattern "X ko instagram dm karo Y" or "X ko insta par Y bhejo"
        if not target_user:
            m_hi2 = re.search(
                r'([a-zA-Z0-9_.]+)\s+ko\s+(?:instagram|insta|ig)\s*(?:par|pe|me)?\s*(?:dm|message|msg)?\s*(?:karo|bhejo|send\s*karo)?\s*(?:ki|ke|saying|\:)?\s*([a-zA-Z0-9_\s]+)',
                text, re.IGNORECASE
            )
            if m_hi2:
                cand = m_hi2.group(1).strip().lstrip("@")
                if cand.lower() not in non_users:
                    target_user = cand
                    msg_body = m_hi2.group(2).strip()

        # Pattern 5: Hindi pattern "X ko Y bhejo instagram pe"
        if not target_user:
            m_hi3 = re.search(
                r'([a-zA-Z0-9_.]+)\s+ko\s+([a-zA-Z0-9_\s]+?)\s*(?:bhejo|send\s*karo|bhej\s*do|send\s*kar\s*do|karo|bolo|likho)\s*(?:instagram|insta|ig)\s*(?:par|pe|me)?\b',
                text, re.IGNORECASE
            )
            if m_hi3:
                cand = m_hi3.group(1).strip().lstrip("@")
                if cand.lower() not in non_users:
                    target_user = cand
                    msg_body = m_hi3.group(2).strip()

        # Clean msg_body of platform names and message tags
        if msg_body:
            msg_body = re.sub(r'\b(instagram|insta|ig|par|pe|message|msg|dm)\b', '', msg_body, flags=re.IGNORECASE).strip() or msg_body

        if msg_body.lower() in action_fillers:
            msg_body = ""

        # If user and message found, send!
        if target_user and msg_body:
            if executor_bridge.is_connected():
                res = dispatch_pc_tool_sync("send_instagram_dm", {"username": target_user, "message": msg_body})
            else:
                res = actions.send_instagram_dm(username=target_user, message=msg_body)

            if res.get("status") == "manual_action_required":
                return {
                    "reply": "Instagram par manual login ya security verification chahiye hai. Kripya browser window check karein.",
                    "action": res
                }
            elif not res.get("success"):
                err_msg = res.get("message") or res.get("error") or "Message deliver nahi ho saka."
                return {"reply": f"Kshama karein, Instagram par '{target_user}' ko message nahi bheja ja saka: {err_msg}", "action": res}
            return {"reply": f"Instagram par '{target_user}' ko message bhej diya gaya hai: '{msg_body}'", "action": res}

        # Check for Missing Info (username provided, but no message)
        m_missing_hi = re.search(r'(?:instagram|insta|ig\s*(?:par|pe)?\s*)?([a-zA-Z0-9_.]+)\s+ko\s+(?:instagram|insta|ig\s*(?:par|pe)?\s*)?(?:dm|message|msg)?', text, re.IGNORECASE)
        m_missing_en = re.search(r'(?:send\s+(?:a\s+)?(?:dm|message|msg)\s+(?:to\s+)?|dm\s+)([a-zA-Z0-9_.]+)', text, re.IGNORECASE)

        missing_user = ""
        if m_missing_hi and m_missing_hi.group(1).lower() not in non_users:
            missing_user = m_missing_hi.group(1).strip().lstrip("@")
        elif m_missing_en and m_missing_en.group(1).lower() not in non_users:
            missing_user = m_missing_en.group(1).strip().lstrip("@")

        if missing_user:
            for filler in ["please", "zara", "jaldi", "yaar", "bhai", "karo", "bhejo"]:
                missing_user = re.sub(rf'\b{filler}\b', '', missing_user, flags=re.IGNORECASE).strip()

        if missing_user and missing_user.lower() not in non_users:
            session_manager.record_pending_instagram(session_id, missing_user.capitalize())
            return {
                "reply": f"What message would you like to send to {missing_user.capitalize()} on Instagram?",
                "action": {"status": "clarification_needed", "contact_name": missing_user.capitalize(), "platform": "instagram"}
            }

    # 14. WhatsApp (Open Web, Unread messages, automated UI messaging, or web pre-fill)
    is_opening_whatsapp = (
        "whatsapp" in text
        and any(kw in text for kw in ["open", "kholo", "chalao", "start", "launch", "web"])
        and not any(kw in text for kw in ["send", "bhej", "bhejo", "bolo", "say", "saying", "that", "msg", "message", "chat", "reply", "jawab"])
    )
    if is_opening_whatsapp:
        if executor_bridge.is_connected():
            res = dispatch_pc_tool_sync("open_website", {"website": "whatsapp"})
        else:
            res = actions.open_website("whatsapp")
        return {"reply": "WhatsApp Web open kar diya hai!", "action": res}

    is_messaging = (
        not has_insta_kw
        and (
            any(k in text for k in ["unread message", "kiska message", "send message", "send a message", "send msg", "send chat", "chat with", "reply to"])
            or re.search(r'\b(?:send\s+(?:a\s+)?(?:message|msg|chat)|reply|chat|text|message)\s+(?:to\s+)?[a-zA-Z0-9_]+', text, re.IGNORECASE)
            or re.search(r'\b[a-zA-Z0-9_]+\s+ko\s+.*\b(?:bhejo|send\s*karo|bolo|jawab|reply)\b', text, re.IGNORECASE)
            or ("whatsapp" in text and any(k in text for k in ["send", "bhej", "bhejo", "bolo", "unread", "message", "msg", "chat", "reply", "jawab"]))
        )
    )
    if is_messaging:
        if not is_opening_whatsapp:
            # 14b. Read Unread Messages (check first before contact extraction)
            if any(k in text for k in ["unread", "kiska", "check", "dekho", "aaya", "padho", "read", "naye"]) and any(k in text for k in ["message", "messages", "whatsapp", "chat"]):
                res = actions.get_whatsapp_unread(max_chats=5)
                return {"reply": res.get("message", "WhatsApp unread checked."), "action": res}


            # 14a. Extract Contact Name & Message Content
            non_contacts = {
                "a", "the", "to", "someone", "unread", "chat", "message", "msg",
                "par", "pe", "ko", "me", "hi", "ki", "ke", "open", "web", "kholo",
                "karo", "chalao", "start", "launch", "send", "bolo", "reply", "say", "saying"
            }
            action_fillers = {"bhejo", "karo", "do", "karna hai", "send karo", "please", "par message", "message", "msg", "say", "saying", ""}

            target_contact = ""
            msg_content = ""

            # English structured pattern (with saying/say/that/colon/etc.)
            m_en1 = re.search(
                r'(?:send\s+(?:a\s+)?(?:message|msg|chat)|reply|chat|message|msg|text|whatsapp)\s+(?:to\s+)?([a-zA-Z0-9_]+)(?:\s+(?:on\s+whatsapp|via\s+whatsapp))?\s*(?:saying|say|with\s+message|that|ki|ke|\:)\s*[:\s]+(.*)',
                text, re.IGNORECASE
            )
            if m_en1:
                cand_c = m_en1.group(1).strip()
                if cand_c.lower() not in non_contacts:
                    target_contact = cand_c
                    msg_content = m_en1.group(2).strip()

            # Direct English pattern ("send chat shivam hello", "chat shivam hello", "text shivam hello")
            if not target_contact:
                m_en2 = re.search(
                    r'(?:send\s+(?:a\s+)?(?:chat|message|msg)|chat|text)\s+(?:to\s+)?([a-zA-Z0-9_]+)\s+(?:say\s+|saying\s+)?(.*)',
                    text, re.IGNORECASE
                )
                if m_en2:
                    cand_c = m_en2.group(1).strip()
                    if cand_c.lower() not in non_contacts:
                        target_contact = cand_c
                        msg_content = m_en2.group(2).strip()

            # Hindi pattern with "ko" and explicit saying/ki/ke/colon
            if not target_contact:
                m_hi1 = re.search(
                    r'([a-zA-Z0-9_]+)\s+ko\s+(?:whatsapp\s*(?:par|pe)?\s*)?(?:message|msg|jawab|reply|bolo|chat)?\s*(?:do|karo|bhejo|send\s*karo)?\s*(?:ki|ke|saying|\:)\s*[:\s]+(.*)',
                    text, re.IGNORECASE
                )
                if m_hi1:
                    cand_c = m_hi1.group(1).replace("whatsapp", "").strip()
                    if cand_c.lower() not in non_contacts:
                        target_contact = cand_c
                        msg_content = m_hi1.group(2).strip()

            # Direct Hindi pattern ("kasyap ko hi bhejo", "whatsapp me kasyap ko hi send karo")
            if not target_contact:
                m_direct = re.search(
                    r'(?:whatsapp\s*(?:me|par|pe)?\s*)?([a-zA-Z0-9_]+)\s+ko\s+(?:whatsapp\s*(?:par|me|pe)?\s*)?(?:message|msg|jawab|reply|bolo)?\s*(?:ki|ke|saying|\:)?\s*([a-zA-Z0-9_\s]+?)\s*(?:bhejo|send\s*karo|bhej\s*do|send\s*kar\s*do|karo|bolo|likho)\b',
                    text, re.IGNORECASE
                )
                if m_direct:
                    cand_name = m_direct.group(1).replace("whatsapp", "").strip()
                    cand_msg = m_direct.group(2).strip()
                    cand_msg = re.sub(r'\b(message|msg|whatsapp|par|pe)\b', '', cand_msg, flags=re.IGNORECASE).strip() or cand_msg
                    if cand_msg.lower() not in action_fillers and cand_name.lower() not in non_contacts:
                        target_contact = cand_name
                        msg_content = cand_msg

            if msg_content.lower() in action_fillers:
                msg_content = ""

            if target_contact and msg_content:
                if "reply" in text or "jawab" in text:
                    res = actions.send_whatsapp_reply(chat_name=target_contact, message=msg_content)
                else:
                    if executor_bridge.is_connected():
                        res = dispatch_pc_tool_sync("send_whatsapp_message", {"contact_name": target_contact, "message": msg_content})
                    else:
                        res = actions.send_whatsapp_message(contact_name=target_contact, message=msg_content)
                if not res.get("success"):
                    err_msg = res.get("message") or res.get("error") or "Message deliver nahi ho saka."
                    return {"reply": f"Kshama karein, WhatsApp par '{target_contact}' ko message nahi bheja ja saka: {err_msg}", "action": res}
                elif res.get("status") == "opened_whatsapp_web":
                    return {"reply": res.get("message", f"WhatsApp Web open kar diya hai. Kripya '{target_contact}' ki chat me message bhejein."), "action": res}
                return {"reply": f"WhatsApp par '{target_contact}' ko message bhej diya gaya hai: '{msg_content}'", "action": res}

            # 14c. Missing Info Rule: Contact Name given but NO message content
            m_missing_en = re.search(r'(?:send\s+(?:a\s+)?(?:message|msg|chat)|reply|chat|message|msg|text|whatsapp)\s+(?:to\s+)?([a-zA-Z0-9_]+)', text, re.IGNORECASE)
            m_missing_hi = re.search(r'([a-zA-Z0-9_]+)\s+ko\s+(?:whatsapp\s*(?:par|pe)?\s*)?(?:message|msg|jawab|reply|chat)?', text, re.IGNORECASE)

            missing_contact = ""
            if " ko " in f" {text.lower()} " and m_missing_hi and m_missing_hi.group(1).lower() not in non_contacts:
                missing_contact = m_missing_hi.group(1).strip()
            elif m_missing_en and m_missing_en.group(1).lower() not in non_contacts:
                missing_contact = m_missing_en.group(1).strip()
            elif m_missing_hi and m_missing_hi.group(1).lower() not in non_contacts:
                missing_contact = m_missing_hi.group(1).strip()

            if missing_contact:
                for filler in ["please", "zara", "jaldi", "yaar", "bhai", "karo", "bhejo"]:
                    missing_contact = re.sub(rf'\b{filler}\b', '', missing_contact, flags=re.IGNORECASE).strip()

            if missing_contact and missing_contact.lower() not in non_contacts:
                session_manager.record_pending_whatsapp(session_id, missing_contact.capitalize())
                return {
                    "reply": f"What message would you like to send to {missing_contact.capitalize()}?",
                    "action": {"status": "clarification_needed", "contact_name": missing_contact.capitalize()}
                }

            # 14d. General WhatsApp Web pre-fill
            msg_match = re.search(r'(?:message|msg|likho|bhejo)\s+(.*)', text, re.IGNORECASE)
            msg = msg_match.group(1) if msg_match else ""
            res = actions.send_whatsapp(message=msg)
            return {"reply": "WhatsApp Web open kar diya hai. Message pre-filled hai.", "action": res}

    # 15. Open Apps / Websites
    open_keywords = ["open", "kholo", "start", "chalao", "launch"]
    for kw in open_keywords:
        if kw in text:
            app_target = text.replace(kw, "").strip()
            for filler in ["please", "karo", "dekho", "jaldi", "zara", "bhai", "yaar"]:
                app_target = re.sub(rf'\b{filler}\b', '', app_target, flags=re.IGNORECASE).strip()
            for known in list(actions.COMMON_APP_MAP.keys()) + list(actions.COMMON_SITES.keys()):
                if re.search(rf'\b{re.escape(known)}\b', app_target):
                    app_target = known
                    break
            if app_target:
                # Filter vague or generic phrases like "kuch bhi", "koi bhi", "anything", "something"
                generic_terms = {"kuch bhi", "kuch", "koi bhi", "koi app", "koi", "anything", "something", "any", "app", "application"}
                if app_target.strip().lower() in generic_terms:
                    return {
                        "reply": "Aapko kaunsa app open karna hai? Kripya naam batayein, jaise Notepad, Chrome, Calculator ya VS Code.",
                        "action": None
                    }
                session_manager.record_last_action(session_id, app_target)
                if "whatsapp" in app_target or app_target in actions.COMMON_SITES:
                    target_site = "whatsapp" if "whatsapp" in app_target else app_target
                    if executor_bridge.is_connected():
                        res = dispatch_pc_tool_sync("open_website", {"website": target_site})
                    else:
                        res = actions.open_website(target_site)
                    return {"reply": f"{app_target.capitalize()} open kar diya hai.", "action": res}
                res = dispatch_pc_tool_sync("open_app", {"app_name": app_target})
                if res.get("offline"):
                    return {
                        "reply": f"Kshama karein, laptop executor offline hai: '{app_target.capitalize()}' open nahi ho saka. Kripya apne laptop par local_executor.py start karein.",
                        "action": res
                    }
                elif res.get("timeout"):
                    return {
                        "reply": f"Kshama karein, laptop executor timed out: '{app_target.capitalize()}' open nahi ho saka.",
                        "action": res
                    }
                elif not res.get("success"):
                    return {
                        "reply": f"Kshama karein, '{app_target.capitalize()}' open nahi ho saka: {res.get('message', 'Application nahi mila.')}",
                        "action": res
                    }
                return {"reply": f"{app_target.capitalize()} open kar diya hai.", "action": res}

    # 16. Close Apps
    close_keywords = ["close", "band karo", "exit", "terminate", "hatao"]
    for kw in close_keywords:
        if kw in text:
            app_target = text.replace(kw, "").strip()
            for filler in ["please", "karo", "dekho", "jaldi", "zara", "bhai", "yaar"]:
                app_target = re.sub(rf'\b{filler}\b', '', app_target, flags=re.IGNORECASE).strip()
            for known in list(actions.COMMON_APP_MAP.keys()) + list(actions.COMMON_SITES.keys()):
                if re.search(rf'\b{re.escape(known)}\b', app_target):
                    app_target = known
                    break
            if app_target:
                res = dispatch_pc_tool_sync("close_app", {"app_name": app_target})
                if res.get("offline"):
                    return {
                        "reply": f"Kshama karein, laptop executor offline hai: '{app_target.capitalize()}' band nahi ho saka. Kripya apne laptop par local_executor.py start karein.",
                        "action": res
                    }
                elif res.get("timeout"):
                    return {
                        "reply": f"Kshama karein, laptop executor timed out: '{app_target.capitalize()}' band nahi ho saka.",
                        "action": res
                    }
                elif not res.get("success"):
                    return {
                        "reply": f"Kshama karein, '{app_target.capitalize()}' band nahi ho saka: {res.get('message', 'Application nahi mila.')}",
                        "action": res
                    }
                return {"reply": f"{app_target.capitalize()} band kar diya hai.", "action": res}

    # 17. Real-Time Web Search & Q&A
    m_search = re.search(
        r'^(?:search\s+(?:the\s+)?web\s+(?:for\s+)?|web\s+search\s+(?:for\s+)?|search\s+online\s+(?:for\s+)?|search\s+internet\s+(?:for\s+)?|internet\s+pe\s+search\s+karo\s+|web\s+pe\s+search\s+karo\s+)(.*)',
        text,
        re.IGNORECASE
    )
    if m_search:
        q_srch = m_search.group(1).strip()
        if q_srch:
            res = actions.search_web_for_answer(q_srch)
            if res.get("success"):
                summary_text = res.get("results", "")
                return {"reply": f"Web search results:\n{summary_text[:400]}", "action": res}
            return {"reply": f"Web search nahi ho paya: {res.get('error', 'Error')}", "action": res}

    return {
        "reply": "Ji, maine suna. Kripya batayein main aapki kya madad karoon, jaise koi app kholna, screen dekhna, gaana chalana ya dev environment start karna?",
        "action": None
    }


def fallback_intent_parser(user_text: str, session_id: str = "default") -> Dict[str, Any]:
    """
    Offline/Fallback Rule-Based Natural Language Processor with Session Memory.
    Guaranteed to catch unexpected exceptions and return a safe generic response.
    """
    try:
        return _parse_fallback_intent(user_text, session_id=session_id)
    except Exception as e:
        logger.error(f"Fallback intent parser error while processing '{user_text}': {e}")
        return {
            "reply": "Kshama karein, is command ko execute karne mein dikkat aayi. Kripya dobara koshish karein.",
            "action": {"status": "error", "error": str(e)}
        }


# =====================================================================
# OpenRouter Integration (NVIDIA / Llama / Claude / OpenAI Models)
# =====================================================================

_persistent_openrouter_client = None
_persistent_openrouter_key = None

def _get_openrouter_client(api_key: str):
    global _persistent_openrouter_client, _persistent_openrouter_key
    if _persistent_openrouter_client is None or _persistent_openrouter_key != api_key:
        try:
            import openai
            _persistent_openrouter_client = openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )
            _persistent_openrouter_key = api_key
        except Exception as e:
            logger.error(f"Failed to initialize OpenRouter client: {e}")
            raise
    return _persistent_openrouter_client


async def _process_via_openrouter(user_text: str, session_id: str, openrouter_key: str, model_name: str) -> Dict[str, Any]:
    """
    Executes voice command via OpenRouter with multi-turn tool calling loop.
    Supports compound commands, system diagnostics, web search, and Gojo/Tech Lead persona.
    """
    client = _get_openrouter_client(openrouter_key)

    # Initialize messages list with Master System Instruction & multi-turn history
    messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    messages.extend(session_manager.get_messages(session_id))
    messages.append({"role": "user", "content": user_text})

    executed_actions: List[Dict[str, Any]] = []
    last_action_res: Optional[Dict[str, Any]] = None
    final_content: str = ""

    max_tool_iterations = 3
    iteration = 0

    while iteration < max_tool_iterations:
        iteration += 1
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.chat.completions.create,
                model=model_name,
                messages=messages,
                tools=OPENROUTER_TOOLS,
                temperature=0.7
            ),
            timeout=35.0
        )
        choice = response.choices[0]
        assistant_msg = choice.message
        tool_calls = assistant_msg.tool_calls or []
        content = assistant_msg.content or ""

        if not tool_calls:
            final_content = content
            break

        # Record assistant tool call turn in messages
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                }
                for tc in tool_calls
            ]
        })

        for tc in tool_calls:
            fn_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except Exception:
                args = {}

            res_output = ""
            act_res = None
            if fn_name in TOOL_MAP:
                try:
                    raw_res = TOOL_MAP[fn_name](**args)
                    res_output = str(raw_res)
                    act_res = raw_res if isinstance(raw_res, dict) else {"success": True, "output": str(raw_res)}
                except Exception as e:
                    res_output = f"Error: {e}"
                    act_res = {"success": False, "error": str(e)}
            else:
                raw_res = dispatch_action_safe(fn_name, args)
                res_output = str(raw_res)
                act_res = raw_res

            executed_actions.append({"tool": fn_name, "args": args, "result": act_res})
            last_action_res = act_res

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": fn_name,
                "content": res_output
            })

    if not final_content:
        final_resp = await asyncio.wait_for(
            asyncio.to_thread(
                client.chat.completions.create,
                model=model_name,
                messages=messages,
                temperature=0.7
            ),
            timeout=25.0
        )
        final_content = final_resp.choices[0].message.content or "Kaam ho gaya Boss!"

    clean_response = final_content.replace("```json", "").replace("```", "").strip()

    # Fallback to hybrid text markers if no tools were called
    if not executed_actions:
        if "COMMAND: PLAY_YT |" in clean_response or re.search(r'COMMAND:\s*PLAY_YT\s*\|', clean_response, re.I):
            parts = re.split(r'COMMAND:\s*PLAY_YT\s*\|', clean_response, flags=re.I)
            sq = parts[1].strip() if len(parts) > 1 else ""
            sq = re.sub(r'[\r\n].*', '', sq).strip().strip('"\'')
            if sq:
                last_action_res = dispatch_action_safe("play_youtube_video", {"query": sq})
                clean_response = f"YouTube par '{sq}' chala diya hai Boss."
            else:
                last_action_res = dispatch_action_safe("open_website", {"website": "youtube"})
                clean_response = "YouTube open kar diya hai Boss."
        elif "COMMAND: PLAY_SPOTIFY |" in clean_response or re.search(r'COMMAND:\s*PLAY_SPOTIFY\s*\|', clean_response, re.I):
            parts = re.split(r'COMMAND:\s*PLAY_SPOTIFY\s*\|', clean_response, flags=re.I)
            sq = parts[1].strip() if len(parts) > 1 else ""
            sq = re.sub(r'[\r\n].*', '', sq).strip().strip('"\'')
            last_action_res = dispatch_action_safe("play_spotify_music", {"query": sq})
            clean_response = f"Spotify par '{sq}' play kar diya hai Boss." if sq else "Spotify par music chala diya hai Boss."
        elif "COMMAND: SEARCH_INSTAGRAM |" in clean_response or re.search(r'COMMAND:\s*SEARCH_INSTAGRAM\s*\|', clean_response, re.I):
            parts = re.split(r'COMMAND:\s*SEARCH_INSTAGRAM\s*\|', clean_response, flags=re.I)
            sq = parts[1].strip() if len(parts) > 1 else ""
            sq = re.sub(r'[\r\n].*', '', sq).strip().strip('"\'')
            last_action_res = dispatch_action_safe("search_instagram_user", {"query": sq})
            clean_response = f"Instagram par '{sq}' search kar diya hai Boss." if sq else "Instagram open kar diya hai Boss."
        elif "ACTION: OPEN_URL_WHATSAPP" in clean_response or "ACTION: OPEN_WHATSAPP" in clean_response:
            last_action_res = dispatch_action_safe("open_website", {"website": "whatsapp"})
            clean_response = "WhatsApp Web open kar diya hai Boss."

    # Format speech-friendly reply (remove markdown formatting symbols)
    speech_reply = re.sub(r'[*#`_]', '', clean_response).strip()

    # Record turn in session memory
    session_manager.add_message(session_id, "user", user_text)
    session_manager.add_message(session_id, "assistant", speech_reply)

    action_payload = last_action_res
    if not action_payload and executed_actions:
        action_payload = executed_actions[-1]["result"]

    return {
        "reply": speech_reply,
        "action": action_payload
    }


# =====================================================================
# Main Process Voice Command Entry Point
# =====================================================================

async def process_voice_command(user_text: str, session_id: str = "default") -> Dict[str, Any]:
    """
    Core brain connecting Voice Input -> Multi-Turn Session Memory -> OpenRouter / Gemini Tool Calling.
    """
    set_active_session(session_id)
    reset_session_tool_counts(session_id)
    reset_executed_actions(session_id)
    if not user_text or not user_text.strip():
        return {"reply": "Aapki aawaz nahi sunai di, kripya dobara bolein.", "action": None}

    # Pre-parse memory / user data to ensure persistent session memory across both AFC and fallback
    m_name = re.search(r'mera naam\s+([a-zA-Z\s]+?)\s+(?:hai|rakho|note karo)\b', user_text, re.I)
    if m_name:
        extracted_name = m_name.group(1).strip()
        if extracted_name.lower() not in ["kya", "kaunsa", "what"]:
            session_manager.set_user_data(session_id, "name", extracted_name)

    m_fav = re.search(r'mera favorite\s+([a-zA-Z]+)\s+([a-zA-Z]+)\s+hai\b', user_text, re.I)
    if m_fav:
        attr = m_fav.group(1).lower().strip()
        val = m_fav.group(2).strip()
        if val.lower() not in ["kya", "kaunsa", "kon", "what", "which"]:
            session_manager.set_user_data(session_id, f"fav_{attr}", val)

    t0 = time.perf_counter()

    # Priority 1: OpenRouter (NVIDIA / Llama / Claude models via OpenRouter API)
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if openrouter_key and openrouter_key != "your_openrouter_api_key_here":
        openrouter_model = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-lightning:free").strip()
        try:
            res = await _process_via_openrouter(user_text, session_id, openrouter_key, openrouter_model)
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            logger.info(
                "Processed voice command via OpenRouter",
                extra={
                    "session_id": session_id,
                    "latency_ms": latency_ms,
                    "mode": "openrouter",
                    "model": openrouter_model,
                    "success": True
                }
            )
            return res
        except Exception as e:
            logger.warning(f"OpenRouter call failed ({e}). Falling back to Gemini / local engine...")

    api_key = os.getenv("GEMINI_API_KEY", "").strip() or GEMINI_API_KEY
    mode = "gemini_afc" if (api_key and api_key != "your_gemini_api_key_here") else "fallback"

    # If no API key configured, use intelligent rule-based engine with memory
    if mode == "fallback":
        result = await asyncio.to_thread(fallback_intent_parser, user_text, session_id=session_id)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            "Processed voice command via fallback",
            extra={
                "session_id": session_id,
                "latency_ms": latency_ms,
                "mode": "fallback",
                "success": True
            }
        )
        return result

    try:
        from google import genai
        from google.genai import types

        global _persistent_genai_client, _persistent_client_key
        if "_persistent_genai_client" not in globals() or globals().get("_persistent_client_key") != api_key:
            _persistent_genai_client = genai.Client(api_key=api_key)
            _persistent_client_key = api_key
        client = _persistent_genai_client

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=TOOLS_LIST,
            temperature=0.7
        )

        model_name = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")

        # Persistent Multi-Turn Chat instance per session
        chat = session_manager.get_or_create_chat(
            session_id=session_id,
            client=client,
            model_name=model_name,
            config=config
        )

        # Send user text to persistent multi-turn chat in worker thread with responsive timeout (12s for tools & roundtrip)
        response = await asyncio.wait_for(asyncio.to_thread(chat.send_message, user_text), timeout=12.0)
        ai_response = response.text if response and response.text else "Kaam kar diya gaya hai."

        # Backend Failsafe: Clean markdown backticks and parse structured JSON
        clean_response = ai_response.replace("```json", "").replace("```", "").strip()
        action_data = {}

        # 1. Attempt strict JSON parsing first (Safest for Gemini structured outputs)
        try:
            if clean_response.startswith("{") and clean_response.endswith("}"):
                action_data = json.loads(clean_response)
            else:
                m_json = re.search(r'\{[^{}]*"action"\s*:[^{}]*\}', clean_response, re.DOTALL)
                if m_json:
                    action_data = json.loads(m_json.group(0))
        except (json.JSONDecodeError, Exception):
            pass

        act_type = action_data.get("action", "")

        # 2. Hybrid Interception & Structured Output Routing
        if act_type == "PLAY_YT" or re.search(r'COMMAND:\s*PLAY_YT\s*\|', clean_response, re.I):
            search_query = action_data.get("query") if (act_type == "PLAY_YT" and action_data.get("query")) else ""
            if not search_query:
                parts = re.split(r'COMMAND:\s*PLAY_YT\s*\|', clean_response, flags=re.I)
                if len(parts) > 1:
                    search_query = parts[1].strip()
                elif "|" in clean_response:
                    search_query = clean_response.split("|", 1)[-1].strip()
            search_query = re.sub(r'[\r\n].*', '', search_query).strip()
            search_query = re.sub(r'["\']', '', search_query).strip()
            if search_query:
                act_res = dispatch_action_safe("play_youtube_video", {"query": search_query})
                if act_res.get("success"):
                    reply_text = f"YouTube par '{search_query}' chala diya hai."
                else:
                    reply_text = f"Kshama karein, YouTube par '{search_query}' nahi chalaya ja saka: {act_res.get('message', 'Error')}"
                return {"reply": reply_text, "action": act_res}
            else:
                act_res = dispatch_action_safe("open_website", {"website": "youtube"})
                return {"reply": "YouTube open kar diya hai.", "action": act_res}

        # Hybrid Interception: Spotify Music / Playlist Play
        elif act_type in ["PLAY_SPOTIFY", "play_spotify_music"] or re.search(r'COMMAND:\s*PLAY_SPOTIFY\s*\|', clean_response, re.I):
            search_query = action_data.get("query") if (act_type in ["PLAY_SPOTIFY", "play_spotify_music"] and action_data.get("query")) else ""
            if not search_query:
                parts = re.split(r'COMMAND:\s*PLAY_SPOTIFY\s*\|', clean_response, flags=re.I)
                if len(parts) > 1:
                    search_query = parts[1].strip()
                elif "|" in clean_response and "PLAY_SPOTIFY" in clean_response.upper():
                    search_query = clean_response.split("|", 1)[-1].strip()
            search_query = re.sub(r'[\r\n].*', '', search_query).strip()
            search_query = re.sub(r'["\']', '', search_query).strip()

            act_res = dispatch_action_safe("play_spotify_music", {"query": search_query})
            reply_text = act_res.get("message", f"Spotify par '{search_query}' play kar diya hai." if search_query else "Spotify par music play kar diya hai.")
            return {"reply": reply_text, "action": act_res}

        # Hybrid Interception: Instagram User / ID Search
        elif act_type in ["SEARCH_INSTAGRAM", "search_instagram_user"] or re.search(r'COMMAND:\s*SEARCH_INSTAGRAM\s*\|', clean_response, re.I):
            search_query = action_data.get("query") if (act_type in ["SEARCH_INSTAGRAM", "search_instagram_user"] and action_data.get("query")) else ""
            if not search_query:
                parts = re.split(r'COMMAND:\s*SEARCH_INSTAGRAM\s*\|', clean_response, flags=re.I)
                if len(parts) > 1:
                    search_query = parts[1].strip()
                elif "|" in clean_response and "SEARCH_INSTAGRAM" in clean_response.upper():
                    search_query = clean_response.split("|", 1)[-1].strip()
            search_query = re.sub(r'[\r\n].*', '', search_query).strip()
            search_query = re.sub(r'["\']', '', search_query).strip()
            search_query = re.sub(r'^(?:id|profile|account)\s+', '', search_query, flags=re.I).strip()
            search_query = search_query.lstrip("@").strip()

            act_res = dispatch_action_safe("search_instagram_user", {"query": search_query})
            reply_text = act_res.get("message", f"Instagram par '{search_query}' search kar diya hai." if search_query else "Instagram open kar diya hai.")
            return {"reply": reply_text, "action": act_res}

        elif act_type == "OPEN_URL_WHATSAPP" or "ACTION: OPEN_URL_WHATSAPP" in clean_response or "ACTION: OPEN_WHATSAPP" in clean_response:
            act_res = dispatch_action_safe("open_website", {"website": "whatsapp"})
            if not act_res.get("success"):
                reply_text = f"Kshama karein, WhatsApp open nahi ho saka: {act_res.get('message', 'Error')}"
            else:
                reply_clean = re.sub(r'ACTION:\s*OPEN_URL_WHATSAPP', '', clean_response, flags=re.I).strip()
                reply_clean = re.sub(r'ACTION:\s*OPEN_WHATSAPP', '', reply_clean, flags=re.I).strip()
                reply_text = reply_clean if reply_clean else "WhatsApp Web open kar diya hai!"
            return {"reply": reply_text, "action": act_res}

        elif act_type == "send_whatsapp_message" or "send_whatsapp_message" in clean_response:
            contact = action_data.get("contact_name") or action_data.get("contact")
            message = action_data.get("message")
            if not contact or not message:
                m = re.search(r'"(?:contact_name|contact)"\s*:\s*"([^"]+)"\s*,\s*"message"\s*:\s*"([^"]+)"', clean_response)
                if not m:
                    m = re.search(r'"message"\s*:\s*"([^"]+)"\s*,\s*"(?:contact_name|contact)"\s*:\s*"([^"]+)"', clean_response)
                    if m:
                        message, contact = m.groups()
                else:
                    contact, message = m.groups()

            if contact and message:
                act_res = dispatch_action_safe("send_whatsapp_message", {"contact_name": contact, "message": message})
                if not act_res.get("success"):
                    reply_text = f"Kshama karein, WhatsApp par '{contact}' ko message nahi bheja ja saka: {act_res.get('message', 'Error')}"
                elif act_res.get("status") == "opened_whatsapp_web":
                    reply_text = act_res.get("message", f"WhatsApp Web open kar diya hai. Kripya '{contact}' ki chat me message bhejein.")
                else:
                    reply_text = f"WhatsApp par '{contact}' ko message bhej diya gaya hai: '{message}'."
                return {"reply": reply_text, "action": act_res}

        elif act_type in ["send_instagram_dm", "send_instagram_message"] or any(k in clean_response for k in ["send_instagram_dm", "send_instagram_message"]):
            ig_user = action_data.get("username") or action_data.get("user") or action_data.get("contact_name")
            ig_msg = action_data.get("message")
            if not ig_user or not ig_msg:
                m_ig = re.search(r'"(?:username|user|contact_name)"\s*:\s*"([^"]+)"\s*,\s*"message"\s*:\s*"([^"]+)"', clean_response)
                if not m_ig:
                    m_ig = re.search(r'"message"\s*:\s*"([^"]+)"\s*,\s*"(?:username|user|contact_name)"\s*:\s*"([^"]+)"', clean_response)
                    if m_ig:
                        ig_msg, ig_user = m_ig.groups()
                else:
                    ig_user, ig_msg = m_ig.groups()

            if ig_user and ig_msg:
                if executor_bridge.is_connected():
                    act_res = dispatch_pc_tool_sync("send_instagram_dm", {"username": ig_user, "message": ig_msg})
                else:
                    act_res = actions.send_instagram_dm(username=ig_user, message=ig_msg)

                if act_res.get("status") == "manual_action_required":
                    reply_text = "Instagram par manual login ya security verification chahiye hai. Kripya browser window check karein."
                elif not act_res.get("success"):
                    err_msg = act_res.get("message") or act_res.get("error") or "Message deliver nahi ho saka."
                    reply_text = f"Kshama karein, Instagram par '{ig_user}' ko message nahi bheja ja saka: {err_msg}"
                else:
                    reply_text = f"Instagram par '{ig_user}' ko message bhej diya gaya hai: '{ig_msg}'"
                return {"reply": reply_text, "action": act_res}

        elif act_type == "search_instagram_user":
            q_user = action_data.get("query", "")
            act_res = dispatch_action_safe("search_instagram_user", {"query": q_user})
            return {"reply": act_res.get("message", f"Instagram par '{q_user}' search kar diya hai."), "action": act_res}

        elif act_type == "search_web_for_answer":
            q_web = action_data.get("query", "")
            act_res = actions.search_web_for_answer(query=q_web)
            if act_res.get("success"):
                reply_text = act_res.get("results", "Web search results retrieved.")
            else:
                reply_text = f"Kshama karein, search nahi ho saka: {act_res.get('error', 'Error')}"
            return {"reply": reply_text, "action": act_res}

        elif act_type in ["open_website", "open_url", "open_or_search_website"]:
            site = action_data.get("website") or action_data.get("url") or "youtube"
            query = action_data.get("search_query", "")
            clean_site = "whatsapp" if "whatsapp" in str(site).lower() else str(site)
            site_display = "YouTube" if clean_site.lower() in ["youtube", "yt"] else clean_site.capitalize()
            act_res = dispatch_action_safe("open_website", {"website": clean_site, "search_query": query})
            reply_text = f"{site_display} open kar diya hai." if act_res.get("success") else f"Kshama karein, {site_display} open nahi ho saka: {act_res.get('message', 'Error')}"
            return {"reply": reply_text, "action": act_res}

        elif act_type in ["open_app", "open_application"]:
            app = action_data.get("app_name", "") or action_data.get("app", "")
            act_res = dispatch_action_safe("open_app", {"app_name": app})
            reply_text = f"{app.capitalize()} open kar diya hai." if act_res.get("success") else f"Kshama karein, '{app.capitalize()}' open nahi ho saka: {act_res.get('message', 'Application nahi mila.')}"
            return {"reply": reply_text, "action": act_res}

        elif act_type:
            p = {k: v for k, v in action_data.items() if k != "action"}
            act_res = dispatch_action_safe(act_type, p)
            return {"reply": clean_response.strip(), "action": act_res}

        # If Gemini AFC already executed a tool, return immediately (never fall through to fallback regexes)
        tools_called_in_afc = get_session_tool_call_count(session_id)
        if tools_called_in_afc > 0:
            return {
                "reply": clean_response.strip(),
                "action": {"status": "executed", "tools_called": tools_called_in_afc}
            }

        # 3. Intent & Execution Verification Safeguard:
        # If user asked to open WhatsApp/web or AI claimed WhatsApp was opened without invoking a tool,
        # guarantee that the browser window is physically opened right now.
        user_lower = user_text.lower().strip()
        resp_lower = clean_response.lower().strip()

        is_whatsapp_open = (
            "whatsapp" in user_lower
            and any(kw in user_lower for kw in ["open", "kholo", "chalao", "start", "launch", "web"])
            and not any(kw in user_lower for kw in ["send", "bhej", "chat", "msg", "message", "reply"])
        )
        ai_claims_whatsapp_open = "whatsapp" in resp_lower and any(
            p in resp_lower for p in ["open kar diya", "khol diya", "opened", "launch kar diya", "open kar raha hoon"]
        )

        if is_whatsapp_open or (ai_claims_whatsapp_open and not any(kw in user_lower for kw in ["send", "bhej", "chat"])):
            act_res = dispatch_action_safe("open_website", {"website": "whatsapp"})
            if not act_res.get("success"):
                reply_text = f"Kshama karein, WhatsApp open nahi ho saka: {act_res.get('message', 'Error')}"
            else:
                reply_text = clean_response if (clean_response and "khol" not in clean_response.lower()) else "WhatsApp Web open kar diya hai!"
            return {
                "reply": reply_text.strip(),
                "action": act_res
            }

        # Safeguard: If Gemini AFC already executed a tool, return immediately and NEVER hit fallback regex interceptors
        tools_called_in_afc = get_session_tool_call_count(session_id)
        if tools_called_in_afc > 0:
            return {
                "reply": clean_response.strip(),
                "action": {"status": "executed", "tools_called": tools_called_in_afc}
            }

        # 4. General "open/kholo" Failsafe Interceptor (only runs if Gemini AFC did not invoke any tools)
        target_name = None
        if not any(kw in user_lower for kw in ["send", "bhej", "chat", "msg", "message", "reply", "kuch", "koi"]) and not is_whatsapp_open:
            m1 = re.match(r'^(?:open|launch|kholo|start)\s+(?:app|the\s+app)?\s*([a-zA-Z0-9_\s]+)$', user_lower, re.IGNORECASE)
            m2 = re.match(r'^([a-zA-Z0-9_\s]+?)\s+(?:open|kholo|chalao|launch)(?:\s+karo|\s+do|\s+kar\s+do)?$', user_lower, re.IGNORECASE)
            if m1:
                target_name = m1.group(1).strip()
            elif m2:
                target_name = m2.group(1).strip()

            if target_name:
                for filler in ["please", "zara", "jaldi", "yaar", "bhai", "karo", "do", "kar do", "app", "the"]:
                    target_name = re.sub(rf'\b{filler}\b', '', target_name, flags=re.IGNORECASE).strip()

                if target_name in actions.COMMON_SITES or any(k == target_name for k in actions.COMMON_SITES):
                    site_key = next((k for k in actions.COMMON_SITES if k == target_name), target_name)
                    act_res = dispatch_action_safe("open_website", {"website": site_key})
                    if not act_res.get("success"):
                        reply_text = f"Kshama karein, {site_key.capitalize()} open nahi ho saka: {act_res.get('message', 'Error')}"
                    else:
                        reply_text = f"{site_key.capitalize()} open kar diya hai."
                    return {
                        "reply": reply_text,
                        "action": act_res
                    }
                elif target_name in actions.COMMON_APP_MAP or any(k == target_name for k in actions.COMMON_APP_MAP):
                    app_key = next((k for k in actions.COMMON_APP_MAP if k == target_name), target_name)
                    act_res = dispatch_action_safe("open_app", {"app_name": app_key})
                    if not act_res.get("success"):
                        reply_text = f"Kshama karein, '{app_key.capitalize()}' open nahi ho saka: {act_res.get('message', 'Error')}"
                    else:
                        reply_text = f"{app_key.capitalize()} open kar diya hai."
                    return {
                        "reply": reply_text,
                        "action": act_res
                    }

        reply_text = clean_response

        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            "Processed voice command via Gemini AFC",
            extra={
                "session_id": session_id,
                "latency_ms": latency_ms,
                "mode": "gemini_afc",
                "success": True
            }
        )

        return {
            "reply": reply_text.strip(),
            "action": {"status": "executed"}
        }

    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.warning(
            f"Gemini API error ({repr(e)}). Utilizing fallback engine with memory.",
            extra={
                "session_id": session_id,
                "latency_ms": latency_ms,
                "mode": "fallback_recovery",
                "error": str(e),
                "success": True
            }
        )
        try:
            fallback = await asyncio.to_thread(fallback_intent_parser, user_text, session_id=session_id)
            return fallback
        except Exception as fb_err:
            logger.error(f"Fallback recovery error: {fb_err}")
            return {
                "reply": "Kshama karein, main abhi yeh command process nahi kar pa raha hoon. Kripya dobara koshish karein.",
                "action": {"status": "error", "error": str(fb_err)}
            }


# Backward compatibility alias
process_command = process_voice_command
