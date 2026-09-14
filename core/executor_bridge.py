"""
Astra Assistant - Executor Bridge
WebSocket RPC connection manager between Astra server and Windows laptop executor.
Enables remote execution of PC-control tools with a 5-second timeout and offline detection.
"""

import os
import uuid
import asyncio
import concurrent.futures
from typing import Dict, Any, Optional
from core.logger import get_logger

logger = get_logger("astra.bridge")

DEFAULT_TOOL_TIMEOUTS = {
    "send_whatsapp_message": 25.0,
    "send_whatsapp_reply": 25.0,
    "start_dev_environment": 15.0,
    "analyze_screen": 15.0,
}


class ExecutorBridge:
    """
    Singleton managing active WebSocket RPC connections from local_executor.py.
    """

    def __init__(self):
        self._active_ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending_requests: Dict[str, asyncio.Future] = {}

    async def register(self, websocket, loop: Optional[asyncio.AbstractEventLoop] = None):
        """Register a newly connected laptop executor WebSocket."""
        if self._active_ws is not None and self._active_ws != websocket:
            logger.info("Replacing existing laptop executor connection.")
            try:
                await self._active_ws.close(code=1000)
            except Exception:
                pass
        self._active_ws = websocket
        self._loop = loop or asyncio.get_running_loop()
        logger.info("Laptop executor registered with ExecutorBridge.")

    async def unregister(self, websocket):
        """Unregister a disconnected laptop executor WebSocket."""
        if self._active_ws == websocket:
            self._active_ws = None
            logger.info("Laptop executor unregistered from ExecutorBridge.")
            # Fail any pending requests immediately
            for req_id, fut in list(self._pending_requests.items()):
                if not fut.done():
                    fut.set_result({
                        "success": False,
                        "status": "error",
                        "message": "Kshama karein, laptop executor disconnected. Command poori nahi ho saki.",
                        "message": "Sorry, laptop executor disconnected. Command could not be completed.",
                        "offline": True
                    })
            self._pending_requests.clear()

    def is_connected(self) -> bool:
        """Check if a laptop executor WebSocket client is currently connected and alive."""
        if self._active_ws is None:
            return False
        # Starlette WebSocket client_state check (3 = DISCONNECTED)
        client_state = getattr(self._active_ws, "client_state", None)
        if client_state is not None:
            if getattr(client_state, "name", "") == "DISCONNECTED" or getattr(client_state, "value", 1) == 3:
                return False
        return True

    def handle_response(self, data: Dict[str, Any]):
        """Handle incoming RPC response frame from laptop executor."""
        req_id = data.get("id")
        if not req_id:
            logger.warning(f"Received executor frame without 'id': {data}")
            return

        future = self._pending_requests.get(req_id)
        if future and not future.done():
            result = data.get("result", {})
            future.set_result(result)
        else:
            logger.warning(f"No pending future found for request id: {req_id}")

    async def execute_remote_tool(
        self,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0
    ) -> Dict[str, Any]:
        """
        Send a tool execution RPC request to the connected laptop executor.
        Enforces strict timeout and graceful offline error handling.
        """
        if timeout == 5.0 and tool_name in DEFAULT_TOOL_TIMEOUTS:
            timeout = DEFAULT_TOOL_TIMEOUTS[tool_name]

        if not self.is_connected():
            logger.warning(f"Attempted to call PC tool '{tool_name}' but executor is offline.")
            return {
                "success": False,
                "status": "error",
                "message": "Kshama karein, laptop executor offline hai. Kripya apne laptop par local_executor.py start karein.",
                "message": "Sorry, laptop executor is offline. Please start local_executor.py on your laptop.",
                "offline": True
            }

        req_id = str(uuid.uuid4())
        loop = self._loop or asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_requests[req_id] = future

        payload = {
            "id": req_id,
            "action": tool_name,
            "params": params or {}
        }

        try:
            await self._active_ws.send_json(payload)
            logger.info(f"Dispatched '{tool_name}' (ID: {req_id}) to laptop executor.")
        except Exception as e:
            self._pending_requests.pop(req_id, None)
            logger.error(f"Failed to send RPC request to executor: {e}")
            return {
                "success": False,
                "status": "error",
                "message": f"Kshama karein, laptop executor connection error: {e}",
                "message": f"Sorry, laptop executor connection error: {e}",
                "offline": True,
                "error": str(e)
            }

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"PC tool '{tool_name}' timed out after {timeout}s.")
            return {
                "success": False,
                "status": "error",
                "message": f"Kshama karein, laptop executor timed out ({int(timeout)}s). Laptop unreachable hai.",
                "message": f"Sorry, laptop executor timed out ({int(timeout)}s). Laptop is unreachable.",
                "timeout": True
            }
        except Exception as e:
            logger.error(f"Unexpected error waiting for executor response: {e}")
            return {
                "success": False,
                "status": "error",
                "message": f"Executor error: {e}",
                "error": str(e)
            }
        finally:
            self._pending_requests.pop(req_id, None)

    def dispatch_sync(
        self,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0
    ) -> Dict[str, Any]:
        """
        Synchronous wrapper for execute_remote_tool.
        Safe for use inside Gemini AFC functions and synchronous rule parsers.
        """
        if timeout == 5.0 and tool_name in DEFAULT_TOOL_TIMEOUTS:
            timeout = DEFAULT_TOOL_TIMEOUTS[tool_name]

        if not self.is_connected():
            return {
                "success": False,
                "status": "error",
                "message": "Kshama karein, laptop executor offline hai. Kripya apne laptop par local_executor.py start karein.",
                "message": "Sorry, laptop executor is offline. Please start local_executor.py on your laptop.",
                "offline": True
            }

        loop = self._loop
        if loop is None or not loop.is_running():
            try:
                return asyncio.run(self.execute_remote_tool(tool_name, params, timeout))
            except Exception as e:
                return {
                    "success": False,
                    "status": "error",
                    "message": f"Executor error: {e}",
                    "error": str(e)
                }

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        # Check if caller is on the same event loop - blocking with .result() causes deadlock!
        if current_loop is not None and loop is not None and current_loop is loop:
            import core.actions as actions
            func = getattr(actions, tool_name, None)
            if func:
                try:
                    return func(**(params or {}))
                except Exception as e:
                    logger.error(f"Local direct execution error for '{tool_name}': {e}")
                    return {"success": False, "status": "error", "message": str(e)}

        fut = asyncio.run_coroutine_threadsafe(
            self.execute_remote_tool(tool_name, params, timeout),
            loop
        )
        try:
            return fut.result(timeout=timeout + 1.0)
        except Exception as e:
            logger.warning(f"Executor dispatch_sync error ({e}); falling back to local action.")
            import core.actions as actions
            func = getattr(actions, tool_name, None)
            if func:
                try:
                    return func(**(params or {}))
                except Exception:
                    pass
            return {
                "success": False,
                "status": "error",
                "message": f"Executor error: {e}",
                "error": str(e)
            }


# Global singleton
executor_bridge = ExecutorBridge()


def dispatch_pc_tool_sync(
    tool_name: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 5.0
) -> Dict[str, Any]:
    """Helper function to dispatch PC tool synchronously."""
    return executor_bridge.dispatch_sync(tool_name, params, timeout)


async def dispatch_pc_tool_async(
    tool_name: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 5.0
) -> Dict[str, Any]:
    """Helper function to dispatch PC tool asynchronously."""
    return await executor_bridge.execute_remote_tool(tool_name, params, timeout)

