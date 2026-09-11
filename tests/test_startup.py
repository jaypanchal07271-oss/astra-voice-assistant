"""
Tests for run.py startup utilities:
- is_port_in_use detection
- free_port_if_occupied handling
- wait_for_server socket polling
- safely_terminate_process handle reaping
"""

import socket
import subprocess
import sys
import run


def test_is_port_in_use_detects_listening_socket():
    # Bind a temporary socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    try:
        assert run.is_port_in_use(port) is True
    finally:
        sock.close()

    # After closing, port should no longer be in use
    assert run.is_port_in_use(port) is False


def test_wait_for_server_succeeds_when_port_open():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    try:
        assert run.wait_for_server(port, timeout=1.0) is True
    finally:
        sock.close()


def test_wait_for_server_times_out_on_closed_port():
    # Pick a port unlikely to be open
    assert run.wait_for_server(59999, timeout=0.5) is False


def test_safely_terminate_process_handles_none_and_running_proc():
    # Should not raise for None
    run.safely_terminate_process(None)

    # Should cleanly terminate a real subprocess without WinError 6
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    assert proc.poll() is None
    run.safely_terminate_process(proc)
    assert proc.poll() is not None


def test_is_local_executor_running_does_not_crash():
    # Should return a boolean without throwing an exception
    result = run.is_local_executor_running()
    assert isinstance(result, bool)

