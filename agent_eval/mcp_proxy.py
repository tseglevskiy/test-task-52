"""
agent_eval/mcp_proxy.py - stdio ↔ Unix-socket relay for MCP isolation.

This script is the CLIENT side of the relay.  It is written to Claude's
clean working directory so Claude has no idea where the real MCP server
lives.  It simply bridges:

    Claude (stdio) ←→ this proxy ←→ Unix socket ←→ real mcp_server.py

Usage (written into .mcp.json in the clean temp dir):
    python /path/to/mcp_proxy.py /path/to/mcp.sock

The socket path is the only argument.  The proxy connects, then pumps
bytes in both directions until either side closes.
"""

import os
import socket
import sys
import threading


def _stdin_to_socket(sock: socket.socket, done: threading.Event) -> None:
    """Pump stdin → socket until EOF or error.

    Uses os.read() on the raw stdin fd so we get data as soon as it
    arrives — unlike sys.stdin.buffer.read(n) which blocks until n bytes.
    """
    stdin_fd = sys.stdin.fileno()
    try:
        while not done.is_set():
            data = os.read(stdin_fd, 65536)
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


def _socket_to_stdout(sock: socket.socket, done: threading.Event) -> None:
    """Pump socket → stdout until EOF or error."""
    stdout = sys.stdout.buffer
    try:
        while not done.is_set():
            data = sock.recv(65536)
            if not data:
                break
            stdout.write(data)
            stdout.flush()
    except Exception:
        pass
    finally:
        done.set()


def main() -> None:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: mcp_proxy.py <socket_path>\n")
        sys.exit(1)

    sock_path = sys.argv[1]

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sock_path)

    done = threading.Event()

    t1 = threading.Thread(target=_stdin_to_socket, args=(sock, done), daemon=True)
    t2 = threading.Thread(target=_socket_to_stdout, args=(sock, done), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    try:
        sock.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
