"""
Astra Assistant - Persistent Long-Term Memory Layer (Mem0)
Provides session-spanning memory for user preferences, facts, and past interactions.
Designed with Antigravity Architecture: Gracefully degrades to local user profile
if unconfigured, offline, or if dependencies are missing.
"""

import os
import logging
import threading
from typing import Dict, Any, Optional

logger = logging.getLogger("astra.memory")

# --- ASTRA UPGRADE: MEM0 START ---

_MEM0_CLIENT = None
_MEM0_INITIALIZED = False
_MEM0_LOCK = threading.Lock()
MEM0_AVAILABLE = False


def _get_mem0_client():
    """Initializes and returns the singleton Mem0 client with safe fallback."""
    global _MEM0_CLIENT, _MEM0_INITIALIZED, MEM0_AVAILABLE
    with _MEM0_LOCK:
        if _MEM0_INITIALIZED:
            return _MEM0_CLIENT

        api_key = os.getenv("MEM0_API_KEY", "").strip()
        if not api_key or api_key.startswith("your_"):
            logger.info("[Mem0] MEM0_API_KEY is not configured. Falling back to local user profile context.")
            _MEM0_INITIALIZED = True
            _MEM0_CLIENT = None
            MEM0_AVAILABLE = False
            return None

        try:
            if api_key.startswith("m0-"):
                from mem0 import MemoryClient
                _MEM0_CLIENT = MemoryClient(api_key=api_key)
            else:
                from mem0 import Memory
                _MEM0_CLIENT = Memory()
            MEM0_AVAILABLE = True
            logger.info("[Mem0] Persistent memory layer successfully initialized.")
        except Exception as e:
            logger.debug(f"[Mem0] Failed to initialize Mem0 client: {e}. Graceful degradation active.")
            _MEM0_CLIENT = None
            MEM0_AVAILABLE = False

        _MEM0_INITIALIZED = True
        return _MEM0_CLIENT


def search_relevant_memories(query: str, user_id: Optional[str] = None, limit: int = 5) -> str:
    """
    Searches Mem0 for contextually relevant past preferences and facts.
    Returns a clean, bulleted text block for dynamic system prompt injection.
    Returns an empty string if Mem0 is unconfigured or an error occurs.
    """
    if not query or not query.strip():
        return ""

    uid = user_id or os.getenv("MEM0_USER_ID", "boss_jay").strip() or "boss_jay"
    client = _get_mem0_client()
    if not client:
        return ""

    try:
        is_cloud_client = type(client).__name__ == "MemoryClient"
        if is_cloud_client:
            memories = client.search(query=query.strip(), filters={"user_id": uid}, limit=limit)
        else:
            memories = client.search(query=query.strip(), user_id=uid, limit=limit)

        if not memories:
            return ""

        if isinstance(memories, dict) and "results" in memories:
            memories = memories["results"]

        extracted = []
        for mem in memories:
            if isinstance(mem, dict):
                text = mem.get("memory") or mem.get("text") or ""
                if text:
                    extracted.append(f"- {text}")
            elif isinstance(mem, str) and mem.strip():
                extracted.append(f"- {mem.strip()}")

        if not extracted:
            return ""

        return "\n".join(extracted)
    except Exception as e:
        logger.debug(f"[Mem0] Memory search notice: {e}")
        return ""


def add_interaction_memory_async(user_text: str, assistant_reply: str, user_id: Optional[str] = None) -> None:
    """
    Saves the user-assistant interaction into Mem0 asynchronously in a background thread.
    Zero latency overhead on conversational voice responses.
    """
    if not user_text or not assistant_reply:
        return

    uid = user_id or os.getenv("MEM0_USER_ID", "boss_jay").strip() or "boss_jay"
    client = _get_mem0_client()
    if not client:
        return

    def _worker():
        try:
            messages = [
                {"role": "user", "content": user_text.strip()},
                {"role": "assistant", "content": assistant_reply.strip()}
            ]
            client.add(messages=messages, user_id=uid)
            logger.debug(f"[Mem0] Background interaction memory saved for user '{uid}'.")
        except Exception as e:
            logger.warning(f"[Mem0] Background memory add error: {e}")

    threading.Thread(target=_worker, daemon=True).start()


def get_memory_status() -> Dict[str, Any]:
    """Returns the operational status of the long-term memory layer."""
    client = _get_mem0_client()
    return {
        "active": client is not None,
        "provider": "mem0ai" if client is not None else "local_profile_fallback",
        "user_id": os.getenv("MEM0_USER_ID", "boss_jay")
    }

# --- ASTRA UPGRADE: MEM0 END ---

