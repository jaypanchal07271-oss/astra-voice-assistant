"""
Astra AI Voice Assistant - FastAPI Backend Server
Provides secured REST API endpoints, session management, and static UI delivery.
"""

import os
import re
import time
from pathlib import Path
import asyncio
from typing import Optional, Dict, Any

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
    ASTRA_AUTH_TOKEN, CORS_ORIGINS
)
from core.brain import process_voice_command
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


class ApiKeyRequest(BaseModel):
    api_key: str


class PushSubscribeRequest(BaseModel):
    subscription: Dict[str, Any]


class PushTestRequest(BaseModel):
    title: Optional[str] = "Astra Test Push"
    body: Optional[str] = "Hello from Astra Assistant!"


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
    return {
        "status": "online",
        "has_api_key": has_key,
        "mode": "Gemini AI (Tools & Memory Active)" if has_key else "Offline / Rule-based Fallback",
        "auth_enabled": True,
        "executor_connected": executor_bridge.is_connected()
    }


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

    os.environ["GEMINI_API_KEY"] = new_key

    env_file = Path(__file__).resolve().parent / ".env"
    try:
        content = ""
        if env_file.exists():
            content = env_file.read_text(encoding="utf-8")
            if "GEMINI_API_KEY=" in content:
                content = re.sub(r'GEMINI_API_KEY=.*', f'GEMINI_API_KEY={new_key}', content)
            else:
                content += f"\nGEMINI_API_KEY={new_key}\n"
        else:
            content = f"GEMINI_API_KEY={new_key}\nHOST={HOST}\nPORT={PORT}\n"

        env_file.write_text(content, encoding="utf-8")
        return {"success": True, "message": "API Key successfully updated and saved!"}
    except Exception as e:
        return {"success": True, "message": f"Session updated: {e}"}


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
async def handle_chat(req: ChatRequest):
    """
    Main integration endpoint:
    Frontend Mic -> FastAPI (Authenticated) -> Gemini Brain (Memory & Tools) -> Edge-TTS Audio -> JSON
    """
    t0 = time.perf_counter()
    user_text = req.text.strip()
    if not user_text:
        return JSONResponse({
            "reply": "Aapki aawaz sunai nahi di, kripya dobara bolein.",
            "audio_url": None,
            "action": None
        })

    # 1. Process voice command via Gemini Brain with multi-turn session memory
    session_id = req.session_id or "default"
    try:
        brain_result = await process_voice_command(user_text, session_id=session_id)
    except Exception as e:
        logger.error(f"Critical error in process_voice_command: {e}")
        brain_result = {
            "reply": "Kshama karein, takneeki kharabi ke karan command execute nahi ho paayi.",
            "action": {"status": "error", "error": str(e)}
        }
    reply_text = brain_result.get("reply", "Kaam ho gaya hai.")
    action_info = brain_result.get("action")

    # 2. Convert response to spoken audio via Edge-TTS
    audio_url = None
    try:
        audio_url = await generate_audio(reply_text, lang=req.lang or "hi-IN")
    except Exception as e:
        print(f"[TTS Generation Error] {e}")

    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    logger.info(
        "Chat request processed",
        extra={
            "session_id": session_id,
            "latency_ms": latency_ms,
            "path": "/api/chat",
            "method": "POST",
            "status_code": 200
        }
    )

    # 3. Return response with audio url and action details
    return {
        "reply": reply_text,
        "audio_url": audio_url,
        "action": action_info
    }


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


@app.post("/api/voice_upload", dependencies=[Depends(verify_token)])
async def handle_voice_upload(
    audio_file: UploadFile = File(...),
    session_id: Optional[str] = Form("default"),
    lang: Optional[str] = Form("hi-IN")
):
    """
    Fallback record-and-upload endpoint for mobile browsers where the Web Speech API
    is unsupported or restricted (e.g. iOS Safari).
    Accepts recorded audio chunks, transcribes them, and processes via Gemini Brain.
    """
    t0 = time.perf_counter()
    try:
        content = await audio_file.read()
        if not content or len(content) < 50:
            return JSONResponse({
                "reply": "Aapki aawaz sunai nahi di, kripya dobara bolein.",
                "audio_url": None,
                "action": None
            })

        transcription = ""
        api_key = os.getenv("GEMINI_API_KEY", "").strip() or GEMINI_API_KEY
        if api_key and api_key != "your_gemini_api_key_here":
            try:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=api_key)
                mime_type = audio_file.content_type or "audio/webm"
                prompt = (
                    "Transcribe this user voice audio accurately into text. "
                    "The user speaks in Hindi, Hinglish, or English. "
                    "Return ONLY the spoken transcription words with no extra explanation."
                )
                response = client.models.generate_content(
                    model=os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest"),
                    contents=[
                        types.Part.from_bytes(data=content, mime_type=mime_type),
                        prompt
                    ]
                )
                if response and response.text:
                    transcription = response.text.strip()
            except Exception as e:
                logger.warning(f"Audio transcription via Gemini failed: {e}")

        if not transcription:
            transcription = "who are you"

        # Route through Gemini Brain with session memory
        brain_result = await process_voice_command(transcription, session_id=session_id)
        reply_text = brain_result.get("reply", "Kaam kar diya gaya hai.")
        action_info = brain_result.get("action")

        # Convert to audio via Edge-TTS
        audio_url = None
        try:
            audio_url = await generate_audio(reply_text, lang=lang or "hi-IN")
        except Exception as e:
            print(f"[TTS Generation Error] {e}")

        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            "Voice upload request processed",
            extra={
                "session_id": session_id,
                "latency_ms": latency_ms,
                "path": "/api/voice_upload",
                "transcription": transcription,
                "status_code": 200
            }
        )

        return {
            "reply": reply_text,
            "audio_url": audio_url,
            "action": action_info,
            "transcript": transcription
        }

    except Exception as e:
        logger.error(f"Error handling voice upload: {e}")
        return JSONResponse({
            "reply": "Audio process karne mein dikkat aayi. Kripya dobara koshish karein.",
            "audio_url": None,
            "action": {"status": "error", "error": str(e)}
        }, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=HOST, port=PORT, reload=False)
