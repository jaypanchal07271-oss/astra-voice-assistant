"""
Astra AI Voice Assistant - FastAPI Backend Server
Provides secured REST API endpoints, session management, and static UI delivery.
"""

import os
import sys
import socket
import warnings
import re
import time
from pathlib import Path
import asyncio
from typing import Optional, Dict, Any

# Suppress harmless Python 3.14 deprecation warnings & Google SDK advisory notices
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*automatic function calling.*")

# Silence Windows asyncio WinError 10054 bug in Python ProactorEventLoop
if sys.platform == "win32":
    try:
        import asyncio.proactor_events
        _orig_call_connection_lost = asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost

        def _patched_call_connection_lost(self, exc):
            if getattr(self, '_called_connection_lost', False):
                return
            try:
                if hasattr(self, '_protocol') and self._protocol is not None:
                    self._protocol.connection_lost(exc)
            finally:
                if hasattr(self, '_sock') and self._sock is not None:
                    if hasattr(self._sock, 'shutdown') and self._sock.fileno() != -1:
                        try:
                            self._sock.shutdown(socket.SHUT_RDWR)
                        except (OSError, ConnectionResetError):
                            pass
                    try:
                        self._sock.close()
                    except Exception:
                        pass
                    self._sock = None
                server = getattr(self, '_server', None)
                if server is not None:
                    try:
                        server._detach(self)
                    except Exception:
                        pass
                    self._server = None
                self._called_connection_lost = True

        asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost = _patched_call_connection_lost
    except Exception:
        pass

from fastapi import (
    FastAPI, Request, Response, HTTPException, Depends, Header,
    WebSocket, WebSocketDisconnect, Query, UploadFile, File, Form
)
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import (
    STATIC_DIR, AUDIO_DIR, GEMINI_API_KEY, HOST, PORT,
    ASTRA_AUTH_TOKEN, CORS_ORIGINS, LLM_TIMEOUT, TTS_TIMEOUT,
    VOICE_TRANSCRIPTION_TIMEOUT, USE_GEMINI_LIVE, GEMINI_LIVE_MODEL
)
from core.brain import process_voice_command, run_live_session, session_manager, get_ai_provider_status
from core.tts import generate_audio
import core.actions as actions
from core.logger import get_logger, get_recent_logs, redact_params
from core.executor_bridge import executor_bridge

logger = get_logger("astra.app")

app = FastAPI(
    title="Astra - Gemini AI Voice Assistant",
    version="2.0.0",
    description="Secured, local AI voice assistant with laptop automation and vision."
)

# Secure CORS configuration (supports localhost, LAN IP, and Cloudflare Tunnels)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"^https://[a-zA-Z0-9-]+\.trycloudflare\.com$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Ensure static directories exist and mount static files
STATIC_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# =====================================================================
# Security & Token Authentication Dependency
# =====================================================================

def verify_token(request: Request, x_astra_token: Optional[str] = Header(None)) -> str:
    """
    Validates that incoming requests supply the correct security token
    via X-Astra-Token header or authenticated session cookie.
    Prevents unauthorized LAN/network access to laptop control endpoints.
    """
    token = x_astra_token or request.cookies.get("astra_session_token")
    if not token or token != ASTRA_AUTH_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Valid Astra security token required."
        )
    return token


# =====================================================================
# Request / Response Schemas
# =====================================================================

class ChatRequest(BaseModel):
    text: str
    lang: Optional[str] = "hi-IN"
    session_id: Optional[str] = "default"
    is_mobile: Optional[bool] = None


class ApiKeyRequest(BaseModel):
    api_key: str


class PushSubscribeRequest(BaseModel):
    subscription: Dict[str, Any]


class PushTestRequest(BaseModel):
    title: Optional[str] = "Astra Test Push"
    body: Optional[str] = "Hello from Astra Assistant!"


def validate_audio_file(audio_url: Optional[str]) -> bool:
    """Verifies that generated audio URL points to an existing, non-empty audio file."""
    if not audio_url or not isinstance(audio_url, str):
        return False
    try:
        if audio_url.startswith("/static/"):
            rel_path = audio_url.lstrip("/")
            file_path = Path(__file__).resolve().parent / rel_path
        else:
            file_path = Path(audio_url)
        return file_path.is_file() and file_path.stat().st_size > 0
    except Exception:
        return False


def get_safe_timeout(var_name: str, default: float) -> float:
    """Safely retrieves a float timeout from environment with resilient fallback."""
    try:
        val = os.getenv(var_name)
        return float(val) if val is not None else float(default)
    except (ValueError, TypeError):
        return float(default)


# =====================================================================
# API Endpoints
# =====================================================================

@app.get("/", response_class=HTMLResponse)
async def serve_home():
    """Serves the frontend UI and establishes a secure authenticated session."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        html = index_file.read_text(encoding="utf-8")
        # Securely embed session token into meta tag for the local client
        token_meta = f'<meta name="astra-token" content="{ASTRA_AUTH_TOKEN}">\n</head>'
        html = html.replace("</head>", token_meta)

        response = HTMLResponse(content=html)
        response.set_cookie(
            key="astra_session_token",
            value=ASTRA_AUTH_TOKEN,
            httponly=True,
            samesite="lax",
            max_age=86400 * 30
        )
        return response
    return HTMLResponse("<h2>Astra AI Voice Assistant is Running. Index file not found.</h2>")


@app.get("/manifest.json")
async def serve_manifest():
    """Serves PWA manifest file."""
    manifest_file = STATIC_DIR / "manifest.json"
    if manifest_file.exists():
        return FileResponse(manifest_file, media_type="application/manifest+json")
    raise HTTPException(status_code=404, detail="Manifest not found")


@app.get("/sw.js")
async def serve_service_worker():
    """Serves PWA service worker with root scope."""
    sw_file = STATIC_DIR / "sw.js"
    if sw_file.exists():
        return FileResponse(
            sw_file,
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"}
        )
    raise HTTPException(status_code=404, detail="Service worker not found")


@app.get("/api/status")
async def get_status():
    """Returns assistant operational status."""
    current_key = os.getenv("GEMINI_API_KEY", "").strip() or GEMINI_API_KEY
    has_key = bool(current_key and current_key != "your_gemini_api_key_here")
    active_tunnel = ""
    try:
        from start_tunnel import _active_tunnel_url
        active_tunnel = _active_tunnel_url or os.getenv("CLOUDFLARE_TUNNEL_URL", "")
    except Exception:
        pass

    mcp_info = {"status": "Not Configured", "tool_count": 0}
    try:
        from core.mcp_client import get_mcp_status
        mcp_data = get_mcp_status()
        mcp_info = {"status": mcp_data["status"], "tool_count": mcp_data["tool_count"]}
    except Exception:
        pass

    ai_status = get_ai_provider_status()

    return {
        "status": "online",
        "has_api_key": has_key,
        "mode": "Gemini AI (Tools & Memory Active)" if has_key else "Offline / Rule-based Fallback",
        "auth_enabled": True,
        "executor_connected": executor_bridge.is_connected(),
        "tunnel_url": active_tunnel,
        "mcp": mcp_info,
        "ai_status": ai_status,
        "degraded_mode": ai_status.get("degraded_mode", False)
    }


@app.get("/api/mcp/status")
async def get_mcp_status_endpoint():
    """Returns verified real-time status, tool list, and activity log for the MCP Tool Bridge."""
    try:
        from core.mcp_client import get_mcp_status
        return JSONResponse(get_mcp_status(), status_code=200)
    except Exception as e:
        return JSONResponse({

            "status": "Not Configured",
            "enabled": False,
            "configured": False,
            "server_name": "None",
            "tool_count": 0,
            "tools": [],
            "activity": [],
            "last_activity": "No MCP activity yet."
        }, status_code=200)


@app.websocket("/ws/executor")
async def websocket_executor_endpoint(websocket: WebSocket, token: Optional[str] = Query(None)):
    """
    WebSocket RPC endpoint for the local Windows laptop executor (local_executor.py).
    Authenticates incoming connection against ASTRA_AUTH_TOKEN via query parameter.
    """
    if not token or token != ASTRA_AUTH_TOKEN:
        logger.warning("Rejected unauthorized executor WebSocket connection.")
        await websocket.close(code=1008)  # Policy violation
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    await executor_bridge.register(websocket, loop)
    logger.info("Local PC executor connected via WebSocket.")
    try:
        while True:
            data = await websocket.receive_json()
            executor_bridge.handle_response(data)
    except WebSocketDisconnect:
        logger.info("Local PC executor disconnected.")
    except Exception as e:
        logger.error(f"Error in executor WebSocket connection: {e}")
    finally:
        await executor_bridge.unregister(websocket)


@app.post("/api/save_key", dependencies=[Depends(verify_token)])
async def save_api_key(req: ApiKeyRequest):
    """Saves and updates Gemini API key in runtime and .env (Requires Authentication)."""
    new_key = req.api_key.strip()
    if not new_key:
        return JSONResponse({"success": False, "message": "API key cannot be empty"}, status_code=400)

    env_file = Path(__file__).resolve().parent / ".env"
    try:
        if env_file.exists():
            content = env_file.read_text(encoding="utf-8")
            lines = content.splitlines()
            found = False
            new_lines = []
            for line in lines:
                if re.match(r'^\s*(?:export\s+)?GEMINI_API_KEY\s*=', line):
                    new_lines.append(f"GEMINI_API_KEY={new_key}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"GEMINI_API_KEY={new_key}")
            new_content = "\n".join(new_lines) + "\n"
        else:
            new_content = f"GEMINI_API_KEY={new_key}\nHOST={HOST}\nPORT={PORT}\n"

        env_file.write_text(new_content, encoding="utf-8")

        # Verify .env was successfully written and contains the expected key
        saved_content = env_file.read_text(encoding="utf-8")
        if f"GEMINI_API_KEY={new_key}" not in saved_content:
            raise IOError("Verification failed: saved .env did not match expected value")

        # Update runtime environment ONLY after successful persistence and verification
        os.environ["GEMINI_API_KEY"] = new_key
        logger.info("Gemini API key successfully persisted to .env and updated in runtime environment.")
        return {"success": True, "message": "API Key successfully updated and saved!"}

    except Exception as e:
        logger.error(f"Failed to save API key to .env: {type(e).__name__}")
        return JSONResponse(
            {"success": False, "message": "Failed to save API key."},
            status_code=500
        )


@app.get("/api/logs/recent", dependencies=[Depends(verify_token)])
async def get_recent_logs_endpoint(limit: int = 50):
    """
    Returns the last 50 structured JSON log entries for UI/system debugging.
    Guarded by Astra security token authentication.
    """
    safe_limit = max(1, min(int(limit), 200))
    entries = get_recent_logs(limit=safe_limit)
    return {
        "success": True,
        "count": len(entries),
        "logs": entries
    }


@app.post("/api/chat", dependencies=[Depends(verify_token)])
async def handle_chat(req: ChatRequest, request: Request):
    """
    Main integration endpoint:
    Frontend Mic -> FastAPI (Authenticated) -> Gemini Brain (Memory & Tools) -> Edge-TTS Audio -> JSON
    """
    t0 = time.perf_counter()
    session_id = req.session_id or "default"
    user_text = req.text.strip() if (req.text and isinstance(req.text, str)) else ""

    if not user_text:
        logger.warning(
            "Empty or whitespace command received at /api/chat. Returning transcription failure.",
            extra={"session_id": session_id, "operation": "chat_text_validation"}
        )
        return JSONResponse({
            "success": False,
            "error": "transcription_failed",
            "message": "I couldn't understand the voice input. Please try again.",
            "detail": "Empty or whitespace-only command",
            "transcript": "",
            "transcription": "",
            "reply": "I could not clearly hear your command. Please try again.",
            "audio_url": None,
            "action": None
        }, status_code=200)

    # Log incoming transcript before intent processing
    logger.info(f"[STT] Transcript: {user_text}")
    logger.info(f"[Router] Received transcript: {user_text}")
    print(f"[STT] Transcript: {user_text}")
    print(f"[Router] Received transcript: {user_text}")

    # Detect client device (Mobile vs PC) from User-Agent with optional frontend override
    user_agent = request.headers.get("user-agent", "").lower()
    ua_is_mobile = any(x in user_agent for x in ["android", "iphone", "ipad", "ipod", "mobile"])
    is_mobile = req.is_mobile if req.is_mobile is not None else ua_is_mobile
    from core.brain import session_manager
    session_manager.set_user_data(session_id, "device", "Mobile (Smartphone)" if is_mobile else "PC / Laptop")
    session_manager.set_user_data(session_id, "is_mobile", is_mobile)
    try:
        brain_result = await asyncio.wait_for(
            process_voice_command(user_text, session_id=session_id),
            timeout=get_safe_timeout("LLM_TIMEOUT", LLM_TIMEOUT)
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"process_voice_command timed out after {LLM_TIMEOUT}s",
            extra={"session_id": session_id, "operation": "brain_timeout"}
        )
        brain_result = {
            "reply": "Kshama karein, command process hone mein zyaada samay lag gaya. Kripya dobara koshish karein.",
            "action": {"status": "timeout", "error": "Request timed out"}
        }
    except Exception as e:
        logger.error(f"Critical error in process_voice_command: {e}")
        brain_result = {
            "reply": "Sorry (Kshama karein), a technical error prevented this command from completing.",
            "action": {"status": "error", "error": str(e)}
        }
    reply_text = brain_result.get("reply", "Task completed successfully.")
    action_info = brain_result.get("action")

    # 2. Convert response to spoken audio via Edge-TTS
    audio_url = None
    tts_error = False
    req_lang = req.lang or "en-IN"
    try:
        raw_audio_url = await asyncio.wait_for(
            generate_audio(reply_text, lang=req_lang),
            timeout=get_safe_timeout("TTS_TIMEOUT", TTS_TIMEOUT)
        )
        if validate_audio_file(raw_audio_url):
            audio_url = raw_audio_url
        else:
            tts_error = True
            logger.warning(
                "TTS audio file validation failed: file missing or empty",
                extra={"session_id": session_id, "operation": "tts_validation", "lang": req_lang}
            )
    except (asyncio.TimeoutError, TimeoutError):
        tts_error = True
        logger.warning(
            f"TTS generation timed out after {TTS_TIMEOUT}s",
            extra={"session_id": session_id, "operation": "tts_timeout", "lang": req_lang}
        )
    except Exception as e:
        tts_error = True
        logger.error(
            f"TTS audio synthesis failed: {type(e).__name__}",
            extra={"session_id": session_id, "operation": "tts_generation", "lang": req_lang}
        )

    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    logger.info(
        "Backend chat request ended",
        extra={
            "session_id": session_id,
            "latency_ms": latency_ms,
            "path": "/api/chat",
            "method": "POST",
            "status_code": 200,
            "tts_error": tts_error
        }
    )

    # 3. Return response with audio url, transcript and action details
    res_payload = {
        "success": True,
        "reply": reply_text,
        "audio_url": audio_url,
        "action": action_info,
        "transcript": user_text,
        "transcription": user_text,
        "tts_failed": tts_error
    }
    if brain_result.get("degraded_mode"):
        res_payload["degraded_mode"] = True
        res_payload["ai_status"] = brain_result.get("ai_status", "degraded")
        res_payload["degraded_reason"] = brain_result.get("degraded_reason", "")
    if tts_error:
        res_payload["tts_error"] = True
    return res_payload


@app.get("/api/auth/token", dependencies=[Depends(verify_token)])
async def get_session_token_status():
    """
    Verifies current session authentication status.
    Reachable from PWA shell and secured by HttpOnly cookie / token header.
    """
    return {
        "authenticated": True,
        "token": ASTRA_AUTH_TOKEN
    }


@app.get("/api/push/vapid_public_key")
async def get_vapid_public_key():
    """
    Returns the VAPID public key (base64url uncompressed P-256 EC point)
    for registering Web Push subscriptions on the client device.
    """
    from core.push_service import get_public_vapid_key
    public_key = get_public_vapid_key()
    return {"public_key": public_key}


@app.post("/api/push/subscribe", dependencies=[Depends(verify_token)])
async def handle_push_subscribe(payload: PushSubscribeRequest):
    """
    Registers a client device push subscription for Web Push notifications.
    """
    from core.push_service import add_subscription
    result = add_subscription(payload.subscription)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Invalid subscription"))
    return result


@app.post("/api/push/test", dependencies=[Depends(verify_token)])
async def handle_push_test(payload: Optional[PushTestRequest] = None):
    """
    Dispatches an immediate test push notification to all subscribed devices.
    """
    from core.push_service import send_broadcast_push
    title = payload.title if payload and payload.title else "Astra Test Push"
    body = payload.body if payload and payload.body else "Hello from Astra Assistant!"
    result = send_broadcast_push(title=title, body=body, tag="astra-test")
    return {"success": True, "result": result}


@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    """
    Gemini Live API Bidirectional Streaming WebSocket Endpoint:
    Authenticates on the first message frame with a 5-second timeout:
      Client sends: {"type": "auth", "token": "..."}
      Server responds: {"type": "authenticated"}
    Client then sends commands:
      {"text": "notepad kholo", "session_id": "...", "lang": "hi-IN", "is_mobile": false}
    Server streams frames:
      {"type": "start", "session_id": "...", "transcript": "..."}
      {"type": "text_chunk", "delta": "..."}
      {"type": "action", "tool": "...", "args": {...}, "result": {...}}
      {"type": "done", "reply": "...", "action": {...}, "audio_url": "..."}
    """
    await websocket.accept()
    logger.info("[WebSocket /ws/live] Client connected. Waiting for auth frame...")

    try:
        auth_data = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
    except (asyncio.TimeoutError, Exception) as auth_err:
        logger.warning(f"[WebSocket /ws/live] Authentication timed out or failed: {auth_err}")
        try:
            await websocket.close(code=1008, reason="Authentication timeout or invalid frame")
        except Exception:
            pass
        return

    if not isinstance(auth_data, dict) or auth_data.get("type") != "auth" or auth_data.get("token") != ASTRA_AUTH_TOKEN:
        logger.warning("[WebSocket /ws/live] Unauthorized connection attempt rejected.")
        try:
            await websocket.close(code=1008, reason="Unauthorized: Invalid security token")
        except Exception:
            pass
        return

    await websocket.send_json({"type": "authenticated"})
    logger.info("[WebSocket /ws/live] Client authenticated successfully.")

    try:
        while True:
            data = await websocket.receive_json()
            user_text = (data.get("text") or "").strip()
            session_id = (data.get("session_id") or "default").strip()
            req_lang = (data.get("lang") or "hi-IN").strip()

            if not user_text:
                await websocket.send_json({
                    "type": "error",
                    "error": "empty_command",
                    "reply": "I could not hear your command. Please try again."
                })
                continue

            await websocket.send_json({
                "type": "start",
                "session_id": session_id,
                "transcript": user_text
            })

            async def on_text_chunk(chunk_str: str):
                try:
                    await websocket.send_json({"type": "text_chunk", "delta": chunk_str})
                except Exception:
                    pass

            async def on_tool_call(tool_name: str, args: Dict[str, Any], result: Any):
                try:
                    await websocket.send_json({"type": "action", "tool": tool_name, "args": args, "result": result})
                except Exception:
                    pass

            ua = (websocket.headers.get("user-agent") or "").lower()
            ua_is_mobile = any(x in ua for x in ["android", "iphone", "ipad", "ipod", "mobile"])
            client_is_mobile = data.get("is_mobile")
            is_mobile = client_is_mobile if client_is_mobile is not None else ua_is_mobile
            session_manager.set_user_data(session_id, "device", "Mobile (Smartphone)" if is_mobile else "PC / Laptop")
            session_manager.set_user_data(session_id, "is_mobile", is_mobile)

            res = await run_live_session(
                user_text=user_text,
                session_id=session_id,
                on_text_chunk=on_text_chunk,
                on_tool_call=on_tool_call,
                is_mobile=is_mobile
            )

            reply_text = res.get("reply", "")
            action_info = res.get("action")

            audio_url = None
            try:
                raw_audio_url = await asyncio.wait_for(
                    generate_audio(reply_text, lang=req_lang),
                    timeout=get_safe_timeout("TTS_TIMEOUT", TTS_TIMEOUT)
                )
                if validate_audio_file(raw_audio_url):
                    audio_url = raw_audio_url
            except Exception:
                pass

            await websocket.send_json({
                "type": "done",
                "reply": reply_text,
                "action": action_info,
                "audio_url": audio_url,
                "degraded_mode": res.get("degraded_mode", False),
                "ai_status": res.get("ai_status", "ready")
            })

    except WebSocketDisconnect:
        logger.info("[WebSocket /ws/live] Client disconnected.")
    except Exception as e:
        logger.error(f"[WebSocket /ws/live] Error in live session: {e}")
        try:
            await websocket.close(code=1011, reason=str(e))
        except Exception:
            pass
    finally:
        logger.info("[WebSocket /ws/live] Session cleanup completed.")


@app.post("/api/voice_upload", dependencies=[Depends(verify_token)])
async def handle_voice_upload(
    request: Request,
    audio_file: UploadFile = File(...),
    session_id: Optional[str] = Form("default"),
    lang: Optional[str] = Form("hi-IN"),
    is_mobile: Optional[bool] = Form(None)
):
    """
    Fallback record-and-upload endpoint for mobile browsers where the Web Speech API
    is unsupported or restricted (e.g. iOS Safari).
    Accepts recorded audio chunks, transcribes them, and processes via Gemini Brain.
    """
    t0 = time.perf_counter()
    user_agent = request.headers.get("user-agent", "").lower()
    ua_is_mobile = any(x in user_agent for x in ["android", "iphone", "ipad", "ipod", "mobile"])
    client_is_mobile = is_mobile if is_mobile is not None else ua_is_mobile
    active_sid = session_id or "default"
    from core.brain import session_manager
    session_manager.set_user_data(active_sid, "device", "Mobile (Smartphone)" if client_is_mobile else "PC / Laptop")
    session_manager.set_user_data(active_sid, "is_mobile", client_is_mobile)
    try:
        content = await audio_file.read()
        logger.info(
            "Backend voice upload request started",
            extra={
                "session_id": active_sid,
                "endpoint": "/api/voice_upload",
                "audio_filename": audio_file.filename,
                "content_type": audio_file.content_type,
                "payload_bytes": len(content) if content else 0
            }
        )

        if not content or len(content) < 100:
            logger.warning(
                "Voice upload rejected: empty or tiny audio payload",
                extra={"session_id": active_sid, "operation": "voice_upload_payload", "bytes": len(content) if content else 0}
            )
            return JSONResponse({
                "success": False,
                "error": "transcription_failed",
                "message": "I couldn't understand the voice input. Please try again.",
                "detail": "Empty or corrupt audio payload",
                "transcript": "",
                "transcription": "",
                "reply": "I could not clearly hear your command. Please try again.",
                "audio_url": None,
                "action": None
            }, status_code=200)

        # Detect and normalize supported audio MIME type
        raw_mime = (audio_file.content_type or "").split(";")[0].strip().lower()
        fname = (audio_file.filename or "").lower()
        if "mp4" in raw_mime or fname.endswith(".mp4") or fname.endswith(".m4a"):
            mime_type = "audio/mp4"
        elif "ogg" in raw_mime or fname.endswith(".ogg") or fname.endswith(".opus"):
            mime_type = "audio/ogg"
        elif "wav" in raw_mime or fname.endswith(".wav"):
            mime_type = "audio/wav"
        else:
            mime_type = "audio/webm"

        transcription = ""
        api_key = os.getenv("GEMINI_API_KEY", "").strip() or GEMINI_API_KEY
        if api_key and api_key != "your_gemini_api_key_here":
            try:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=api_key)
                prompt = (
                    "Transcribe the ENTIRE audio recording accurately into text. "
                    "Do not summarize. Do not truncate. Preserve every single spoken word. "
                    "The user may speak in Hindi, English, or Hinglish. "
                    "Return ONLY the spoken transcription words with no extra explanation."
                )
                transcription_timeout = get_safe_timeout("VOICE_TRANSCRIPTION_TIMEOUT", VOICE_TRANSCRIPTION_TIMEOUT)
                primary_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
                trans_models = [primary_model]
                for alt in ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash", "gemini-flash-latest"]:
                    if alt not in trans_models:
                        trans_models.append(alt)

                response = None
                for t_model in trans_models:
                    try:
                        response = await asyncio.wait_for(
                            asyncio.to_thread(
                                client.models.generate_content,
                                model=t_model,
                                contents=[
                                    types.Part.from_bytes(data=content, mime_type=mime_type),
                                    prompt
                                ]
                            ),
                            timeout=transcription_timeout
                        )
                        if response and response.text:
                            transcription = response.text.strip()
                            break
                    except Exception as t_err:
                        err_str = str(t_err)
                        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "retryDelay" in err_str:
                            logger.warning(f"Transcription model '{t_model}' rate-limited/quota exhausted. Trying fallback...")
                            continue
                        elif "not found" in err_str.lower():
                            logger.warning(f"Transcription model '{t_model}' not found. Trying fallback...")
                            continue
                        else:
                            logger.warning(f"Transcription model '{t_model}' error ({t_err}). Trying fallback...")
                            continue
            except asyncio.TimeoutError:
                logger.warning(
                    f"Audio transcription via Gemini timed out after {transcription_timeout}s",
                    extra={"session_id": active_sid, "operation": "gemini_transcription_timeout"}
                )
                transcription = ""
            except Exception as e:
                logger.warning(
                    f"Audio transcription via Gemini failed: {e}",
                    extra={"session_id": active_sid, "operation": "gemini_transcription"}
                )

        # Validate transcription before processing: Ensure string, strip whitespace, ensure non-empty
        # CRITICAL: Never replace failed/empty transcription with a fake command like 'who are you'.
        if not transcription or not isinstance(transcription, str) or not transcription.strip():
            logger.info(
                "Voice upload transcription returned no text or failed. Aborting execution without command processing.",
                extra={"session_id": active_sid, "operation": "voice_upload_transcription"}
            )
            return JSONResponse({
                "success": False,
                "error": "transcription_failed",
                "message": "I couldn't understand the voice input. Please try again.",
                "detail": "Voice transcription failed or no speech detected",
                "transcript": "",
                "transcription": "",
                "reply": "I could not clearly hear your command. Please try again.",
                "audio_url": None,
                "action": None
            }, status_code=200)

        cleaned_transcription = transcription.strip()

        # Route through Gemini Brain with session memory
        try:
            brain_result = await asyncio.wait_for(
                process_voice_command(cleaned_transcription, session_id=session_id),
                timeout=get_safe_timeout("LLM_TIMEOUT", LLM_TIMEOUT)
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Voice upload process_voice_command timed out after {LLM_TIMEOUT}s",
                extra={"session_id": session_id, "operation": "voice_upload_brain_timeout"}
            )
            brain_result = {
                "reply": "Kshama karein, command process hone mein zyaada samay lag gaya. Kripya dobara koshish karein.",
                "action": {"status": "timeout", "error": "Request timed out"}
            }
        except Exception as e:
            logger.error(f"Critical error in voice upload process_voice_command: {e}")
            brain_result = {
                "reply": "Sorry (Kshama karein), a technical error prevented this command from completing.",
                "action": {"status": "error", "error": str(e)}
            }
        reply_text = brain_result.get("reply", "Task completed successfully.")
        action_info = brain_result.get("action")

        # Convert to audio via Edge-TTS
        audio_url = None
        tts_error = False
        target_lang = lang or "en-IN"
        try:
            raw_audio_url = await asyncio.wait_for(
                generate_audio(reply_text, lang=target_lang),
                timeout=get_safe_timeout("TTS_TIMEOUT", TTS_TIMEOUT)
            )
            if validate_audio_file(raw_audio_url):
                audio_url = raw_audio_url
            else:
                tts_error = True
                logger.warning(
                    "Voice upload TTS audio validation failed: file missing or empty",
                    extra={"session_id": session_id, "operation": "tts_validation", "lang": target_lang}
                )
        except (asyncio.TimeoutError, TimeoutError):
            tts_error = True
            logger.warning(
                f"Voice upload TTS generation timed out after {TTS_TIMEOUT}s",
                extra={"session_id": session_id, "operation": "tts_timeout", "lang": target_lang}
            )
        except Exception as e:
            tts_error = True
            logger.error(
                f"Voice upload TTS synthesis failed: {type(e).__name__}",
                extra={"session_id": session_id, "operation": "tts_generation", "lang": target_lang}
            )

        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            "Backend voice upload request ended",
            extra={
                "session_id": session_id,
                "latency_ms": latency_ms,
                "path": "/api/voice_upload",
                "transcription": cleaned_transcription,
                "status_code": 200,
                "tts_error": tts_error
            }
        )

        res_payload = {
            "success": True,
            "reply": reply_text,
            "audio_url": audio_url,
            "action": action_info,
            "transcript": cleaned_transcription,
            "transcription": cleaned_transcription,
            "tts_failed": tts_error
        }
        if tts_error:
            res_payload["tts_error"] = True
        return res_payload

    except Exception as e:
        logger.error(f"Error handling voice upload: {e}")
        return JSONResponse({
            "success": False,
            "error": "transcription_failed",
            "message": "I couldn't understand the voice input. Please try again.",
            "detail": "Internal processing error during voice upload",
            "transcript": "",
            "transcription": "",
            "reply": "I could not clearly hear your command. Please try again.",
            "audio_url": None,
            "action": None
        }, status_code=200)


if __name__ == "__main__":
    import uvicorn
    from config import USE_SSL, SSL_KEYFILE, SSL_CERTFILE
    ssl_kwargs = {}
    if USE_SSL:
        ssl_kwargs["ssl_keyfile"] = SSL_KEYFILE
        ssl_kwargs["ssl_certfile"] = SSL_CERTFILE
    uvicorn.run("app:app", host=HOST, port=PORT, reload=False, **ssl_kwargs)
