import os
import sys
import time
import socket
import warnings
import webbrowser
import threading
import uvicorn
import subprocess

# Suppress harmless Python 3.14 deprecation warnings & Google SDK advisory notices
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*automatic function calling.*")

# Suppress harmless "Invalid HTTP request received" spam from browsers probing HTTPS on HTTP port
import logging

class SuppressInvalidHTTPRequestFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "Invalid HTTP request received" not in record.getMessage()

logging.getLogger("uvicorn.error").addFilter(SuppressInvalidHTTPRequestFilter())

# Silence Windows asyncio WinError 10054 bug in Python ProactorEventLoop
if sys.platform == "win32":
    try:
        import asyncio.proactor_events
        _orig_call_connection_lost = asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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

from config import HOST, PORT, LAN_IP, USE_TUNNEL, USE_SSL, SSL_KEYFILE, SSL_CERTFILE


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Checks if a local TCP port is currently in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect((host, port))
            return True
        except (OSError, ConnectionRefusedError):
            return False


def free_port_if_occupied(port: int):
    """
    Checks if the specified port is already bound.
    If occupied, safely terminates any stale process holding the port to prevent [Errno 10048].
    """
    if not is_port_in_use(port):
        return

    print(f"[*] Port {port} is currently in use. Reclaiming port...")
    current_pid = os.getpid()

    # Strategy 1: psutil (clean and process-aware)
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['pid'] == current_pid:
                    continue
                for conn in proc.net_connections(kind='inet'):
                    if conn.laddr and conn.laddr.port == port:
                        name = proc.info.get('name', 'Process')
                        pid = proc.info.get('pid')
                        print(f"[*] Terminating stale process {name} (PID {pid}) holding port {port}...")
                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except psutil.TimeoutExpired:
                            proc.kill()
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass

    # Strategy 2: Windows netstat + taskkill fallback
    if is_port_in_use(port) and sys.platform == "win32":
        try:
            output = subprocess.check_output(f'netstat -ano | findstr :{port}', shell=True, text=True, errors="ignore")
            for line in output.strip().splitlines():
                if "LISTENING" in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) != current_pid:
                        print(f"[*] Freeing port {port} from PID {pid}...")
                        subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
        except Exception:
            pass

    # Wait up to 3 seconds for port to clear
    start = time.time()
    while time.time() - start < 3.0:
        if not is_port_in_use(port):
            print(f"[*] Port {port} successfully freed.")
            return
        time.sleep(0.3)

    if not is_port_in_use(port):
        print(f"[*] Port {port} successfully freed.")
    else:
        print(f"[!] Warning: Port {port} still appears occupied. Continuing startup attempt...")


def is_local_executor_running() -> bool:
    """Checks whether local_executor.py is already running in another process."""
    try:
        import psutil
        current_pid = os.getpid()
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['pid'] == current_pid:
                    continue
                cmdline = " ".join(proc.info.get('cmdline') or [])
                if "local_executor.py" in cmdline:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return False


def start_local_executor_process():
    """Starts local_executor.py as a background process on the Windows machine."""
    try:
        executor_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_executor.py")
        if os.path.exists(executor_path):
            proc = subprocess.Popen(
                [sys.executable, executor_path],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return proc
    except Exception as e:
        print(f"[!] Warning: Could not auto-start local executor: {e}")
    return None


def safely_terminate_process(proc):
    """Cleanly terminates a subprocess and reaps its handle to avoid WinError 6 on Python 3.14."""
    if not proc:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except Exception:
            proc.kill()
            proc.wait(timeout=1.0)
    except Exception:
        pass
    finally:
        for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None), getattr(proc, "stdin", None)):
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass


def wait_for_server(port: int, timeout: float = 12.0) -> bool:
    """Waits until the local server is listening and accepting TCP connections."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.2)
    return False


def get_local_ip():
    """Gets the local machine's IP address on the Wi-Fi/LAN network."""
    if LAN_IP:
        return LAN_IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def print_banner():
    banner = r"""
    ========================================================
       ___   _____ _____ ___    _     _____     _______ 
      / _ \ / ____|_   _|  __ \  / \   / ____|   |__   __|
     / /_\ \ (___   | | | |__) |/ _ \  \___ \      | |   
     |  _  |\___ \  | | |  _  // ___ \  ___) |     | |   
     | | | |____) |_| |_| | \ \_/   \_\|_____/      |_|   
     
         AI Voice Automated Assistant (Gemini Live)
    ========================================================
    """
    print(banner)


def main():
    print_banner()

    # Pre-flight check: auto-free port if occupied
    free_port_if_occupied(PORT)

    local_ip = LAN_IP or get_local_ip()
    protocol = "https" if USE_SSL else "http"
    local_url = f"{protocol}://127.0.0.1:{PORT}"
    mobile_url = f"{protocol}://{local_ip}:{PORT}"

    tunnel_proc = None
    tunnel_url = None
    managed_executor_proc = None

    if USE_TUNNEL:
        print("[*] Cloudflare Tunnel : Initializing HTTPS tunnel for mobile access...")
        try:
            from start_tunnel import start_cloudflared_tunnel, stop_cloudflared_tunnel
            tunnel_proc, tunnel_url = start_cloudflared_tunnel(port=PORT)
        except Exception as e:
            print(f"[!] Warning: Could not start Cloudflare Tunnel: {e}")

    if USE_SSL:
        print(f"[*] HTTPS Status   : Enabled (Native SSL cert: {SSL_CERTFILE})")
    elif USE_TUNNEL:
        print(f"[*] HTTPS Status   : Enabled via Cloudflare Tunnel")
    else:
        print(f"[*] HTTPS Status   : Disabled (Set USE_TUNNEL=true or SSL_KEYFILE/SSL_CERTFILE in .env)")

    print(f"[*] Local URL      : {local_url}")
    if tunnel_url:
        print(f"[*] 📱 Open on mobile: {tunnel_url}")
        print(f"[*] [Mobile HTTPS] : {tunnel_url}")
        print(f"[*] LAN Fallback   : {mobile_url} (LAN IP: {local_ip})")
    else:
        print(f"[*] Open on mobile : {mobile_url} (LAN IP: {local_ip})")

    if HOST == "0.0.0.0":
        print(f"[*] Network Status : Listening on all interfaces (0.0.0.0:{PORT})")
    else:
        print(f"[*] Notice         : Bound to {HOST}. Set HOST=0.0.0.0 in .env for mobile access")
    print(f"[*] Firewall Tip   : If mobile cannot connect, allow port {PORT} in Windows Firewall (Admin):")
    print(f"                     netsh advfirewall firewall add rule name=\"Astra\" dir=in action=allow protocol=TCP localport={PORT}")
    print(f"[*] Status         : Starting server...\n")

    # Background service manager: waits for server socket before opening browser and starting local_executor
    def background_service_worker():
        nonlocal managed_executor_proc
        if not wait_for_server(PORT, timeout=12.0):
            return

        # 1. Open browser
        try:
            print(f"\n[Astra] Server ready. Opening Assistant interface at {local_url} ...")
            webbrowser.open(local_url)
        except Exception as e:
            print(f"[!] Could not open browser: {e}")

        # 2. Start local executor if on Windows and not already running
        if sys.platform == "win32":
            if is_local_executor_running():
                print("[*] Local Executor : Already active on this machine.")
            else:
                print("[*] Local Executor : Connecting Windows laptop PC-control agent...")
                managed_executor_proc = start_local_executor_process()

    threading.Thread(target=background_service_worker, daemon=True).start()

    # Run FastAPI server
    ssl_kwargs = {}
    if USE_SSL:
        ssl_kwargs["ssl_keyfile"] = SSL_KEYFILE
        ssl_kwargs["ssl_certfile"] = SSL_CERTFILE

    try:
        uvicorn.run("app:app", host=HOST, port=PORT, reload=False, log_level="info", **ssl_kwargs)
    except KeyboardInterrupt:
        print("\n[Astra] Shutting down gracefully. Goodbye!")
    finally:
        if managed_executor_proc:
            safely_terminate_process(managed_executor_proc)
        if tunnel_proc:
            try:
                from start_tunnel import stop_cloudflared_tunnel
                stop_cloudflared_tunnel(tunnel_proc)
            except Exception:
                pass


if __name__ == "__main__":
    main()
