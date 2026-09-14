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
from contextvars import ContextVar
from typing import Dict, Any, Optional, List, Callable, Sequence
from pathlib import Path
from dotenv import load_dotenv
import warnings

# Suppress harmless Python 3.14 deprecation warnings & Google SDK advisory notices
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*automatic function calling.*")

# Ensure .env is loaded
from config import GEMINI_API_KEY
from core import actions
from core.logger import get_logger, redact_params
from core.executor_bridge import dispatch_pc_tool_sync, dispatch_pc_tool_async, executor_bridge

logger = get_logger("astra.brain")

# --- ASTRA UPGRADE: MEM0 & MCP IMPORTS START ---
from core.memory import search_relevant_memories, add_interaction_memory_async
from core.mcp_client import discover_mcp_tools, get_dynamic_mcp_tools_sync
# --- ASTRA UPGRADE: MEM0 & MCP IMPORTS END ---
from core.command_router import (
    CommandRouter,
    extract_whatsapp_parameters,
    transliterate_indic_command,
    CONJUNCTIONS,
    NON_CONTACT_WORDS
)


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

SYSTEM_INSTRUCTION = f"""You are Astra, an intelligent English-speaking AI voice assistant for PC and mobile devices, companion to {_BOSS_NAME} ('{_CALL_NAME}'), who is a {_ROLE} based in {_CITY}.

CRITICAL RESPONSE RULES:

1. NEVER use single-word replies like "Okay", "Done", "Yes". Always respond with complete, conversational sentences (minimum 15-25 words).
   - ❌ BAD: "Done." / "Okay."
   - ✅ GOOD: "I have opened Notepad for you, {_CALL_NAME}. Is there anything else you'd like to work on?"
   - ✅ GOOD: "Sure thing! Launching Chrome browser right away for you."
   - ✅ GOOD: "I have gathered the latest updates for you, {_CALL_NAME}. Here are the details..."

2. When executing any tool/action, ALWAYS provide a detailed confirmation:
   - ❌ BAD: "Notepad opened."
   - ✅ GOOD: "I have successfully opened Notepad for you. You can now start typing your notes. Is there anything else you'd like me to help you with?"

3. For incomplete or unclear voice commands (common on mobile), politely ask for clarification:
   - User says: "Notepad"
   - You respond: "I heard you say 'Notepad'. Would you like me to open Notepad application, or did you want to do something else with it?"

4. Language Requirement:
   - Always respond in natural, crisp, fluent English.
   - Even if the user uses Hindi or Hinglish keywords, speak and reply in clear, natural English so all responses are consistent and easy to follow.

5. When tools fail, explain what went wrong and offer alternatives:
   - ❌ BAD: "Error."
   - ✅ GOOD: "I'm sorry, I couldn't open that application. It seems like it's not installed on your system. Would you like me to help you find an alternative?"

6. Be warm and conversational. Use phrases like:
   - "Certainly!", "Of course!", "I'd be happy to help!", "Right away!"

7. Current Context:
   - Current Date/Time: {{current_datetime}}
   - User's Device: {{user_device}}
   - Assistant Name: Astra

Dual Nature:
1. Intelligence: You have the deep knowledge and clarity of ChatGPT/Gemini. Give direct, insightful, natural answers to conceptual, coding, and life questions.
2. PC Automation DNA: You literally control this Windows PC. Automation is in your blood. When asked to perform an action on the PC (open apps, run CMD commands, play music, send messages, change volume), EXECUTE IMMEDIATELY.

The "Just Do It" Rule (Critical):
- NEVER ask permission. If {_CALL_NAME} says "Open Notepad" or "Notepad kholo", do NOT reply "Shall I open Notepad?". Execute the tool immediately and confirm crisply: "I have opened Notepad for you, {_CALL_NAME}!"
- For compound requests, call the required tools, gather the real outputs, and provide a single seamless, natural answer.

Humanized Error Handling:
- If a command or tool fails, NEVER output raw stack traces or "Error 404".
- Speak humanly in English: "I was unable to execute that command, please verify the syntax or parameters." or "Could not reach the website, please check your network connection {_CALL_NAME}."

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
- When a user requests to play a song or video on YouTube or YT (e.g., 'open yt and play specialz', 'play specialz on yt', 'play lofi beats on youtube'), call the play_youtube_video tool with query=<search_query>. If the user gives no specific topic (e.g. 'play yt', 'open yt', 'play video'), call open_or_search_website(website='youtube').

Spotify Automation Instructions:
- When a user requests to play a song, music, or playlist on Spotify (e.g., 'open spotify and play music', 'play relax songs on spotify', 'spotify pe lofi playlist chalao', 'play playlist on spotify', 'play music'), call the play_spotify_music tool with query=<search_query>. If the user gives no specific topic or asks generally to play music/spotify, call play_spotify_music(query='').

Instagram Automation Instructions:
- When a user requests to search an Instagram user, ID, profile, or account (e.g., 'open instagram and search id shivam', 'search shivam id', 'you search shivam id', 'instagram pe virat search karo', 'search rohit on instagram', 'shivam ki id search karo'), call the search_instagram_user tool with query=<username>. Never tell the user to manually type it into the search bar.

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
- Strict Website & App Execution: When a user asks to open a website or application (e.g., WhatsApp Web, YouTube, Chrome, Notepad), you must strictly use the designated tool/function call (open_or_search_website or open_application) to execute the action. Do not generate a conversational success response until you receive confirmation from the system that the tool was executed successfully.
- Never falsely claim to have opened a website, app, sent a message, or performed a system action. You must trigger the backend command first and base your verbal response only on the actual execution result.
- Never use bullet points, numbered lists, markdown symbols (asterisks, hashes, backticks), or code snippets in spoken replies. Formulate full, flowing conversational sentences suitable for human speech synthesis.

Human-Like Reasoning & Problem-Solving Protocol (Mandatory):
1. UNDERSTAND FIRST: Read the request fully. If ambiguous, pick the most reasonable interpretation and execute it directly. Only ask a clarifying question if answering wrong would waste real effort. Never answer a different question than asked.
2. THINK BEFORE SPEAKING: Break problems down silently: known -> what is asked -> steps -> cleanest answer. Never dump raw chain-of-thought, scratch work, or "let me think..." filler into spoken/chat replies. Deliver the final crisp answer like a sharp human expert who already thought it through.
3. HONESTY OVER CONFIDENCE-THEATER: If unsure, say so plainly ("Pakka nahi pata, but yeh possibility hai...") instead of guessing. If a fact might have changed or needs current data, use search_web_for_answer. Never fabricate numbers, names, sources, or command outputs.
4. ANSWER LIKE A SHARP HUMAN, NOT A MANUAL: Simple question -> 1-2 line direct answer. Complex/technical question -> structured but concise explanation. Use analogies/examples when helpful. Skip disclaimers, filler, and corporate hedging ("As an AI...", "It depends...").
5. CONTEXT & MEMORY AWARENESS: Track earlier turns in the session. Resolve pronouns ("isko", "wahi wala", "usme") using recent context before asking for clarification. Accept corrections naturally without over-apologizing.
6. DECISION-MAKING UNDER AMBIGUITY: Default to the interpretation that gets the user their actual answer fastest. When genuinely stuck between two valid paths, briefly state the assumption rather than stalling.
7. EMOTIONAL CALIBRATION: Casual chat -> casual, witty reply. Serious/urgent/technical -> drop the wit, be precise and fast. Never joke or be sarcastic when the user is frustrated, worried, or discussing sensitive topics (health, money, errors).
8. SELF-CORRECTION: If you realize mid-answer that your first instinct was wrong or incomplete, correct it immediately in the same response.

CRITICAL EXECUTION RULES (MANDATORY):
1. NEVER describe what a tool does. EXECUTE IT. If the user says "news batao", do NOT say "You can check News24" or "Main search kar sakta hoon". Call the tool immediately and read its output.
2. SEARCH QUERIES: When the user asks for news, weather, facts, or any real-time information, you MUST call 'search_web_for_answer' with a specific English query. Then READ the returned text and summarize the top 3 results in the user's language.
3. NO EMPTY CONFIRMATIONS: Never respond with just "Ready hoon" or "Bataiye kya karna hai" when the user has already given a clear command. If the user says "Notepad kholo", open it. Do not ask "Kya main khol doon?"
4. TOOL OUTPUT HANDLING: After calling a tool, you will receive its output as text. You MUST incorporate that real data into your final spoken reply. Never ignore tool output. Never fabricate data that was not in the tool output.
5. ERROR RESPONSES: If a tool fails or returns empty results, say exactly what happened in natural Hinglish. Example: "Arre yaar, search result nahi mila, internet check kar lo." Do NOT fall back to generic advice.
6. RESPONSE FORMAT FOR NEWS/SEARCH:
   - User asks: "Aaj ki news kya hai?"
   - You call: search_web_for_answer(query="latest news India today")
   - You receive: "1. Headline A: description... 2. Headline B: description..."
   - You speak: "Aaj ki top 3 khabrein: 1. [Real Headline A], 2. [Real Headline B], 3. [Real Headline C]."
   - NEVER say: "Aap News24 ya Google News par dekh sakte hain."
7. TONE ADAPTATION:
   - Casual greeting -> warm, confident, 1 sentence.
   - Serious/technical/urgent request -> drop the wit, be precise and fast.
   - Never be sarcastic when the user sounds frustrated or is asking about something sensitive.
8. SELF-CORRECTION: If you realize mid-answer that your first instinct was wrong or incomplete, correct it immediately in the same response.
"""

def get_effective_system_instruction(user_text: str = "", session_id: str = "") -> str:
    """Dynamically injects current date/time and detected device into system instruction."""
    from datetime import datetime
    now_str = datetime.now().strftime("%A, %d %B %Y at %I:%M %p")
    session_device = session_manager.get_user_data(session_id, "device") if session_id else None
    if session_device:
        device_type = session_device
    elif any(x in (user_text or "").lower() for x in ["mobile", "phone", "android", "iphone"]):
        device_type = "Mobile (Smartphone)"
    else:
        device_type = "PC / Laptop"

    res = SYSTEM_INSTRUCTION
    res = res.replace("{{current_datetime}}", now_str).replace("{current_datetime}", now_str)
    res = res.replace("{{user_device}}", device_type).replace("{user_device}", device_type)
    return res

# Active session tracker & AFC Tool Call Inspection Counter (ContextVar for async/concurrency isolation)
_active_session_id_var: ContextVar[str] = ContextVar("active_session_id", default="default")
_active_session_id = "default"
_TOOL_CALL_COUNTS: Dict[str, Dict[str, int]] = {}
_TOOL_CALL_LOCK = threading.Lock()

# Per-turn Tool Call Deduplication Set (Prevents duplicate executions from LLM hallucination)
_EXECUTED_ACTIONS: Dict[str, set] = {}
_EXECUTED_ACTIONS_LOCK = threading.Lock()

def get_active_session() -> str:
    """Returns the active session ID for the current context/task."""
    try:
        return _active_session_id_var.get()
    except LookupError:
        return _active_session_id

def set_active_session(session_id: str):
    """Sets the active session ID for the current context/task."""
    global _active_session_id
    _active_session_id = session_id
    _active_session_id_var.set(session_id)

def _log_tool_invocation(tool_name: str, args: Dict[str, Any]) -> int:
    """Logs tool call with session-specific counter for Gemini AFC invocation inspection."""
    active_id = get_active_session()
    with _TOOL_CALL_LOCK:
        sess_counts = _TOOL_CALL_COUNTS.setdefault(active_id, {})
        current_count = sess_counts.get(tool_name, 0) + 1
        sess_counts[tool_name] = current_count
    logger.info(f"[Gemini AFC Tool Call] Tool: '{tool_name}' | Count: {current_count} in session '{active_id}' | Args: {args}")
    print(f"[Gemini AFC Tool Call] Tool: '{tool_name}' (call #{current_count} for session '{active_id}') args={args}")
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
    active_id = get_active_session()
    # Tool call deduplication: ensure exact function + argument pair runs at most once per turn
    if check_and_record_executed_action(active_id, tool_name, p):
        logger.warning(
            f"[Tool Call Deduplication] Suppressed duplicate action: '{tool_name}' with params {p} in session '{active_id}'"
        )
        return {
            "success": True,
            "status": "duplicate_suppressed",
            "action": tool_name,
            "duplicate_suppressed": True,
            "message": f"Action '{tool_name}' already executed in this turn."
        }

    if tool_name == "send_whatsapp_message" and "is_mobile" not in p:
        ctx_mobile = session_manager.get_user_data(active_id, "is_mobile")
        if ctx_mobile is not None:
            p["is_mobile"] = bool(ctx_mobile)
        else:
            device_ctx = (session_manager.get_user_data(active_id, "device") or "").lower()
            if "mobile" in device_ctx or "smartphone" in device_ctx:
                p["is_mobile"] = True

    try:
        res = dispatch_pc_tool_sync(tool_name, p)
        if res.get("success"):
            return res
        if not res.get("offline") and not res.get("timeout"):
            return res
    except Exception:
        pass

    func = getattr(actions, tool_name, None)
    if func:
        try:
            import inspect
            sig = inspect.signature(func)
            has_var_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values())
            call_params = p if has_var_kwargs else {k: v for k, v in p.items() if k in sig.parameters}
            return func(**call_params)
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
    session_manager.record_last_action(get_active_session(), clean)
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

def send_whatsapp_message(
    phone_number: str = "",
    message: str = "",
    is_mobile: bool = False,
    contact_name: str = "",
    phone: str = "",
    **kwargs
) -> str:
    """Send a WhatsApp message to a phone number or contact name (platform-aware for mobile and desktop)."""
    clean_target = (phone_number or phone or contact_name or "").strip()
    clean_msg = message.strip()

    # If is_mobile is False, resolve platform from session context if available
    effective_is_mobile = is_mobile
    if not effective_is_mobile:
        active_id = get_active_session()
        ctx_mobile = session_manager.get_user_data(active_id, "is_mobile")
        if ctx_mobile:
            effective_is_mobile = True
        else:
            device_ctx = (session_manager.get_user_data(active_id, "device") or "").lower()
            if "mobile" in device_ctx or "smartphone" in device_ctx:
                effective_is_mobile = True

    _log_tool_invocation("send_whatsapp_message", {
        "phone_number": clean_target,
        "message": clean_msg,
        "is_mobile": effective_is_mobile
    })
    if clean_target:
        if clean_target.lower() in CONJUNCTIONS or clean_target.lower() in NON_CONTACT_WORDS:
            return f"Failed to send WhatsApp message: '{clean_target}' is a conjunction or invalid contact name."
        res = dispatch_action_safe("send_whatsapp_message", {
            "phone_number": clean_target,
            "message": clean_msg,
            "is_mobile": effective_is_mobile,
            "contact_name": clean_target,
            "phone": clean_target
        })
        if not res.get("success"):
            return f"Failed to send WhatsApp message to '{clean_target}': {res.get('message', 'Error')}"
        return res.get("message", f"WhatsApp message to '{clean_target}' processed.")
    res = actions.send_whatsapp_message(phone_number="", message=clean_msg, is_mobile=effective_is_mobile)
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
    send_whatsapp_message,
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


def execute_live_tool_call(tool_name: str, args: Optional[Dict[str, Any]] = None, session_id: str = "default") -> Dict[str, Any]:
    """
    Executes a function call requested by the Gemini Live API session.
    Routes through TOOL_MAP / dispatch_action_safe while logging and tracking counts.
    """
    set_active_session(session_id)
    clean_name = tool_name.strip() if tool_name else ""
    clean_args = dict(args) if args else {}

    if clean_name == "send_whatsapp_message" and "is_mobile" not in clean_args:
        ctx_mobile = session_manager.get_user_data(session_id, "is_mobile")
        if ctx_mobile is not None:
            clean_args["is_mobile"] = bool(ctx_mobile)
        else:
            device_ctx = (session_manager.get_user_data(session_id, "device") or "").lower()
            if "mobile" in device_ctx or "smartphone" in device_ctx:
                clean_args["is_mobile"] = True

    logger.info(f"[Gemini Live Tool Call] Executing '{clean_name}' with args {clean_args} in session '{session_id}'")

    func = TOOL_MAP.get(clean_name)
    if func:
        try:
            raw_res = func(**clean_args)
            if isinstance(raw_res, dict):
                return raw_res
            return {
                "success": True,
                "status": "executed",
                "tool": clean_name,
                "action": clean_name,
                "message": str(raw_res)
            }
        except Exception as e:
            logger.error(f"[Gemini Live Tool Call] Error executing '{clean_name}': {e}")
            return {"success": False, "status": "error", "tool": clean_name, "error": str(e)}

    # Fallback to dispatch_action_safe for dynamic/MCP tools
    try:
        res = dispatch_action_safe(clean_name, clean_args)
        return res
    except Exception as e:
        logger.error(f"[Gemini Live Tool Call] Fallback dispatch error for '{clean_name}': {e}")
        return {"success": False, "status": "error", "tool": clean_name, "error": str(e)}


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
    if not user_text or not isinstance(user_text, str) or not user_text.strip():
        return {"reply": "Aapki aawaz nahi sunai di, kripya dobara bolein.", "action": None}

    user_text_norm = transliterate_indic_command(user_text)
    text = user_text_norm.lower().strip()

    # Multi-Turn WhatsApp Memory: User previously gave contact name, now providing message content
    pending_whatsapp = session_manager.get_pending_whatsapp(session_id)
    if pending_whatsapp and (pending_whatsapp.lower() in CONJUNCTIONS or pending_whatsapp.lower() in NON_CONTACT_WORDS):
        session_manager.clear_pending_whatsapp(session_id)
        pending_whatsapp = None

    if pending_whatsapp:
        if any(k in text for k in ["cancel", "mat bhejo", "stop", "rehne do", "chodo", "no"]):
            session_manager.clear_pending_whatsapp(session_id)
            return {
                "reply": f"WhatsApp message to '{pending_whatsapp}' cancel kar diya gaya hai.",
                "action": {"status": "cancelled", "contact_name": pending_whatsapp}
            }

        routed_check = CommandRouter.route_command(user_text)
        if routed_check.get("intent") in ["whatsapp_message", "whatsapp_clarification_needed", "get_whatsapp_unread", "open_whatsapp", "open_settings"]:
            session_manager.clear_pending_whatsapp(session_id)
            pending_whatsapp = None
        else:
            session_manager.clear_pending_whatsapp(session_id)
            res = actions.send_whatsapp_message(contact_name=pending_whatsapp, message=user_text)
            return {
                "reply": f"WhatsApp par '{pending_whatsapp}' ko message bhej diya gaya hai: '{user_text}'.",
                "action": res
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
            "reply": "Hello! I am Astra, your desktop AI voice assistant. How can I help you today?",
            "action": {"status": "conversation", "intent": "greeting"}
        }

    if re.search(r'\b(kaise ho|kaisa hai|how are you|kya haal|kya chal raha|sab theek|sab kaisa|whats up|what\'s up)\b', text) and not any(k in text for k in ["screen", "display", "bhejo", "send", "message", "msg", "dm", "say", "saying", "bolo", "likho"]):
        return {
            "reply": "I am doing great and ready to assist! How can I help you on your laptop today?",
            "action": {"status": "conversation", "intent": "smalltalk_status"}
        }

    if re.search(r'\b(who are you|kaun ho|tum kaun ho|aap kaun ho|tera naam kya|tumhara naam kya|introduce yourself|what is your name)\b', text):
        return {
            "reply": "My name is Astra! I am your intelligent desktop voice assistant. I can open applications, play music, search the web, analyze your screen, and control your PC.",
            "action": {"status": "conversation", "intent": "identity"}
        }

    # Multi-turn user identity & preferences memory
    if re.search(r'(?:mera naam kya hai|what is my name|who am i)', text, re.I):
        saved_name = session_manager.get_user_data(session_id, "name")
        if saved_name:
            return {"reply": f"Your name is {saved_name}.", "action": None}
        return {"reply": "I don't know your name yet. Please tell me your name.", "action": None}

    m_name = re.search(r'(?:mera naam|my name is)\s+([a-zA-Z\s]+?)(?:\s+(?:hai|rakho|note karo)|$)', text, re.I)
    if m_name:
        extracted_name = m_name.group(1).strip()
        if extracted_name.lower() not in ["kya", "kaunsa", "what", "unknown"]:
            session_manager.set_user_data(session_id, "name", extracted_name)
            return {"reply": f"Hello {extracted_name}! I have remembered your name.", "action": None}

    m_fav_q = re.search(r'(?:mera favorite|what is my favorite)\s+([a-zA-Z]+)(?:\s+kya hai|\?)?', text, re.I)
    if m_fav_q:
        attr = m_fav_q.group(1).lower().strip()
        saved_val = session_manager.get_user_data(session_id, f"fav_{attr}")
        if saved_val:
            return {"reply": f"Your favorite {attr} is {saved_val}.", "action": None}
        return {"reply": f"I don't know your favorite {attr} yet.", "action": None}

    m_fav = re.search(r'(?:mera favorite|my favorite)\s+([a-zA-Z]+)\s+(?:is\s+)?([a-zA-Z0-9_\s]+?)(?:\s+hai|$)', text, re.I)
    if m_fav:
        attr = m_fav.group(1).lower().strip()
        val = m_fav.group(2).strip()
        if val.lower() not in ["kya", "kaunsa", "kon", "what", "which"]:
            session_manager.set_user_data(session_id, f"fav_{attr}", val)
            return {"reply": f"I have saved that your favorite {attr} is '{val}'.", "action": None}

    if re.search(r'\b(kya kar sakte ho|kya kya kar sakte|what can you do|features|madad|capabilities|help|kya aata hai)\b', text):
        return {
            "reply": "I can open and close applications on your laptop, analyze your screen, play music on Spotify and YouTube, control volume, search the web, and automate PC tasks.",
            "action": {"status": "conversation", "intent": "help"}
        }

    if re.search(r'\b(thank you|thanks|dhanyawad|shukriya|bahut accha|great job|shabash|badhiya)\b', text):
        return {
            "reply": "You are most welcome! Always ready to assist. Let me know if you need anything else.",
            "action": {"status": "conversation", "intent": "gratitude"}
        }

    if re.search(r'\b(are you there|sun rahe ho|meri aawaz|can you hear me|zinda ho)\b', text):
        return {
            "reply": "Yes, I am right here and listening! What would you like me to do?",
            "action": {"status": "conversation", "intent": "presence"}
        }

    if re.search(r'\b(bye|goodbye|alvida|tata|see you|phir milte)\b', text):
        return {
            "reply": "Goodbye! Whenever you need anything, just let me know.",
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
    if re.search(r'\b(time|samay|baje|date|tareekh|din|aaj)\b', text) and not any(k in text for k in ["search", "dhoondo", "release date", "expiry", "birth"]):
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
        query = text
        for w in ["google", "search", "dhoondo", "find", "kholo", "open", "chalao", "start", "launch", "karo", "do", "pe", "par", "me", "mein", "please"]:
            query = re.sub(rf'\b{w}\b', '', query, flags=re.IGNORECASE).strip()
        query = re.sub(r'\s+', ' ', query).strip()
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

    # 14. Priority 1 & 2: WhatsApp (Terminal Route) & Windows Settings
    routed = CommandRouter.route_command(user_text)

    if routed.get("intent") == "get_whatsapp_unread":
        res = actions.get_whatsapp_unread(max_chats=5)
        return {"reply": res.get("message", "WhatsApp unread checked."), "action": res}

    elif routed.get("intent") == "open_whatsapp":
        if executor_bridge.is_connected():
            res = dispatch_pc_tool_sync("open_website", {"website": "whatsapp"})
        else:
            res = actions.open_website("whatsapp")
        return {"reply": "WhatsApp Web open kar diya hai!", "action": res}

    elif routed.get("intent") == "whatsapp_message":
        target_contact = routed.get("contact")
        msg_content = routed.get("message")
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

    elif routed.get("intent") == "whatsapp_clarification_needed":
        target_contact = routed.get("contact")
        session_manager.record_pending_whatsapp(session_id, target_contact)
        return {
            "reply": f"What message would you like to send to {target_contact}?",
            "action": {"status": "clarification_needed", "contact_name": target_contact}
        }

    elif routed.get("intent") == "whatsapp_contact_clarification_needed":
        return {
            "reply": "Who would you like to message on WhatsApp?",
            "action": {"status": "clarification_needed", "platform": "whatsapp"}
        }

    elif routed.get("intent") == "open_settings":
        target = routed.get("setting_target", "settings")
        session_manager.record_last_action(session_id, target)
        if executor_bridge.is_connected():
            res = dispatch_pc_tool_sync("open_app", {"app_name": target})
        else:
            res = actions.open_app(target)
        if res.get("offline"):
            return {
                "reply": f"Kshama karein, laptop executor offline hai: '{target.capitalize()}' open nahi ho saka. Kripya apne laptop par local_executor.py start karein.",
                "action": res
            }
        elif res.get("timeout"):
            return {
                "reply": f"Kshama karein, laptop executor timed out: '{target.capitalize()}' open nahi ho saka.",
                "action": res
            }
        return {"reply": f"{target.capitalize()} open kar diya hai.", "action": res}


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

    # Helper to format clean, high-naturalness response for speech synthesis
    def _format_search_reply(user_query: str, raw_results: str) -> str:
        if not raw_results:
            return "Web search results: No details found for this query."
        is_news = any(k in user_query.lower() for k in ["headline", "headlines", "news", "khabar", "khabrein", "updates", "today", "aaj"])
        titles = re.findall(r'\[\d+\]\s*(.*?)(?:\s*\(Source:.*?\)|$)', raw_results)
        if is_news and titles:
            top_headlines = [re.sub(r'\s*\|\s*.*$', '', t).strip() for t in titles[:3]]
            formatted = ', '.join([f'{i+1}. {t}' for i, t in enumerate(top_headlines)])
            return f"Web search results - Today's top headlines: {formatted}."
        
        m_sum = re.search(r'Summary:\s*(.*?)(?:\n\[\d+\]|$)', raw_results, re.DOTALL)
        if m_sum:
            clean_snippet = m_sum.group(1).strip()
            sentences = re.split(r'(?<=[.!?])\s+', clean_snippet)
            top_sentences = " ".join(sentences[:2]).strip()
            return f"Web search results: {top_sentences or clean_snippet[:250]}"
        return f"Web search results:\n{raw_results[:300]}"

    # 17. Real-Time Web Search & Q&A
    if routed.get("intent") == "web_search":
        q_srch = routed.get("query", "").strip()
        if q_srch:
            res = actions.search_web_for_answer(q_srch)
            if res.get("success"):
                summary_text = res.get("results", "")
                return {"reply": _format_search_reply(user_text, summary_text), "action": res}
            return {"reply": f"Web search could not be completed: {res.get('error', 'Error')}", "action": None}

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
                return {"reply": _format_search_reply(user_text, summary_text), "action": res}
            return {"reply": f"Web search could not be completed: {res.get('error', 'Error')}", "action": None}

    # Catch-all for open-ended inquiries/questions: Execute real-time web search instead of canned refusal
    clean_inquiry = re.sub(r'^(?:please|zara|yaar|bhai)\s+', '', text, flags=re.I).strip()
    words = clean_inquiry.split()
    conversational_fillers = {"okay", "ok", "theek", "theek hai", "accha", "shukriya", "thanks", "thank you", "bye", "alvida", "chalo"}
    if len(words) >= 2 and clean_inquiry.lower() not in conversational_fillers:
        try:
            res = actions.search_web_for_answer(clean_inquiry)
            if res.get("success") and res.get("results"):
                summary_text = res.get("results", "")
                return {"reply": _format_search_reply(user_text, summary_text), "action": res}
        except Exception as search_err:
            logger.debug(f"[Fallback] Catch-all web search skipped: {search_err}")

    return {
        "reply": "I'm listening! How can I help you today? You can ask me to open apps, search the web, play music, or check your screen.",
        "action": None
    }


def fallback_intent_parser(user_text: str, session_id: str = "default") -> Dict[str, Any]:
    """
    Offline/Fallback Rule-Based Natural Language Processor with Session Memory.
    Guaranteed to catch unexpected exceptions and return a safe generic response.
    """
    if not user_text or not isinstance(user_text, str) or not user_text.strip():
        return {"reply": "I could not hear your voice clearly. Please speak again.", "action": None}
    try:
        return _parse_fallback_intent(user_text, session_id=session_id)
    except Exception as e:
        logger.error(f"Fallback intent parser error while processing '{user_text}': {e}")
        return {
            "reply": "Sorry, I encountered an issue executing this command. Please try again.",
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


_openrouter_circuit_broken_until: float = 0.0
_openrouter_cooldown_reason: str = ""

def is_openrouter_available() -> bool:
    """Checks if OpenRouter is configured and not currently rate-limited by circuit breaker."""
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key or key == "your_openrouter_api_key_here":
        return False
    if time.time() < _openrouter_circuit_broken_until:
        return False
    return True

def trip_openrouter_circuit_breaker(error_msg: str, duration: float = 3600.0):
    """Trips circuit breaker to avoid repeated 429 delays and console spam."""
    global _openrouter_circuit_broken_until, _openrouter_cooldown_reason
    _openrouter_circuit_broken_until = time.time() + duration
    _openrouter_cooldown_reason = error_msg
    logger.warning(
        f"[Brain] OpenRouter circuit breaker tripped for {int(duration)}s ({error_msg}). "
        f"All subsequent queries will route directly to Gemini without delay."
    )

def reset_openrouter_circuit_breaker():
    """Resets circuit breaker status."""
    global _openrouter_circuit_broken_until, _openrouter_cooldown_reason
    _openrouter_circuit_broken_until = 0.0
    _openrouter_cooldown_reason = ""


def is_openrouter_circuit_broken() -> bool:
    """Returns True if OpenRouter is actively rate-limited/circuit-broken."""
    return time.time() < _openrouter_circuit_broken_until


_gemini_circuit_broken_until: float = 0.0
_gemini_cooldown_reason: str = ""

def is_gemini_circuit_broken() -> bool:
    """Returns True if Gemini is actively rate-limited/circuit-broken due to 429 quota exhaustion."""
    return time.time() < _gemini_circuit_broken_until

def is_gemini_available() -> bool:
    """Checks if Gemini is configured and not currently rate-limited by circuit breaker."""
    env_gemini_key = os.getenv("GEMINI_API_KEY")
    api_key = env_gemini_key.strip() if env_gemini_key is not None else GEMINI_API_KEY
    if not api_key or api_key == "your_gemini_api_key_here":
        return False
    if is_gemini_circuit_broken():
        return False
    return True

def trip_gemini_circuit_breaker(error_msg: str, duration: float = 30.0):
    """Trips Gemini circuit breaker to avoid repeated 429 quota delays and timeouts."""
    global _gemini_circuit_broken_until, _gemini_cooldown_reason
    err_str = str(error_msg)
    if "free_tier_requests" in err_str or "limit: 20" in err_str or "quota exceeded" in err_str.lower():
        duration = max(duration, 300.0)
    # Extract retryDelay if provided by Google API, e.g. "'retryDelay': '8s'"
    delay_match = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s?", str(error_msg), re.I)
    delay_match = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s?", err_str, re.I)
    if delay_match:
        try:
            parsed_delay = float(delay_match.group(1))
            duration = max(duration, parsed_delay + 2.0)
        except Exception:
            pass
    _gemini_circuit_broken_until = time.time() + duration
    _gemini_cooldown_reason = str(error_msg)
    _gemini_cooldown_reason = err_str
    logger.warning(
        f"[Brain] Gemini circuit breaker tripped for {int(duration)}s ({str(error_msg)[:120]}). "
        f"Subsequent queries will route directly to fast fallback/OpenRouter without delay."
    )

def reset_gemini_circuit_breaker():
    """Resets Gemini circuit breaker status."""
    global _gemini_circuit_broken_until, _gemini_cooldown_reason
    _gemini_circuit_broken_until = 0.0
    _gemini_cooldown_reason = ""


def get_ai_provider_status() -> Dict[str, Any]:
    """
    Returns the real-time operational status of all configured AI providers (Gemini & OpenRouter).
    Detects if system is running in degraded offline mode due to quota exhaustion or circuit breaks.
    """
    gemini_ok = is_gemini_available()
    openrouter_ok = is_openrouter_available()
    degraded = (not gemini_ok) and (not openrouter_ok)
    return {
        "status": "degraded" if degraded else "ready",
        "degraded_mode": degraded,
        "gemini_available": gemini_ok,
        "gemini_circuit_broken": not gemini_ok,
        "gemini_cooldown_reason": _gemini_cooldown_reason if not gemini_ok else "",
        "openrouter_available": openrouter_ok,
        "openrouter_circuit_broken": not openrouter_ok,
        "openrouter_cooldown_reason": _openrouter_cooldown_reason if not openrouter_ok else ""
    }


async def _process_via_openrouter(user_text: str, session_id: str, openrouter_key: str, model_name: str, timeout: float = 20.0) -> Dict[str, Any]:
    """
    Executes voice command via OpenRouter with multi-turn tool calling loop.
    Supports compound commands, system diagnostics, web search, and Gojo/Tech Lead persona.
    """
    client = _get_openrouter_client(openrouter_key)

    # --- ASTRA UPGRADE: DYNAMIC MEMORY (MEM0) INJECTION START ---
    mem_ctx = search_relevant_memories(user_text)
    effective_system_instruction = get_effective_system_instruction(user_text, session_id=session_id)
    if mem_ctx:
        effective_system_instruction = (
            f"{effective_system_instruction}\n\n"
            f"[Persistent Long-Term Memory & User Preferences]:\n{mem_ctx}\n"
            f"(Note: Seamlessly incorporate these remembered facts if relevant to the conversation)."
        )
    # --- ASTRA UPGRADE: DYNAMIC MEMORY (MEM0) INJECTION END ---

    # --- ASTRA UPGRADE: DYNAMIC MCP TOOLS DISCOVERY START ---
    try:
        _, mcp_map, mcp_schemas = await discover_mcp_tools()
    except Exception as mcp_err:
        logger.warning(f"[MCP] Tool discovery failed: {mcp_err}")
        mcp_map, mcp_schemas = {}, []

    active_tools = OPENROUTER_TOOLS + mcp_schemas if mcp_schemas else OPENROUTER_TOOLS
    active_tool_map = {**TOOL_MAP, **mcp_map} if mcp_map else TOOL_MAP
    # --- ASTRA UPGRADE: DYNAMIC MCP TOOLS DISCOVERY END ---

    # Initialize messages list with Master System Instruction & multi-turn history
    messages: List[Dict[str, Any]] = [{"role": "system", "content": effective_system_instruction}]
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
                tools=active_tools,
                temperature=0.7
            ),
            timeout=timeout
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
            if fn_name in active_tool_map:
                try:
                    raw_res = active_tool_map[fn_name](**args)
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
# Gemini Live API (Bidirectional Streaming Session)
# =====================================================================

async def _invoke_live_callback(cb, *args):
    """Safely executes sync or async callback for Gemini Live streaming."""
    if not cb:
        return
    try:
        res = cb(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as e:
        logger.debug(f"[Gemini Live] Callback execution notice: {e}")


async def run_live_session(
    user_text: str,
    session_id: str = "default",
    on_text_chunk: Optional[Callable[[str], Any]] = None,
    on_audio_chunk: Optional[Callable[[bytes], Any]] = None,
    on_tool_call: Optional[Callable[[str, Dict[str, Any], Any], Any]] = None,
    is_mobile: Optional[bool] = None
) -> Dict[str, Any]:
    """
    Opens a Gemini Live bidirectional streaming connection (client.aio.live.connect).
    Registers the exact same function-calling tools schema (TOOLS_LIST) with the Live session,
    streams response chunks as they arrive, and dispatches tool calls in real time.
    Falls back gracefully to the standard offline / Gemini engine if Live fails to connect.
    """
    if not user_text or not isinstance(user_text, str) or not user_text.strip():
        return {"reply": "Aapki aawaz nahi sunai di, kripya dobara bolein.", "action": None}

    clean_text = user_text.strip()
    t0 = time.perf_counter()
    set_active_session(session_id)
    if is_mobile is not None:
        session_manager.set_user_data(session_id, "is_mobile", is_mobile)
        session_manager.set_user_data(session_id, "device", "Mobile (Smartphone)" if is_mobile else "PC / Laptop")
    reset_session_tool_counts(session_id)
    reset_executed_actions(session_id)

    # 1. Fast deterministic check: instant execution for deterministic app / media / website intents
    fallback_res = fallback_intent_parser(clean_text, session_id=session_id)
    if fallback_res and fallback_res.get("action") is not None:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            "[Gemini Live] Executed via fast deterministic intent check",
            extra={"session_id": session_id, "latency_ms": latency_ms, "mode": "fast_fallback_action", "success": True}
        )
        reply = fallback_res.get("reply", "")
        if on_text_chunk and reply:
            await _invoke_live_callback(on_text_chunk, reply)
        return fallback_res

    # 2. Check API key and circuit breaker
    if not is_gemini_available():
        logger.info("[Gemini Live] Gemini API unavailable or circuit broken. Using standard fallback.")
        return await _process_voice_command_core(clean_text, session_id=session_id)
    env_gemini_key = os.getenv("GEMINI_API_KEY")
    api_key = env_gemini_key.strip() if env_gemini_key is not None else GEMINI_API_KEY

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        mem_ctx = search_relevant_memories(clean_text)
        effective_gemini_instruction = get_effective_system_instruction(clean_text, session_id=session_id)
        if mem_ctx:
            effective_gemini_instruction = (
                f"{effective_gemini_instruction}\n\n"
                f"[Persistent Long-Term Memory & User Preferences]:\n{mem_ctx}\n"
            )

        mcp_tools, _, _ = get_dynamic_mcp_tools_sync()
        effective_gemini_tools = TOOLS_LIST + mcp_tools if mcp_tools else TOOLS_LIST

        live_config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=effective_gemini_instruction,
            tools=effective_gemini_tools,
            temperature=0.7
        )

        primary_model = os.getenv("GEMINI_LIVE_MODEL", "gemini-2.5-flash-native-audio-latest").strip() or "gemini-2.5-flash-native-audio-latest"
        candidate_models = [primary_model]
        for alt in [
            "gemini-2.5-flash-native-audio-latest",
            "gemini-3.1-flash-live-preview",
            "gemini-2.5-flash-native-audio-preview-12-2025",
            "gemini-2.5-flash-native-audio-preview-09-2025"
        ]:
            if alt not in candidate_models:
                candidate_models.append(alt)

        last_live_exc = None
        for cand_model in candidate_models:
            try:
                full_text_chunks: List[str] = []
                last_action_payload: Optional[Dict[str, Any]] = None

                logger.info(f"[Gemini Live] Connecting live session with model: '{cand_model}'")
                async with client.aio.live.connect(model=cand_model, config=live_config) as session:
                    await session.send_client_content(
                        turns=types.Content(
                            role="user",
                            parts=[types.Part(text=clean_text)]
                        ),
                        turn_complete=True
                    )

                    async for message in session.receive():
                        # A. Tool Call Handling
                        if getattr(message, "tool_call", None):
                            for fc in getattr(message.tool_call, "function_calls", []):
                                t_name = getattr(fc, "name", "")
                                t_args = dict(getattr(fc, "args", {}))
                                t_id = getattr(fc, "id", None)

                                act_res = execute_live_tool_call(t_name, t_args, session_id=session_id)
                                last_action_payload = act_res

                                if on_tool_call:
                                    await _invoke_live_callback(on_tool_call, t_name, t_args, act_res)

                                fn_resp = types.FunctionResponse(
                                    name=t_name,
                                    response={"result": act_res},
                                    id=t_id
                                )
                                await session.send_tool_response(function_responses=fn_resp)

                        # B. Server Content (Text / Audio Streaming)
                        if getattr(message, "server_content", None):
                            m_turn = getattr(message.server_content, "model_turn", None)
                            if m_turn:
                                for part in getattr(m_turn, "parts", []):
                                    txt = getattr(part, "text", "")
                                    if txt:
                                        full_text_chunks.append(txt)
                                        if on_text_chunk:
                                            await _invoke_live_callback(on_text_chunk, txt)
                                    inline_data = getattr(part, "inline_data", None)
                                    if inline_data and getattr(inline_data, "data", None):
                                        if on_audio_chunk:
                                            await _invoke_live_callback(on_audio_chunk, inline_data.data)

                    reply_text = "".join(full_text_chunks).strip()
                    if not reply_text:
                        if last_action_payload and last_action_payload.get("message"):
                            reply_text = last_action_payload["message"]
                        else:
                            reply_text = "Kaam kar diya gaya hai."

                    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
                    logger.info(
                        "[Gemini Live] Completed live streaming session",
                        extra={"session_id": session_id, "latency_ms": latency_ms, "model": cand_model, "success": True}
                    )
                    return {
                        "reply": reply_text,
                        "action": last_action_payload or {"status": "executed", "mode": "gemini_live"}
                    }

            except Exception as live_err:
                last_live_exc = live_err
                err_str = str(live_err)
                logger.warning(f"[Gemini Live] Session error on '{cand_model}': {err_str}")
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    trip_gemini_circuit_breaker(err_str)
                    break
                continue

        if last_live_exc:
            raise last_live_exc

    except Exception as exc:
        logger.warning(f"[Gemini Live] Session connection failed ({exc}). Gracefully falling back to standard engine.")
        return await _process_voice_command_core(clean_text, session_id=session_id)

    return await _process_voice_command_core(clean_text, session_id=session_id)


# =====================================================================
# Main Process Voice Command Entry Point
# =====================================================================

async def _process_voice_command_core(user_text: str, session_id: str = "default", is_mobile: Optional[bool] = None) -> Dict[str, Any]:
    """
    Core brain connecting Voice Input -> Multi-Turn Session Memory -> OpenRouter / Gemini Tool Calling.
    """
    set_active_session(session_id)
    if is_mobile is not None:
        session_manager.set_user_data(session_id, "is_mobile", is_mobile)
        session_manager.set_user_data(session_id, "device", "Mobile (Smartphone)" if is_mobile else "PC / Laptop")
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

    primary_provider = os.getenv("PRIMARY_PROVIDER", os.getenv("AI_PROVIDER", "auto")).strip().lower()
    env_gemini_key = os.getenv("GEMINI_API_KEY")
    api_key = env_gemini_key.strip() if env_gemini_key is not None else GEMINI_API_KEY
    has_gemini = is_gemini_available()
    openrouter_ready = is_openrouter_available()

    # Determine whether OpenRouter should be attempted first
    # - If primary_provider == "gemini": bypass OpenRouter, call Gemini directly for fastest response & native AFC
    # - If primary_provider == "openrouter": attempt OpenRouter first
    # - If primary_provider == "auto": attempt OpenRouter if available and configured, else Gemini
    should_try_openrouter_first = False
    if openrouter_ready:
        if primary_provider == "openrouter":
            should_try_openrouter_first = True
        elif primary_provider == "auto":
            should_try_openrouter_first = True

    if should_try_openrouter_first:
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        openrouter_model = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-lightning:free").strip()
        or_timeout = float(os.getenv("OPENROUTER_TIMEOUT", "20.0"))
        try:
            res = await _process_via_openrouter(user_text, session_id, openrouter_key, openrouter_model, timeout=or_timeout)
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
            err_str = str(e)
            status_code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
            logger.error(
                f"[OpenRouter] API call failed for model '{openrouter_model}': {type(e).__name__} (status={status_code}): {err_str}",
                extra={"model": openrouter_model, "error_type": type(e).__name__, "status_code": status_code, "error": err_str}
            )
            if "429" in err_str or status_code == 429 or "rate_limit" in err_str.lower():
                trip_openrouter_circuit_breaker(f"Rate limit 429: {err_str[:120]}", duration=3600.0)
            elif isinstance(e, (asyncio.TimeoutError, TimeoutError)):
                trip_openrouter_circuit_breaker(f"OpenRouter call timed out after {or_timeout}s (model may be queued or reasoning on free tier)", duration=180.0)
            else:
                logger.warning(f"OpenRouter call failed ({type(e).__name__}: {err_str}). Falling back to Gemini / local engine...")

    mode = "gemini_afc" if has_gemini else "fallback"

    # If no API key configured or Gemini circuit-broken, use intelligent rule-based engine / OpenRouter
    if mode == "fallback":
        # 1. Fast path: Direct deterministic intents (apps, websites, media, volume, system) execute in 1ms!
        fallback_res = fallback_intent_parser(user_text, session_id=session_id)
        if fallback_res and fallback_res.get("action") is not None:
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            logger.info(
                "Processed voice command via fast fallback intent parser",
                extra={
                    "session_id": session_id,
                    "latency_ms": latency_ms,
                    "mode": "fast_fallback_action",
                    "success": True
                }
            )
            return fallback_res

        # 2. For open-ended conversation or questions, try OpenRouter only if enabled, available, and primary_provider != 'gemini'
        if is_openrouter_available() and primary_provider != "gemini":
            openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
            openrouter_model = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-lightning:free").strip()
            elapsed = time.perf_counter() - t0
            remaining_budget = max(0.0, 24.0 - elapsed)
            or_timeout = float(os.getenv("OPENROUTER_TIMEOUT", "20.0"))
            if remaining_budget >= 4.0:
                try:
                    res = await _process_via_openrouter(
                        user_text, session_id, openrouter_key, openrouter_model, timeout=min(remaining_budget, or_timeout)
                    )
                    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
                    logger.info("Processed voice command via OpenRouter fallback", extra={"session_id": session_id, "latency_ms": latency_ms, "mode": "openrouter", "success": True})
                    return res
                except Exception as e:
                    err_str = str(e)
                    status_code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
                    logger.error(
                        f"[OpenRouter] Fallback call failed for model '{openrouter_model}': {type(e).__name__} (status={status_code}): {err_str}",
                        extra={"model": openrouter_model, "error_type": type(e).__name__, "status_code": status_code, "error": err_str}
                    )
                    if "429" in err_str or status_code == 429 or "rate_limit" in err_str.lower():
                        trip_openrouter_circuit_breaker(f"Rate limit 429: {err_str[:120]}", duration=3600.0)
                    elif isinstance(e, (asyncio.TimeoutError, TimeoutError)):
                        trip_openrouter_circuit_breaker(f"OpenRouter fallback call timed out after {min(remaining_budget, or_timeout)}s", duration=180.0)

        # 3. Honest reply if Gemini is actively circuit-broken/exhausted from 429 and OpenRouter could not fulfill the general query
        if fallback_res.get("action") is None and is_gemini_circuit_broken():
            fallback_res["reply"] = "AI quota abhi khatam ho gayi hai ya servers busy hain, kripya thodi der baad try karein. (Lekin local PC commands jaise apps kholna, gaana chalana abhi bhi kaam kar rahe hain)."
            fallback_res["degraded_mode"] = True
            fallback_res["ai_status"] = "degraded"
            fallback_res["degraded_reason"] = f"Gemini quota exhausted / circuit-broken ({_gemini_cooldown_reason[:100] if _gemini_cooldown_reason else '429'})"

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
        return fallback_res

    try:
        from google import genai
        from google.genai import types

        global _persistent_genai_client, _persistent_client_key
        if "_persistent_genai_client" not in globals() or globals().get("_persistent_client_key") != api_key:
            _persistent_genai_client = genai.Client(api_key=api_key)
            _persistent_client_key = api_key
        client = _persistent_genai_client

        # --- ASTRA UPGRADE: DYNAMIC MEMORY & MCP INJECTION FOR GEMINI AFC START ---
        mem_ctx = search_relevant_memories(user_text)
        effective_gemini_instruction = get_effective_system_instruction(user_text, session_id=session_id)
        if mem_ctx:
            effective_gemini_instruction = (
                f"{effective_gemini_instruction}\n\n"
                f"[Persistent Long-Term Memory & User Preferences]:\n{mem_ctx}\n"
            )

        mcp_tools, _, _ = get_dynamic_mcp_tools_sync()
        effective_gemini_tools = TOOLS_LIST + mcp_tools if mcp_tools else TOOLS_LIST
        # --- ASTRA UPGRADE: DYNAMIC MEMORY & MCP INJECTION FOR GEMINI AFC END ---

        config = types.GenerateContentConfig(
            system_instruction=effective_gemini_instruction,
            tools=effective_gemini_tools,
            temperature=0.7
        )

        primary_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
        candidate_models = [primary_model]
        for alt in ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash", "gemini-flash-latest"]:
            if alt not in candidate_models:
                candidate_models.append(alt)

        response = None
        last_gemini_exc = None
        for cand_model in candidate_models:
            try:
                # Persistent Multi-Turn Chat instance per session
                chat = session_manager.get_or_create_chat(
                    session_id=session_id,
                    client=client,
                    model_name=cand_model,
                    config=config
                )
                response = await asyncio.wait_for(asyncio.to_thread(chat.send_message, user_text), timeout=10.0)
                if response:
                    break
            except Exception as gemini_err:
                last_gemini_exc = gemini_err
                err_text = str(gemini_err)
                if "429" in err_text or "RESOURCE_EXHAUSTED" in err_text or "retryDelay" in err_text:
                    logger.warning(f"[Gemini] Quota/rate limit 429 on '{cand_model}'. Trying next candidate model...")
                    logger.warning(f"[Gemini] Quota/rate limit 429 on '{cand_model}'.")
                    # If daily project-level free quota is exhausted (limit: 20 reqs/day), all models will fail. Break immediately to avoid 5-second dead freeze!
                    if "free_tier_requests" in err_text or "limit: 20" in err_text or "quota exceeded" in err_text.lower():
                        logger.warning("[Gemini] Daily project-level quota exhausted. Tripping circuit breaker immediately to eliminate delay.")
                        trip_gemini_circuit_breaker(err_text, duration=300.0)
                        break
                    continue
                elif "not found" in err_text.lower():
                    logger.warning(f"[Gemini] Model '{cand_model}' not found. Trying fallback model...")
                    continue
                raise gemini_err

        if not response and last_gemini_exc:
            err_text = str(last_gemini_exc)
            if "429" in err_text or "RESOURCE_EXHAUSTED" in err_text or "retryDelay" in err_text:
                trip_gemini_circuit_breaker(err_text)
                logger.warning(f"[Gemini] Quota/rate limit 429 on all candidate models. Tripped Gemini circuit breaker; bypassing remaining models.")
            raise last_gemini_exc

        ai_response = response.text if response and response.text else "Task completed successfully."

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
                    reply_text = f"Playing '{search_query}' on YouTube for you."
                else:
                    reply_text = f"Sorry, could not play '{search_query}' on YouTube: {act_res.get('message', 'Error')}"
                return {"reply": reply_text, "action": act_res}
            else:
                act_res = dispatch_action_safe("open_website", {"website": "youtube"})
                return {"reply": "Opened YouTube for you.", "action": act_res}

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
            reply_text = act_res.get("message", f"Playing '{search_query}' on Spotify." if search_query else "Playing music on Spotify.")
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
            reply_text = act_res.get("message", f"Searching for '{search_query}' on Instagram." if search_query else "Opened Instagram.")
            return {"reply": reply_text, "action": act_res}

        elif act_type == "OPEN_URL_WHATSAPP" or "ACTION: OPEN_URL_WHATSAPP" in clean_response or "ACTION: OPEN_WHATSAPP" in clean_response:
            act_res = dispatch_action_safe("open_website", {"website": "whatsapp"})
            if not act_res.get("success"):
                reply_text = f"Sorry, could not open WhatsApp: {act_res.get('message', 'Error')}"
            else:
                reply_clean = re.sub(r'ACTION:\s*OPEN_URL_WHATSAPP', '', clean_response, flags=re.I).strip()
                reply_clean = re.sub(r'ACTION:\s*OPEN_WHATSAPP', '', reply_clean, flags=re.I).strip()
                reply_text = reply_clean if reply_clean else "Opened WhatsApp Web."
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

            # Guard against conjunctions or invalid contact names extracted by LLM
            if contact and (contact.strip().lower() in CONJUNCTIONS or contact.strip().lower() in NON_CONTACT_WORDS):
                real_c, real_m, _ = extract_whatsapp_parameters(user_text)
                if real_c:
                    contact = real_c.capitalize()
                    if real_m and not message:
                        message = real_m
                else:
                    contact = None

            if contact and not message:
                session_manager.record_pending_whatsapp(session_id, contact.capitalize())
                return {
                    "reply": f"What message would you like to send to {contact.capitalize()}?",
                    "action": {"status": "clarification_needed", "contact_name": contact.capitalize()}
                }

            if not contact:
                return {
                    "reply": "Who would you like to message on WhatsApp?",
                    "action": {"status": "clarification_needed", "platform": "whatsapp"}
                }

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
            reply_text = act_res.get("message") or (f"Opened {site_display} for you." if act_res.get("success") else f"Sorry, could not open {site_display}: {act_res.get('message', 'Error')}")
            return {"reply": reply_text, "action": act_res}

        elif act_type in ["open_app", "open_application"]:
            app = action_data.get("app_name", "") or action_data.get("app", "")
            act_res = dispatch_action_safe("open_app", {"app_name": app})
            reply_text = act_res.get("message") or (f"Opened {app.capitalize()} for you." if act_res.get("success") else f"Sorry, could not open '{app.capitalize()}': {act_res.get('message', 'Application not found.')}")
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
                        reply_text = f"Sorry, could not open {site_key.capitalize()}: {act_res.get('message', 'Error')}"
                    else:
                        reply_text = f"Opened {site_key.capitalize()} for you."
                    return {
                        "reply": reply_text,
                        "action": act_res
                    }
                elif target_name in actions.COMMON_APP_MAP or any(k == target_name for k in actions.COMMON_APP_MAP):
                    app_key = next((k for k in actions.COMMON_APP_MAP if k == target_name), target_name)
                    act_res = dispatch_action_safe("open_app", {"app_name": app_key})
                    if not act_res.get("success"):
                        reply_text = f"Sorry, could not open '{app_key.capitalize()}': {act_res.get('message', 'Error')}"
                    else:
                        reply_text = f"Opened {app_key.capitalize()} for you."
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
        # 1. Fast deterministic intent check: if command is an app, website, media, volume, or system command,
        # fallback_intent_parser executes it in 1-5ms without waiting for slow cloud LLMs!
        try:
            fallback_res = fallback_intent_parser(user_text, session_id=session_id)
            if fallback_res and fallback_res.get("action") is not None:
                return fallback_res
        except Exception as fb_err:
            logger.error(f"Fallback recovery error: {fb_err}")
            fallback_res = {
                "reply": "Kshama karein, main abhi yeh command process nahi kar pa raha hoon. Kripya dobara koshish karein.",
                "action": {"status": "error", "error": str(fb_err)}
            }

        # 2. Check remaining time budget before attempting OpenRouter
        elapsed = time.perf_counter() - t0
        remaining_budget = max(0.0, 24.0 - elapsed)
        or_timeout = float(os.getenv("OPENROUTER_TIMEOUT", "20.0"))

        if is_openrouter_available() and not should_try_openrouter_first and remaining_budget >= 4.0:
            openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
            openrouter_model = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-lightning:free").strip()
            try:
                return await _process_via_openrouter(
                    user_text,
                    session_id,
                    openrouter_key,
                    openrouter_model,
                    timeout=min(remaining_budget, or_timeout)
                )
            except Exception as or_err:
                err_str = str(or_err)
                status_code = getattr(or_err, "status_code", None) or getattr(getattr(or_err, "response", None), "status_code", None)
                logger.error(
                    f"[OpenRouter] API call failed during recovery for model '{openrouter_model}': {type(or_err).__name__} (status={status_code}): {err_str}",
                    extra={"model": openrouter_model, "error_type": type(or_err).__name__, "status_code": status_code, "error": err_str}
                )
                if "429" in err_str or status_code == 429 or "rate_limit" in err_str.lower():
                    trip_openrouter_circuit_breaker(f"Rate limit 429: {err_str[:120]}", duration=3600.0)
                elif isinstance(or_err, (asyncio.TimeoutError, TimeoutError)):
                    trip_openrouter_circuit_breaker(f"OpenRouter recovery call timed out after {min(remaining_budget, or_timeout)}s", duration=180.0)

        # 3. Honest fallback for open-ended queries when all cloud AI providers are unavailable (quota exhausted or offline)
        if fallback_res.get("action") is None:
            err_msg_lower = str(e).lower()
            is_quota_exhausted = (
                "429" in err_msg_lower
                or "resource_exhausted" in err_msg_lower
                or "quota" in err_msg_lower
                or "rate limit" in err_msg_lower
                or not is_gemini_available()
            )
            if is_quota_exhausted:
                fallback_res["reply"] = "AI quota abhi khatam ho gayi hai ya servers busy hain, kripya thodi der baad try karein. (Lekin local PC commands jaise apps kholna, gaana chalana abhi bhi kaam kar rahe hain)."
            else:
                fallback_res["reply"] = "AI service abhi temporarily unavailable hai, thodi der baad try karein. Local PC commands jaise apps kholna normal chal rahe hain."
            fallback_res["degraded_mode"] = True
            fallback_res["ai_status"] = "degraded"
            fallback_res["degraded_reason"] = f"Gemini ({type(e).__name__}) and OpenRouter are unavailable."

        return fallback_res

# --- ASTRA UPGRADE: PERSIST LONG-TERM MEMORY (MEM0) WRAPPER START ---
async def process_voice_command(user_text: str, session_id: str = "default", is_mobile: Optional[bool] = None) -> Dict[str, Any]:
    """
    Core brain connecting Voice Input -> Multi-Turn Session Memory -> OpenRouter / Gemini Tool Calling / Live API.
    Asynchronously persists interactions to Mem0 long-term memory layer with zero latency penalty.
    """
    if not user_text or not isinstance(user_text, str) or not user_text.strip():
        return {"reply": "Aapki aawaz nahi sunai di, kripya dobara bolein.", "action": None}
    clean_text = user_text.strip()

    if is_mobile is not None:
        session_manager.set_user_data(session_id, "is_mobile", is_mobile)
        session_manager.set_user_data(session_id, "device", "Mobile (Smartphone)" if is_mobile else "PC / Laptop")

    use_live = os.getenv("USE_GEMINI_LIVE", "false").strip().lower() in ("true", "1", "yes")
    if use_live:
        try:
            res = await run_live_session(clean_text, session_id=session_id, is_mobile=is_mobile)
        except Exception as live_err:
            logger.warning(f"[Gemini Live] Error in run_live_session: {live_err}. Falling back to standard pipeline.")
            res = await _process_voice_command_core(clean_text, session_id=session_id, is_mobile=is_mobile)
    else:
        res = await _process_voice_command_core(clean_text, session_id=session_id, is_mobile=is_mobile)

    try:
        reply_text = res.get("reply", "") if isinstance(res, dict) else ""
        if clean_text and reply_text:
            add_interaction_memory_async(clean_text, reply_text)
    except Exception as e:
        logger.debug(f"[Mem0] Interaction persistence skipped: {e}")
    return res
# --- ASTRA UPGRADE: PERSIST LONG-TERM MEMORY (MEM0) WRAPPER END ---


# Backward compatibility alias
process_command = process_voice_command
