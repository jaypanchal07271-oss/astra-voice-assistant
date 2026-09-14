"""
Astra Assistant - Dynamic Model Context Protocol (MCP) Tool Bridge
Connects to standard Model Context Protocol servers (e.g., Brave Search, Filesystem)
and dynamically registers tools into Astra's brain with safe fallback.
"""

import os
import json
import logging
import asyncio
import shlex
from typing import Dict, Any, List, Tuple, Callable

logger = logging.getLogger("astra.mcp")

# --- ASTRA UPGRADE: MCP START ---

_DYNAMIC_MCP_TOOLS: List[Callable] = []
_DYNAMIC_MCP_MAP: Dict[str, Callable] = {}
_DYNAMIC_OPENROUTER_SCHEMAS: List[Dict[str, Any]] = []
_MCP_DISCOVERED = False
_MCP_LOCK = asyncio.Lock() if hasattr(asyncio, "Lock") else None


def _get_mcp_server_command() -> str:
    """Resolves the MCP server command from environment variables."""
    cmd = os.getenv("MCP_SERVER_COMMAND", "").strip()
    if cmd:
        return cmd

    brave_key = os.getenv("BRAVE_API_KEY", "").strip()
    if brave_key and not brave_key.startswith("your_"):
        return "npx -y @modelcontextprotocol/server-brave-search"

    return ""


async def discover_mcp_tools() -> Tuple[List[Callable], Dict[str, Callable], List[Dict[str, Any]]]:
    """
    Connects to the configured MCP server via stdio and discovers dynamic tools.
    Converts them into:
      1. List of callable Python functions for Gemini AFC
      2. Key-value dictionary for TOOL_MAP
      3. OpenAI/OpenRouter compatible JSON schemas
    Returns: (tools_list, tool_map, openrouter_schemas)
    """
    global _DYNAMIC_MCP_TOOLS, _DYNAMIC_MCP_MAP, _DYNAMIC_OPENROUTER_SCHEMAS, _MCP_DISCOVERED

    if _MCP_DISCOVERED:
        return _DYNAMIC_MCP_TOOLS, _DYNAMIC_MCP_MAP, _DYNAMIC_OPENROUTER_SCHEMAS

    enabled = os.getenv("MCP_ENABLED", "false").strip().lower() in ("true", "1", "yes")
    cmd_str = _get_mcp_server_command()

    if not enabled or not cmd_str:
        logger.info("[MCP] Dynamic MCP server is disabled or not configured. Using native tools.")
        _MCP_DISCOVERED = True
        return [], {}, []

    try:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.session import ClientSession

        parts = shlex.split(cmd_str, posix=False)
        command = parts[0]
        args = parts[1:] if len(parts) > 1 else []

        env = dict(os.environ)
        brave_key = os.getenv("BRAVE_API_KEY", "").strip()
        if brave_key:
            env["BRAVE_API_KEY"] = brave_key

        server_params = StdioServerParameters(command=command, args=args, env=env)

        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tool_list_resp = await session.list_tools()
                raw_tools = getattr(tool_list_resp, "tools", []) or []

                for t in raw_tools:
                    name = t.name
                    desc = t.description or f"MCP tool {name}"
                    schema = t.inputSchema or {"type": "object", "properties": {}}

                    # Build OpenRouter / OpenAI JSON Schema
                    openrouter_def = {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": f"[MCP Dynamic Tool] {desc}",
                            "parameters": schema
                        }
                    }
                    _DYNAMIC_OPENROUTER_SCHEMAS.append(openrouter_def)

                    # Create dynamic invoker that calls tool through MCP
                    def make_invoker(tool_name: str, cmd: str, arg_list: list, server_env: dict):
                        def mcp_tool_wrapper(**kwargs) -> str:
                            async def _exec():
                                sp = StdioServerParameters(command=cmd, args=arg_list, env=server_env)
                                async with stdio_client(sp) as (r, w):
                                    async with ClientSession(r, w) as sess:
                                        await sess.initialize()
                                        res = await sess.call_tool(tool_name, arguments=kwargs)
                                        contents = [c.text for c in getattr(res, "content", []) if hasattr(c, "text")]
                                        record_mcp_activity(f"Tool executed successfully: {tool_name}")
                                        return "\n".join(contents) if contents else "Success (no output)"
                            try:
                                return asyncio.run(_exec())
                            except Exception as ex:
                                record_mcp_activity(f"Tool execution failed: {tool_name}")
                                return f"MCP tool execution failed: {ex}"

                        mcp_tool_wrapper.__name__ = tool_name
                        mcp_tool_wrapper.__doc__ = desc
                        return mcp_tool_wrapper

                    invoker = make_invoker(name, command, args, env)
                    _DYNAMIC_MCP_TOOLS.append(invoker)
                    _DYNAMIC_MCP_MAP[name] = invoker

                record_mcp_activity(f"Successfully registered {len(_DYNAMIC_MCP_TOOLS)} dynamic MCP tool(s)")
                logger.info(f"[MCP] Successfully registered {len(_DYNAMIC_MCP_TOOLS)} dynamic tool(s) from MCP server.")

    except Exception as e:
        record_mcp_activity(f"MCP connection failed: {type(e).__name__}")
        logger.warning(f"[MCP] Failed to connect to MCP server ({e}). Gracefully falling back to native tools.")

    _MCP_DISCOVERED = True
    return _DYNAMIC_MCP_TOOLS, _DYNAMIC_MCP_MAP, _DYNAMIC_OPENROUTER_SCHEMAS


def get_dynamic_mcp_tools_sync() -> Tuple[List[Callable], Dict[str, Callable], List[Dict[str, Any]]]:
    """Synchronous discovery runner for startup initialization."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return _DYNAMIC_MCP_TOOLS, _DYNAMIC_MCP_MAP, _DYNAMIC_OPENROUTER_SCHEMAS
        return loop.run_until_complete(discover_mcp_tools())
    except Exception:
        return _DYNAMIC_MCP_TOOLS, _DYNAMIC_MCP_MAP, _DYNAMIC_OPENROUTER_SCHEMAS


_MCP_ACTIVITY_LOG: List[Dict[str, str]] = []


def record_mcp_activity(event: str, message: str = "") -> None:
    """Records a safe, sanitized event in the in-memory MCP activity feed (max 15 items)."""
    import datetime
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    if message:
        entry = {"timestamp": now_str, "tool": event, "message": message}
    else:
        entry = {"timestamp": now_str, "tool": "mcp", "message": event}
    _MCP_ACTIVITY_LOG.insert(0, entry)
    if len(_MCP_ACTIVITY_LOG) > 15:
        _MCP_ACTIVITY_LOG.pop()


def get_mcp_status() -> Dict[str, Any]:
    """
    Returns real, verified operational status of the MCP Tool Bridge.
    Never fabricates or simulates a connected state if unconfigured.
    """
    enabled = os.getenv("MCP_ENABLED", "false").strip().lower() in ("true", "1", "yes")
    cmd_str = _get_mcp_server_command()
    configured = bool(enabled and cmd_str)

    tools_list = []
    for schema in _DYNAMIC_OPENROUTER_SCHEMAS:
        fn = schema.get("function", {})
        name = fn.get("name", "")
        desc = fn.get("description", "").replace("[MCP Dynamic Tool] ", "")
        if name:
            tools_list.append({
                "name": name,
                "description": desc,
                "available": True
            })

    if not configured:
        status = "Not Configured"
        server_name = "None"
    elif len(tools_list) > 0:
        status = "Connected"
        server_name = cmd_str.split()[0] if cmd_str else "Unknown"
    else:
        status = "Disconnected"
        server_name = cmd_str.split()[0] if cmd_str else "Unknown"

    last_act = _MCP_ACTIVITY_LOG[0]["message"] if _MCP_ACTIVITY_LOG else "No MCP activity yet."

    activities = list(_MCP_ACTIVITY_LOG)
    return {
        "status": status,
        "enabled": enabled,
        "configured": configured,
        "server_name": server_name,
        "tool_count": len(tools_list),
        "tools": tools_list,
        "activity": activities,
        "recent_activities": activities,
        "last_activity": last_act
    }

# --- ASTRA UPGRADE: MCP END ---


