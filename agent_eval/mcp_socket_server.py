"""
agent_eval/mcp_socket_server.py - Unix-socket listener that wraps mcp_server.py.

This is the SERVER side of the stdio-isolation relay.  task_runner.py
starts this process instead of mcp_server.py directly.  It:

  1. Starts mcp_server.py as a subprocess with stdio pipes.
  2. Creates a Unix socket at SOCK_PATH (passed as argv[1]) and listens.
  3. Signals readiness by printing "READY <sock_path>" to stdout.
  4. Accepts exactly one connection on the socket.
  5. Relays bytes bidirectionally:
       socket-client → mcp_server stdin
       mcp_server stdout → socket-client

Claude's proxy (mcp_proxy.py) is the socket client.  Claude never sees
this file or mcp_server.py — it only sees the opaque proxy in its cwd.

Usage:
    python mcp_socket_server.py <sock_path> <python_exe> <mcp_server_py>

All environment variables needed by mcp_server.py are passed via the
current process environment (task_runner sets them before exec).
"""

import os
import socket
import subprocess
import sys
import threading


def _pump_sock_to_pipe(sock: socket.socket, pipe, done: threading.Event) -> None:
    """Relay socket → subprocess stdin pipe until EOF or error."""
    try:
        while not done.is_set():
            data = sock.recv(65536)
            if not data:
                break
            pipe.write(data)
            pipe.flush()  # critical: BufferedWriter won't send until flushed
    except Exception:
        pass
    finally:
        done.set()
        try:
            pipe.close()
        except Exception:
            pass


def _pump_pipe_to_sock(pipe_fd: int, sock: socket.socket, done: threading.Event) -> None:
    """Relay subprocess stdout pipe → socket until EOF or error.

    Uses os.read() on the raw file descriptor so we get data as soon as
    it arrives — unlike file.read(n) which blocks until n bytes are available.
    """
    try:
        while not done.is_set():
            data = os.read(pipe_fd, 65536)
            if not data:
                break
            sock.sendall(data)
    except Exception:
        pass
    finally:
        done.set()
        try:
            sock.shutdown(socket.SHUT_WR)
        except Exception:
            pass


def main() -> None:
    if len(sys.argv) < 4:
        sys.stderr.write(
            "usage: mcp_socket_server.py <sock_path> <python_exe> <mcp_server_py>\n"
        )
        sys.exit(1)

    sock_path = sys.argv[1]
    python_exe = sys.argv[2]
    mcp_server_py = sys.argv[3]

    # Start the real MCP server subprocess before binding the socket.
    # This ensures the subprocess is already running when the proxy connects.
    mcp_proc = subprocess.Popen(
        [python_exe, mcp_server_py],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,  # pass MCP server logs through to our stderr
        env=os.environ.copy(),
    )

    # Remove stale socket file if it exists
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass

    # Create Unix socket and listen (backlog=1 — only one client expected)
    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(sock_path)
    server_sock.listen(1)

    # Signal readiness — task_runner waits for this line before launching Claude.
    sys.stdout.write(f"READY {sock_path}\n")
    sys.stdout.flush()

    # Accept the single proxy connection
    client_sock, _ = server_sock.accept()
    server_sock.close()  # no more connections needed

    done = threading.Event()

    t1 = threading.Thread(
        target=_pump_sock_to_pipe,
        args=(client_sock, mcp_proc.stdin, done),
        daemon=True,
    )
    t2 = threading.Thread(
        target=_pump_pipe_to_sock,
        args=(mcp_proc.stdout.fileno(), client_sock, done),
        daemon=True,
    )
    t1.start()
    t2.start()

    # Wait for the MCP server to exit naturally
    mcp_proc.wait()
    done.set()
    t1.join(timeout=2)
    t2.join(timeout=2)

    try:
        client_sock.close()
    except Exception:
        pass
    try:
        os.unlink(sock_path)
    except Exception:
        pass


if __name__ == "__main__":
    main()
