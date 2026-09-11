"""
start_tunnel.py - Cloudflare Tunnel Manager for Astra
Launches a quick Cloudflare Tunnel subprocess to provide secure public HTTPS
access to the local Astra assistant (enabling mobile mic & push notifications),
parses the generated trycloudflare.com URL, and registers it with CORS.
"""

import os
import re
import sys
import time
import shutil
import atexit
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional, Tuple

# Regex to capture the dynamically generated TryCloudflare public HTTPS URL
TRYCLOUDFLARE_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", re.IGNORECASE)

_active_process: Optional[subprocess.Popen] = None
_active_tunnel_url: Optional[str] = None


def find_cloudflared_executable() -> Optional[str]:
    """
    Locates the cloudflared executable in system PATH or standard installation locations.
    Returns the absolute path to cloudflared executable if found, else None.
    """
    # 1. Standard PATH lookup
    path = shutil.which("cloudflared")
    if path:
        return path

    # 2. Check common Windows installation paths
    candidates = []
    local_app_data = os.getenv("LOCALAPPDATA", "")
    if local_app_data:
        candidates.extend([
            Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe",
            Path(local_app_data) / "Programs" / "cloudflared" / "cloudflared.exe",
        ])
        # WinGet package directory search
        winget_pkgs = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.exists():
            for cf_dir in winget_pkgs.glob("*Cloudflare.cloudflared*"):
                exe = cf_dir / "cloudflared.exe"
                if exe.exists():
                    candidates.append(exe)

    # 3. Check Program Files
    prog_files = os.getenv("ProgramFiles", "C:\\Program Files")
    prog_files_x86 = os.getenv("ProgramFiles(x86)", "C:\\Program Files (x86)")
    candidates.extend([
        Path(prog_files) / "cloudflared" / "cloudflared.exe",
        Path(prog_files_x86) / "cloudflared" / "cloudflared.exe",
    ])

    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def extract_tunnel_url_from_line(line: str) -> Optional[str]:
    """Extracts https://*.trycloudflare.com URL from a log line if present."""
    match = TRYCLOUDFLARE_REGEX.search(line)
    if match:
        return match.group(0).strip()
    return None


def _drain_output(proc: subprocess.Popen, captured_url_holder: list, url_event: threading.Event):
    """
    Background worker that drains process stdout/stderr so buffers do not block,
    and captures the trycloudflare URL.
    """
    try:
        for raw_line in iter(proc.stdout.readline, ""):
            line = raw_line.strip()
            if not line:
                continue

            # Look for the public tunnel URL
            if not captured_url_holder[0]:
                url = extract_tunnel_url_from_line(line)
                if url:
                    captured_url_holder[0] = url
                    url_event.set()
    except Exception:
        pass
    finally:
        url_event.set()


def start_cloudflared_tunnel(
    port: int = 8000,
    timeout: float = 25.0
) -> Tuple[Optional[subprocess.Popen], Optional[str]]:
    """
    Starts cloudflared tunnel pointing to http://127.0.0.1:{port}.
    Captures the public HTTPS URL, registers it in config.CORS_ORIGINS, and returns (proc, url).
    If cloudflared is not installed or fails, returns (None, None).
    """
    global _active_process, _active_tunnel_url

    cloudflared_bin = find_cloudflared_executable()
    if not cloudflared_bin:
        print("\n[!] Cloudflare Tunnel Notice: 'cloudflared' is not installed or not in PATH.")
        print("[!] To enable HTTPS mobile access, install Cloudflare Tunnel with:")
        print("    winget install --id Cloudflare.cloudflared")
        print("[!] Or download from: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n")
        return None, None

    cmd = [
        cloudflared_bin,
        "tunnel",
        "--url",
        f"http://127.0.0.1:{port}"
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace"
        )
    except Exception as e:
        print(f"[!] Failed to spawn cloudflared subprocess: {e}")
        return None, None

    _active_process = proc
    atexit.register(stop_cloudflared_tunnel)

    captured_url_holder = [None]
    url_event = threading.Event()

    drain_thread = threading.Thread(
        target=_drain_output,
        args=(proc, captured_url_holder, url_event),
        daemon=True
    )
    drain_thread.start()

    # Wait for URL extraction
    found = url_event.wait(timeout=timeout)

    if found and captured_url_holder[0]:
        tunnel_url = captured_url_holder[0]
        _active_tunnel_url = tunnel_url

        # Dynamically register with config.CORS_ORIGINS
        try:
            from config import add_cors_origin
            add_cors_origin(tunnel_url)
        except Exception as e:
            print(f"[!] Warning: Could not register tunnel URL with CORS: {e}")

        return proc, tunnel_url

    # Check if process terminated prematurely
    if proc.poll() is not None:
        print(f"[!] cloudflared process terminated prematurely with return code {proc.returncode}")
        return proc, None

    print(f"[!] Cloudflare Tunnel timed out after {timeout}s waiting for public URL.")
    return proc, None


def stop_cloudflared_tunnel(proc: Optional[subprocess.Popen] = None):
    """Gracefully terminates the cloudflared tunnel subprocess."""
    global _active_process, _active_tunnel_url
    target = proc or _active_process
    if target and target.poll() is None:
        try:
            target.terminate()
            target.wait(timeout=3.0)
        except Exception:
            try:
                target.kill()
            except Exception:
                pass
    if target == _active_process:
        _active_process = None
        _active_tunnel_url = None


def main():
    """CLI runner for testing or standalone tunnel launch."""
    port = 8000
    if len(sys.argv) > 1:
        if sys.argv[1] in ("--check", "-c"):
            cf = find_cloudflared_executable()
            if cf:
                print(f"[+] cloudflared found at: {cf}")
                sys.exit(0)
            else:
                print("[-] cloudflared not found. Run: winget install --id Cloudflare.cloudflared")
                sys.exit(1)
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    print(f"[*] Starting Cloudflare Tunnel for port {port}...")
    proc, url = start_cloudflared_tunnel(port=port)
    if url:
        print(f"\n========================================================")
        print(f"📱 Open on mobile: {url}")
        print(f"========================================================\n")
        print("[*] Tunnel active. Press Ctrl+C to stop.")
        try:
            while proc and proc.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[*] Stopping tunnel...")
        finally:
            stop_cloudflared_tunnel(proc)
            print("[*] Tunnel stopped.")
    else:
        print("[-] Could not start tunnel.")
        sys.exit(1)


if __name__ == "__main__":
    main()

