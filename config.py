import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

# Load .env from current directory
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)

# Gemini & AI Provider Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
PRIMARY_PROVIDER = os.getenv("PRIMARY_PROVIDER", os.getenv("AI_PROVIDER", "gemini")).strip().lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-lightning:free").strip()
USE_GEMINI_LIVE = os.getenv("USE_GEMINI_LIVE", "false").strip().lower() in ("true", "1", "yes")
GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-2.5-flash-native-audio-latest").strip() or "gemini-2.5-flash-native-audio-latest"

# Security & Authentication Token
ASTRA_AUTH_TOKEN = os.getenv("ASTRA_AUTH_TOKEN", "").strip()
if not ASTRA_AUTH_TOKEN:
    # Auto-generate a cryptographically secure 48-character hex token
    ASTRA_AUTH_TOKEN = secrets.token_hex(24)
    try:
        if ENV_PATH.exists():
            content = ENV_PATH.read_text(encoding="utf-8")
            if "ASTRA_AUTH_TOKEN=" in content:
                import re
                content = re.sub(r'ASTRA_AUTH_TOKEN=.*', f'ASTRA_AUTH_TOKEN={ASTRA_AUTH_TOKEN}', content)
            else:
                content += f"\nASTRA_AUTH_TOKEN={ASTRA_AUTH_TOKEN}\n"
            ENV_PATH.write_text(content, encoding="utf-8")
        else:
            ENV_PATH.write_text(f"ASTRA_AUTH_TOKEN={ASTRA_AUTH_TOKEN}\n", encoding="utf-8")
        os.environ["ASTRA_AUTH_TOKEN"] = ASTRA_AUTH_TOKEN
    except Exception:
        pass

# Voice configuration (Edge TTS Neural Voices & Tuning)
VOICE_HINDI = os.getenv("VOICE_HINDI", "hi-IN-SwaraNeural")
VOICE_ENGLISH = os.getenv("VOICE_ENGLISH", "en-IN-NeerjaNeural")
TTS_RATE = os.getenv("TTS_RATE", "+3%")
TTS_PITCH = os.getenv("TTS_PITCH", "+0Hz")

def _safe_float_env(var_name: str, default: float) -> float:
    raw = os.getenv(var_name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default

# Request & Operation Timeout Configurations (Seconds)
LLM_TIMEOUT = _safe_float_env("LLM_TIMEOUT", 30.0)
TTS_TIMEOUT = _safe_float_env("TTS_TIMEOUT", 15.0)
VOICE_TRANSCRIPTION_TIMEOUT = _safe_float_env("VOICE_TRANSCRIPTION_TIMEOUT", 15.0)
OPENROUTER_TIMEOUT = _safe_float_env("OPENROUTER_TIMEOUT", 20.0)

# Server Host & Port (Default to localhost 127.0.0.1 for local security)
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

# SSL / HTTPS Configuration (Native Local HTTPS Support)
_raw_key = os.getenv("SSL_KEYFILE", "").strip()
_raw_cert = os.getenv("SSL_CERTFILE", "").strip()

def _resolve_ssl_path(p: str) -> Optional[str]:
    if not p:
        return None
    path_obj = Path(p)
    if not path_obj.is_absolute():
        path_obj = BASE_DIR / path_obj
    return str(path_obj) if path_obj.exists() else None

SSL_KEYFILE = _resolve_ssl_path(_raw_key)
SSL_CERTFILE = _resolve_ssl_path(_raw_cert)
USE_SSL = bool(SSL_KEYFILE and SSL_CERTFILE)

_executor_host = "127.0.0.1" if HOST == "0.0.0.0" else HOST
_ws_scheme = "wss" if USE_SSL else "ws"
EXECUTOR_SERVER_URL = os.getenv("EXECUTOR_SERVER_URL", f"{_ws_scheme}://{_executor_host}:{PORT}/ws/executor")

def _detect_lan_ip():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None

LAN_IP = _detect_lan_ip()

# Cloudflare Tunnel configuration for mobile HTTPS
USE_TUNNEL = os.getenv("USE_TUNNEL", "false").strip().lower() == "true"

# Allowed CORS Origins - includes local loopback and LAN IP for mobile
configured_origins = os.getenv("ALLOWED_ORIGINS", "").strip()
if configured_origins:
    CORS_ORIGINS = [orig.strip() for orig in configured_origins.split(",") if orig.strip()]
else:
    CORS_ORIGINS = [
        f"http://localhost:{PORT}",
        f"http://127.0.0.1:{PORT}",
        "http://localhost",
        "http://127.0.0.1"
    ]
    if LAN_IP:
        CORS_ORIGINS.extend([
            f"http://{LAN_IP}:{PORT}",
            f"http://{LAN_IP}"
        ])

def add_cors_origin(origin: str):
    """Dynamically registers a new origin or domain into CORS_ORIGINS at runtime."""
    if not origin:
        return CORS_ORIGINS
    cleaned = origin.strip().rstrip("/")
    if cleaned and cleaned not in CORS_ORIGINS:
        CORS_ORIGINS.append(cleaned)
    return CORS_ORIGINS

# Static directories
STATIC_DIR = BASE_DIR / "static"
AUDIO_DIR = STATIC_DIR / "audio"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"

# Persistent Data & Web Push Notification Configuration
DATA_DIR = BASE_DIR / "data"
VAPID_KEY_FILE = DATA_DIR / "vapid_keys.json"
PUSH_SUBSCRIPTIONS_FILE = DATA_DIR / "push_subscriptions.json"
VAPID_CLAIMS_EMAIL = os.getenv("VAPID_CLAIMS_EMAIL", "mailto:admin@astra.local")

# WhatsApp Automation Configuration
WHATSAPP_PROFILE_DIR = BASE_DIR / ".whatsapp_profile"
WHATSAPP_AUDIT_LOG = BASE_DIR / "whatsapp_audit.log"
WHATSAPP_RATE_LIMIT_SECONDS = int(os.getenv("WHATSAPP_RATE_LIMIT_SECONDS", "60"))
WHATSAPP_HEADLESS = os.getenv("WHATSAPP_HEADLESS", "false").strip().lower() == "true"

# Instagram Automation Configuration
INSTAGRAM_PROFILE_DIR = DATA_DIR / "instagram_profile"
INSTAGRAM_AUDIT_LOG = BASE_DIR / "instagram_audit.log"
INSTAGRAM_USAGE_FILE = DATA_DIR / "instagram_usage.json"
INSTAGRAM_RATE_LIMIT_SECONDS = int(os.getenv("INSTAGRAM_RATE_LIMIT_SECONDS", "90"))
INSTAGRAM_MAX_DMS_PER_DAY = int(os.getenv("INSTAGRAM_MAX_DMS_PER_DAY", "20"))
INSTAGRAM_HEADLESS = os.getenv("INSTAGRAM_HEADLESS", "false").strip().lower() == "true"

# Logging Configuration
LOGS_DIR = BASE_DIR / "logs"
LOG_FILE = LOGS_DIR / "astra.log"


# Ensure runtime directories exist
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
WHATSAPP_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
INSTAGRAM_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
