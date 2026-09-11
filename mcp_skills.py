
"""
MCP Skills Server - Laptop Assistant
Local Model Context Protocol (MCP) server built with FastMCP exposing
automation skills backed by core.actions as the single source of truth.
"""

import sys
import contextlib
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# FastMCP compatibility import
try:
    from mcp.server.fastmcp import FastMCP
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as FastMCP

# Single Source of Truth: import centralized automation engine
from core import actions

# Initialize FastMCP Server
mcp = FastMCP("Laptop_Assistant")


# =====================================================================
# Core System Tools (Delegated to core.actions)
# =====================================================================

@mcp.tool()
def open_application(app_name: str) -> str:
    """Open a Windows application by name using AppOpener or system shell.

    Args:
        app_name: Name of the application to launch (e.g., 'notepad', 'chrome', 'calc').
    """
    res = actions.open_app(app_name)
    return res.get("message", f"Processed open request for {app_name}.")


@mcp.tool()
def close_application(app_name: str) -> str:
    """Close a running Windows application by name using AppOpener or taskkill.

    Args:
        app_name: Name of the application to terminate (e.g., 'notepad', 'chrome').
    """
    res = actions.close_app(app_name)
    return res.get("message", f"Processed close request for {app_name}.")


@mcp.tool()
def control_system(command: str) -> str:
    """Control system settings: volume_up, volume_down, mute, lock, and screenshot.

    Args:
        command: Action to perform ('volume_up', 'volume_down', 'mute', 'lock', 'screenshot').
    """
    res = actions.system_control(command)
    return res.get("message", f"Executed system command: {command}")


@mcp.tool()
def get_time_and_date() -> str:
    """Return the current local system date, time, and ISO 8601 timestamp."""
    res = actions.get_time_and_date()
    return f"{res.get('message')} (ISO: {res.get('iso')})"


@mcp.tool()
def analyze_clipboard() -> str:
    """Read and summarize text currently stored on the system clipboard."""
    res = actions.analyze_clipboard()
    return res.get("message", "Clipboard analyzed.")


# =====================================================================
# Web & Communication Tools
# =====================================================================

@mcp.tool()
def open_or_search_website(query: str, search: bool = False) -> str:
    """Open URLs directly or format YouTube/Google search queries in default browser.

    Args:
        query: URL, domain, or search query.
        search: If True, forces a web search on Google/YouTube.
    """
    clean_q = query.strip()
    if search:
        if "youtube" in clean_q.lower():
            res = actions.open_website("youtube", clean_q)
        else:
            res = actions.open_website("google", clean_q)
    else:
        res = actions.open_website(clean_q)
    return res.get("message", f"Processed web request for: {query}")


@mcp.tool()
def send_whatsapp_message(phone: str, message: str) -> str:
    """Open web.whatsapp.com/send URL with pre-filled encoded text ready to send.

    Args:
        phone: Recipient phone number (with or without country code).
        message: Pre-filled message text.
    """
    res = actions.send_whatsapp(phone, message)
    return res.get("message", "WhatsApp Web opened with pre-filled message.")


@mcp.tool()
def play_spotify_music(song_query: str) -> str:
    """Open Spotify URI to search and play music on desktop app or web player.

    Args:
        song_query: Song title, artist name, or album to search and play.
    """
    res = actions.play_spotify_music(song_query)
    return res.get("message", f"Triggered Spotify for: {song_query}")


# =====================================================================
# Developer Workflow & Security Tools
# =====================================================================

@mcp.tool()
def start_dev_environment(project_name: str) -> str:
    """Launch VS Code and start the Next.js dev server in a new terminal based on project name.

    Args:
        project_name: Name of the project or directory.
    """
    res = actions.start_dev_environment(project_name=project_name)
    return res.get("message", "Dev environment initialization command executed.")


@mcp.tool()
def manage_odoo_server(action: str, module_name: str = "") -> str:
    """Restart the Odoo server or update a module with strict security guardrails.

    Args:
        action: Management command ('restart', 'update', 'status').
        module_name: Module to update. NOTE: Financial & Inventory modules are strictly forbidden.
    """
    res = actions.manage_odoo_server(action, module_name)
    return res.get("message", "Odoo server command processed.")


@mcp.tool()
def analyze_screen(prompt: str = "") -> str:
    """Capture current desktop screen and analyze it with Gemini Vision.

    Args:
        prompt: Optional specific question about what is displayed on screen.
    """
    res = actions.analyze_screen(prompt)
    return res.get("message", "Screen analyzed.")


# =====================================================================
# Google Workspace Integration Tools
# =====================================================================

@mcp.tool()
def read_recent_emails(count: int = 5) -> str:
    """Read the most recent emails from the user's Gmail inbox.

    Args:
        count: Number of emails to retrieve (1 to 20, default 5).
    """
    res = actions.read_recent_emails(count)
    return res.get("message", "Processed email reading request.")


@mcp.tool()
def get_upcoming_events(days: int = 7) -> str:
    """Retrieve upcoming Google Calendar events and auto-schedule reminders 15 minutes before start.

    Args:
        days: Number of days into the future to look for events (1 to 30, default 7).
    """
    res = actions.get_upcoming_events(days)
    return res.get("message", "Processed calendar events request.")


# =====================================================================
# WhatsApp Web Automation Tools (Playwright Agent)
# =====================================================================

@mcp.tool()
def get_whatsapp_unread(max_chats: int = 5) -> str:
    """Check unread messages and incoming notifications on WhatsApp Web.

    Args:
        max_chats: Maximum number of unread conversations to retrieve (default 5).
    """
    res = actions.get_whatsapp_unread(max_chats)
    return res.get("message", "Processed WhatsApp unread query.")


@mcp.tool()
def send_whatsapp_reply(chat_name: str, message: str) -> str:
    """Send an automated reply to a WhatsApp contact or group name with a 60s cooldown limit.

    Args:
        chat_name: Contact or Group name as displayed in WhatsApp.
        message: Message text to send.
    """
    res = actions.send_whatsapp_reply(chat_name, message)
    return res.get("message", f"Processed WhatsApp reply to {chat_name}.")


# =====================================================================
# Instagram Direct Message Automation Tools (Playwright Agent)
# =====================================================================

@mcp.tool()
def get_instagram_unread(max_chats: int = 5) -> str:
    """Check unread messages and incoming notifications on Instagram Direct.

    Args:
        max_chats: Maximum number of unread conversations to retrieve (default 5).
    """
    res = actions.get_instagram_unread(max_chats)
    return res.get("message", "Processed Instagram unread query.")


@mcp.tool()
def send_instagram_dm(username: str, message: str) -> str:
    """Send an automated direct message to an Instagram user with 90s cooldown and 20 DMs/day ceiling.

    Args:
        username: Instagram handle / username of the recipient.
        message: Message text to send.
    """
    res = actions.send_instagram_dm(username, message)
    return res.get("message", f"Processed Instagram DM to {username}.")


@mcp.tool()
def fetch_instagram_inbox(max_chats: int = 5) -> str:
    """Fetch unread Instagram direct messages."""
    result = actions.get_instagram_unread(max_chats)
    if result.get("status") == "success" or result.get("success"):
        return str(result.get("messages") or result.get("data") or result)
    return result.get("message", "Failed to fetch Instagram messages.")


@mcp.tool()
def send_ig_message(username: str, message: str) -> str:
    """Send an Instagram DM to a user."""
    result = actions.send_instagram_dm(username, message)
    return result.get("message", str(result))



# =====================================================================
# Server Runner
# =====================================================================

if __name__ == "__main__":
    mcp.run(transport="stdio")

