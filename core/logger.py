"""
Astra Assistant - Structured JSON Logging & Observability Engine
Provides JSON-formatted rotating logging, sensitive data redaction,
tool execution latency measurement, and recent log retrieval.
"""

import os
import re
import time
import json
import inspect
import logging
import functools
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

from config import LOG_FILE, LOGS_DIR


# =====================================================================
# Sensitive Data Redaction
# =====================================================================

PHONE_PATTERN = re.compile(r'\+?\d{10,14}')
PHONE_PARAM_KEYS = {"phone", "phone_number", "number", "tel", "mobile", "recipient"}
MSG_PARAM_KEYS = {"message", "msg", "body", "note", "prompt", "query_text"}


def redact_value(key: str, val: Any) -> Any:
    """Recursively redacts phone numbers, private message bodies, and tokens."""
    if isinstance(val, dict):
        return {k: redact_value(k, v) for k, v in val.items()}
    if isinstance(val, list):
        return [redact_value(key, item) for item in val]
    if not isinstance(val, str):
        return val

    lower_k = key.lower()

    if any(pk in lower_k for pk in PHONE_PARAM_KEYS):
        return "[REDACTED_PHONE]"

    if any(mk in lower_k for mk in MSG_PARAM_KEYS):
        return "[REDACTED_MESSAGE]"

    # If the value itself looks like a phone number
    if PHONE_PATTERN.search(val):
        return PHONE_PATTERN.sub("[REDACTED_PHONE]", val)

    return val


def redact_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Redacts sensitive parameters from a dictionary."""
    if not params:
        return {}
    return {k: redact_value(k, v) for k, v in params.items()}


# =====================================================================
# Structured JSON Formatter
# =====================================================================

class JsonFormatter(logging.Formatter):
    """
    Emits log records as single-line JSON objects (JSONL).
    Standardizes timestamp, level, logger name, message, and structured attributes.
    """
    STRUCTURED_FIELDS = [
        "tool_name", "params", "success", "latency_ms",
        "session_id", "path", "method", "status_code", "error"
    ]

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage()
        }

        for field in self.STRUCTURED_FIELDS:
            if hasattr(record, field):
                log_entry[field] = getattr(record, field)

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)


# =====================================================================
# Logger Setup & Handlers
# =====================================================================

_INITIALIZED_LOGGERS = set()
_SHARED_FILE_HANDLER: Optional[TimedRotatingFileHandler] = None
_SHARED_CONSOLE_HANDLER: Optional[logging.StreamHandler] = None


class SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """
    Windows-resilient TimedRotatingFileHandler that prevents PermissionError [WinError 32]
    during file rollover when other processes hold handles to the log file.
    """
    def rotate(self, source, dest):
        try:
            super().rotate(source, dest)
        except Exception:
            pass

    def doRollover(self):
        try:
            super().doRollover()
        except Exception:
            pass


def setup_logger(name: str = "astra") -> logging.Logger:
    """
    Configures and returns a logger instance writing JSON to logs/astra.log
    with daily rotation (midnight) and 30-day retention.
    Uses a shared file handler to avoid Windows file-locking conflicts during rollover.
    """
    global _SHARED_FILE_HANDLER, _SHARED_CONSOLE_HANDLER
    logger = logging.getLogger(name)

    if name in _INITIALIZED_LOGGERS:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Daily Timed Rotating File Handler (Shared singleton across all astra loggers)
    if _SHARED_FILE_HANDLER is None:
        _SHARED_FILE_HANDLER = SafeTimedRotatingFileHandler(
            filename=str(LOG_FILE),
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8"
        )
        _SHARED_FILE_HANDLER.setLevel(logging.INFO)
        _SHARED_FILE_HANDLER.setFormatter(JsonFormatter())

    if _SHARED_FILE_HANDLER not in logger.handlers:
        logger.addHandler(_SHARED_FILE_HANDLER)

    # 2. Console Stream Handler (readable format)
    if _SHARED_CONSOLE_HANDLER is None:
        _SHARED_CONSOLE_HANDLER = logging.StreamHandler()
        _SHARED_CONSOLE_HANDLER.setLevel(logging.WARNING)
        console_formatter = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
        _SHARED_CONSOLE_HANDLER.setFormatter(console_formatter)

    if _SHARED_CONSOLE_HANDLER not in logger.handlers:
        logger.addHandler(_SHARED_CONSOLE_HANDLER)

    _INITIALIZED_LOGGERS.add(name)
    return logger


def get_logger(name: str = "astra") -> logging.Logger:
    """Convenience getter for configured loggers."""
    return setup_logger(name)


# =====================================================================
# Tool Call Observability Decorator
# =====================================================================

def log_tool_call(tool_name: Optional[str] = None):
    """
    Decorator that measures latency in ms, redacts sensitive input parameters,
    and logs every tool call to logs/astra.log as structured JSON.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            name = tool_name or func.__name__

            # Bind arguments to parameter names
            bound_params = {}
            try:
                sig = inspect.signature(func)
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                bound_params = dict(bound.arguments)
            except Exception:
                bound_params = {f"arg_{i}": a for i, a in enumerate(args)}
                bound_params.update(kwargs)

            # Strip private / injected arguments (e.g. _service, _page)
            cleaned_params = {k: v for k, v in bound_params.items() if not k.startswith("_")}
            safe_params = redact_params(cleaned_params)

            t0 = time.perf_counter()
            success = True
            error_msg = None
            try:
                result = func(*args, **kwargs)
                if isinstance(result, dict) and "success" in result:
                    success = bool(result["success"])
                return result
            except Exception as e:
                success = False
                error_msg = str(e)
                raise
            finally:
                latency_ms = round((time.perf_counter() - t0) * 1000, 2)
                tool_logger = get_logger("astra.actions")
                tool_logger.info(
                    f"Executed tool: {name}",
                    extra={
                        "tool_name": name,
                        "params": safe_params,
                        "success": success,
                        "latency_ms": latency_ms,
                        "error": error_msg
                    }
                )
        return wrapper
    return decorator


# =====================================================================
# Recent Logs Retrieval (for UI / API Endpoint)
# =====================================================================

def get_recent_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Reads the last `limit` log entries from logs/astra.log and parses them as JSON.
    Returns newest-first or chronological list.
    """
    if not LOG_FILE.exists():
        return []

    try:
        lines = LOG_FILE.read_text(encoding="utf-8").strip().splitlines()
        recent_lines = lines[-limit:] if len(lines) > limit else lines

        parsed_entries = []
        for line in reversed(recent_lines):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                parsed_entries.append(json.loads(line_str))
            except Exception:
                parsed_entries.append({"raw": line_str})

        return parsed_entries
    except Exception as e:
        print(f"[Logging Error] Failed to read recent logs: {e}")
        return []

