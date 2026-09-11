"""
Astra Assistant - Local PC Executor
Runs as a dedicated lightweight client on the Windows laptop.
Maintains a persistent WebSocket connection to the Astra main server (local or cloud host)
to execute desktop automation / PC-control tools securely.
"""

import os
import sys
import json
import asyncio
from typing import Dict, Any, Optional

import websockets
from dotenv import load_dotenv

# Ensure application root directory is on Python path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

from config import ASTRA_AUTH_TOKEN, EXECUTOR_SERVER_URL
import core.actions as actions
from core.logger import get_logger

logger = get_logger("astra.executor")

# PC-control & browser tools executed locally on the Windows laptop
SUPPORTED_TOOLS = {
    "open_app",
    "close_app",
    "open_website",
    "system_control",
    "start_dev_environment",
    "analyze_screen",
    "analyze_clipboard",
    "send_whatsapp_message",
    "send_whatsapp_reply",
    "send_instagram_dm",
    "get_instagram_unread",
    "play_youtube_video",
    "play_spotify_music",
    "search_instagram_user",
}


def execute_local_tool(action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Dispatches requested PC tool to core.actions."""
    if action not in SUPPORTED_TOOLS:
        return {
            "success": False,
            "status": "error",
            "message": f"Unknown local tool: '{action}'"
        }

    func = getattr(actions, action, None)
    if not func:
        return {
            "success": False,
            "status": "error",
            "message": f"Tool '{action}' not found in core.actions"
        }

    try:
        kwargs = params or {}
        return func(**kwargs)
    except Exception as e:
        logger.error(f"Error executing local tool '{action}': {e}", exc_info=True)
        return {
            "success": False,
            "status": "error",
            "message": f"Error executing local tool '{action}': {e}"
        }


async def run_executor_client(
    server_url: Optional[str] = None,
    token: Optional[str] = None,
    max_retries: Optional[int] = None,
    stop_event: Optional[asyncio.Event] = None
):
    """
    Connects to the Astra server's WebSocket endpoint and listens for tool execution requests.
    Automatically reconnects with exponential backoff if disconnected.
    """
    base_url = server_url or EXECUTOR_SERVER_URL
    auth_token = token or ASTRA_AUTH_TOKEN

    # Ensure token query parameter is properly formatted
    if "token=" not in base_url:
        sep = "&" if "?" in base_url else "?"
        ws_url = f"{base_url}{sep}token={auth_token}"
    else:
        ws_url = base_url

    retry_delay = 1.0
    retries = 0

    while True:
        if stop_event and stop_event.is_set():
            logger.info("Stop event set. Exiting executor client.")
            break

        try:
            logger.info(f"Connecting to Astra server at {base_url}...")
            async with websockets.connect(ws_url) as ws:
                logger.info("Connected to Astra server as Local PC Executor.")
                retry_delay = 1.0
                retries = 0

                async for raw_message in ws:
                    if stop_event and stop_event.is_set():
                        break

                    try:
                        data = json.loads(raw_message)
                        req_id = data.get("id")
                        action = data.get("action")
                        params = data.get("params", {})

                        logger.info(f"Received action: '{action}' (Req ID: {req_id})")
                        # Run the local tool in a thread pool to avoid blocking the WebSocket loop
                        result = await asyncio.to_thread(execute_local_tool, action, params)

                        response_frame = {
                            "id": req_id,
                            "result": result
                        }
                        await ws.send(json.dumps(response_frame))
                        logger.info(f"Completed action '{action}' (Req ID: {req_id}) -> success: {result.get('success')}")

                    except json.JSONDecodeError:
                        logger.warning(f"Received non-JSON frame: {raw_message}")
                    except Exception as e:
                        logger.error(f"Error handling command frame: {e}")

        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            logger.warning(f"Connection to Astra server dropped ({e}). Reconnecting in {retry_delay:.1f}s...")
        except Exception as e:
            logger.error(f"Executor connection error: {e}")

        retries += 1
        if max_retries is not None and retries >= max_retries:
            logger.info("Max retries reached. Exiting executor loop.")
            break

        if stop_event and stop_event.is_set():
            break

        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 10.0)


if __name__ == "__main__":
    print("=" * 60)
    print("  Astra Local PC Executor (Windows Laptop Agent)")
    print("=" * 60)
    print(f"[*] Target Server: {EXECUTOR_SERVER_URL}")
    print("[*] Listening for remote PC-control commands...")
    print("[*] Press Ctrl+C to stop.\n")
    try:
        asyncio.run(run_executor_client())
    except KeyboardInterrupt:
        print("\n[*] Astra Local PC Executor stopped by user.")

