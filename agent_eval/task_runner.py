"""
agent_eval/task_runner.py — Session orchestrator for TASK2 agent evaluation.

Starts the shop, resets the DB, launches Claude Code with an MCP browser
server, waits for completion, then validates the outcome.

Usage:
    agent_eval/.venv/bin/python agent_eval/task_runner.py \\
        --task cancel_order \\
        --seed 0 \\
        --claude /path/to/claude \\
        [--headed] [--docker]

    task:    cancel_order | apply_coupon | buy_cheapest  (default: cancel_order)
    seed:    integer seed for deterministic DB reset      (default: 0)
    claude:  path to the claude CLI binary               (default: claude)
    headed:  run browser headed/visible (default: headless)
    docker:  start shop in Docker instead of subprocess  (default: subprocess)

Session artifacts are written to _tmp/runs/{session_id}/:
    .mcp.json           MCP server config (written before launch)
    hooks/
      pre_tool_use.py   PreToolUse hook (blocks non-browser tools)
    flask.log           Flask web server stdout+stderr
    shop_seed.db        SQLite DB snapshot immediately after seeding (pre-agent)
    shop.db             SQLite DB at end of session (post-agent)
    trajectory.jsonl    One line per MCP tool call
    trajectory.txt      Human-readable trajectory summary
    screenshots/        PNG per screenshot tool call
    trace.zip           Playwright trace (open with: playwright show-trace)
    video.webm          Full session video
    result.json         Final validation result

Process tree (with isolation):
    task_runner.py
      ├── mcp_socket_server.py  (Unix-socket listener, hidden from Claude)
      │     └── mcp_server.py  (FastMCP, stdio pipes to socket server)
      │           └── chromium (Playwright)
      └── claude (Claude Code CLI, cwd = clean /tmp/shopgym_{id}/)
            └── mcp_proxy.py   (opaque stdio↔socket bridge in Claude's cwd)

Claude's working directory is a clean temp folder with no project files.
Its .mcp.json references only the opaque proxy — no project paths visible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
FLASK_PORT = 5299  # dedicated port for agent_eval, avoids conflicts with demo

# Python interpreter used for the MCP server and socket-server wrapper
_PYTHON_EXE = sys.executable

# ---------------------------------------------------------------------------
# Global child-process registry — populated during run_session so the
# SIGINT handler can kill everything on Ctrl+C.
# ---------------------------------------------------------------------------

_child_pgids: list[int] = []   # process-group IDs to kill on interrupt
_flask_proc: subprocess.Popen | None = None  # Flask subprocess (not in a new pgid)


def _kill_all_children(sig: int = signal.SIGTERM) -> None:
    """Send sig to every registered child process group, then SIGKILL after 2s."""
    for pgid in _child_pgids:
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass
    if _flask_proc is not None:
        try:
            _flask_proc.kill()
        except Exception:
            pass


def _sigint_handler(signum, frame) -> None:
    """Ctrl+C: kill all children then re-raise so Python exits normally."""
    print("\n[task_runner] Interrupted — killing child processes ...", flush=True)
    _kill_all_children(signal.SIGTERM)
    # Brief pause so processes can clean up, then force-kill
    time.sleep(2)
    _kill_all_children(signal.SIGKILL)
    sys.exit(1)


signal.signal(signal.SIGINT, _sigint_handler)

_MCP_SERVER_PY = str(ROOT / "agent_eval" / "mcp_server.py")
_MCP_SOCKET_SERVER_PY = str(ROOT / "agent_eval" / "mcp_socket_server.py")
_MCP_PROXY_PY = str(ROOT / "agent_eval" / "mcp_proxy.py")

# ---------------------------------------------------------------------------
# Task registry
# ---------------------------------------------------------------------------

def _load_task(task_name: str):
    """Import and return the task class for the given task name."""
    sys.path.insert(0, str(ROOT))
    if task_name == "cancel_order":
        from tasks.cancel_order import CancelRecentOrderTask
        return CancelRecentOrderTask
    elif task_name == "apply_coupon":
        from tasks.apply_coupon import ApplyCouponWithQuantityTask
        return ApplyCouponWithQuantityTask
    elif task_name == "buy_cheapest":
        from tasks.buy_cheapest import BuyCheapestInCategoryTask
        return BuyCheapestInCategoryTask
    else:
        raise ValueError(f"Unknown task: {task_name!r}. Choose: cancel_order, apply_coupon, buy_cheapest")


# ---------------------------------------------------------------------------
# Shop lifecycle — subprocess (default) or Docker
# ---------------------------------------------------------------------------

def _start_flask_subprocess(db_path: Path, port: int, log_path: Path) -> subprocess.Popen:
    """Start the shop Flask app as a lightweight subprocess (default).

    Uses the shop's own venv Python (shop/.venv/bin/python) so that Flask
    and its dependencies are available without installing them in agent_eval/.venv.
    Falls back to sys.executable if the shop venv doesn't exist.

    Flask stdout+stderr are written to log_path (flask.log in the session dir).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch(exist_ok=True)

    # Prefer the shop's own venv Python — it has Flask installed.
    shop_python = ROOT / "shop" / ".venv" / "bin" / "python"
    python_exe = str(shop_python) if shop_python.exists() else sys.executable

    flask_cmd = (
        "import os, sys; sys.path.insert(0, os.getcwd()); "
        "from app import create_app; "
        f"create_app(os.environ['DATABASE_PATH'], os.environ.get('LOG_PATH')).run("
        f"host='0.0.0.0', port={port}, debug=False, use_reloader=False)"
    )
    log_fh = open(log_path, "wb")
    proc = subprocess.Popen(
        [python_exe, "-c", flask_cmd],
        cwd=ROOT / "shop",
        env={
            **os.environ,
            "DATABASE_PATH": str(db_path),
            "LOG_PATH": str(db_path.with_suffix(".jsonl")),
        },
        stdout=log_fh,
        stderr=log_fh,
    )
    print(f"[task_runner] Flask subprocess started (PID {proc.pid}) on port {port} (python: {python_exe})", flush=True)
    print(f"[task_runner] Flask log: {log_path}", flush=True)
    return proc


def _start_flask_docker(db_path: Path, port: int, container_name: str) -> subprocess.Popen:
    """
    Start the shop in a Docker container with the DB file bind-mounted from the host.

    The container is named so it can be reliably stopped and removed.
    Pre-creates the DB file before mounting (required for file bind-mounts).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch(exist_ok=True)

    cmd = [
        "docker", "run", "--rm",
        "--name", container_name,
        "-p", f"{port}:5000",
        "-v", f"{db_path}:/app/shop.db",
        "-e", "DATABASE_PATH=/app/shop.db",
        "shopgym-shop:latest",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(f"[task_runner] Docker container '{container_name}' started on port {port}", flush=True)
    return proc


def _stop_flask_docker(container_name: str) -> None:
    """Stop and remove a named Docker container."""
    subprocess.run(["docker", "stop", container_name], capture_output=True)
    subprocess.run(["docker", "rm", container_name], capture_output=True)
    print(f"[task_runner] Docker container '{container_name}' stopped.", flush=True)


def _wait_for_flask(base_url: str, retries: int = 40) -> bool:
    """Poll /api/health until Flask is ready or retries are exhausted."""
    print(f"[task_runner] Waiting for Flask at {base_url}/api/health ...", flush=True)
    for i in range(retries):
        try:
            r = requests.get(f"{base_url}/api/health", timeout=1)
            if r.json().get("status") == "ok":
                print(f"[task_runner] Flask is up (attempt {i + 1})", flush=True)
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# MCP socket server — starts the real MCP server behind a Unix socket
# ---------------------------------------------------------------------------

async def _start_mcp_socket_server(
    sock_path: str,
    session_id: str,
    site_url: str,
    runs_dir: str,
    headed: bool,
    task_name: str,
    goal: str,
) -> asyncio.subprocess.Process:
    """
    Start mcp_socket_server.py, which:
      - Creates a Unix socket at sock_path
      - Starts mcp_server.py as a subprocess
      - Accepts one connection and relays stdio ↔ socket

    Waits until the socket server prints "READY <path>" before returning.
    All MCP server env vars are passed through the environment.

    Returns the socket-server subprocess (so task_runner can kill it later).
    """
    env = {
        **os.environ,
        "SESSION_ID": session_id,
        "SITE_URL": site_url,
        "RUNS_DIR": runs_dir,
        "HEADED": "1" if headed else "0",
        "TASK_NAME": task_name,
        "TASK_GOAL": goal,
        "PYTHONPATH": str(ROOT),
    }

    proc = await asyncio.create_subprocess_exec(
        _PYTHON_EXE,
        _MCP_SOCKET_SERVER_PY,
        sock_path,
        _PYTHON_EXE,
        _MCP_SERVER_PY,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        preexec_fn=os.setsid,
    )
    # Register the socket server's process group for SIGINT cleanup
    try:
        _child_pgids.append(os.getpgid(proc.pid))
    except ProcessLookupError:
        pass

    # Wait for "READY <sock_path>" line (socket server signals it's listening)
    ready = False
    try:
        line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
        line = line_bytes.decode().strip()
        if line.startswith("READY "):
            ready = True
            print(f"[task_runner] MCP socket server ready: {line}", flush=True)
        else:
            print(f"[task_runner] WARNING: unexpected socket server output: {line!r}", flush=True)
    except asyncio.TimeoutError:
        print("[task_runner] ERROR: MCP socket server did not signal READY in 30s", flush=True)

    if not ready:
        raise RuntimeError("MCP socket server failed to start")

    # Drain stderr from socket server in background (it passes through mcp_server logs)
    async def _drain_socket_server_stderr():
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            print(f"[mcp]    {text}", flush=True)

    asyncio.ensure_future(_drain_socket_server_stderr())

    return proc


# ---------------------------------------------------------------------------
# Claude's clean working directory — no project files, opaque .mcp.json
# ---------------------------------------------------------------------------

def _setup_claude_workdir(
    session_id: str,
    sock_path: str,
    hook_path: str,
) -> Path:
    """
    Create a clean temp directory for Claude's working directory.

    Contains only:
      .mcp.json    — references the opaque proxy with no project paths
      server.py    — copy of mcp_proxy.py with a generic name
      hooks/       — PreToolUse hook (blocks non-browser tools)

    Claude has no filesystem context: no README, no source files, nothing
    it can read to discover how the system works.  The .mcp.json references
    only local files (server.py in the same dir) and the socket path.

    Returns the path to the clean workdir.
    """
    import ast

    workdir = Path(f"/tmp/shopgym_{session_id}")
    workdir.mkdir(parents=True, exist_ok=True)

    # Copy the proxy script into the workdir under a generic name, stripping
    # the module-level docstring so Claude can't read the project name from it.
    proxy_src = Path(_MCP_PROXY_PY).read_text()
    try:
        tree = ast.parse(proxy_src)
        # If the first statement is a docstring expression, remove it
        if (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)
        ):
            # Find the end of the docstring in the source and strip it
            lines = proxy_src.splitlines(keepends=True)
            # Re-parse to get line numbers
            docstring_end = tree.body[0].end_lineno
            proxy_src = "".join(lines[docstring_end:]).lstrip("\n")
    except Exception:
        pass  # if stripping fails, use the original source

    proxy_copy = workdir / "server.py"
    proxy_copy.write_text(proxy_src)

    # Write the opaque .mcp.json — references only local files, no project paths.
    config = {
        "mcpServers": {
            "browser": {
                "command": _PYTHON_EXE,
                "args": [str(proxy_copy), sock_path],
            }
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{_PYTHON_EXE} {hook_path}",
                        }
                    ],
                }
            ]
        },
    }
    mcp_config_path = workdir / ".mcp.json"
    with open(mcp_config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"[task_runner] Claude workdir: {workdir}", flush=True)
    print(f"[task_runner] Claude .mcp.json: {mcp_config_path}", flush=True)
    return workdir


# ---------------------------------------------------------------------------
# Session directory setup
# ---------------------------------------------------------------------------

def _write_hook(session_dir: Path) -> Path:
    """Write the PreToolUse hook that blocks non-browser tools. Returns hook path."""
    hooks_dir = session_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre_tool_use.py"
    hook_code = '''\
import json
import sys

payload = json.load(sys.stdin)
tool_name = payload.get("tool_name", "")

if tool_name.startswith("mcp__browser__"):
    print(json.dumps({"decision": "approve"}))
else:
    print(json.dumps({
        "decision": "deny",
        "reason": (
            f"Tool \'{tool_name}\' is not allowed in this evaluation. "
            "Use mcp__browser__* tools only."
        ),
    }))
'''
    with open(hook_path, "w") as f:
        f.write(hook_code)
    hook_path.chmod(0o755)
    print(f"[task_runner] PreToolUse hook: {hook_path}", flush=True)
    return hook_path




# ---------------------------------------------------------------------------
# Stream-json event parser
# ---------------------------------------------------------------------------

def _process_stdout_line(text: str, stdout_lines: list) -> None:
    """Parse one stream-json line from Claude and print a human-readable summary."""
    if not text.strip():
        return
    stdout_lines.append(text + "\n")
    try:
        event = json.loads(text)
        etype = event.get("type", "")
        if etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    msg = block["text"].strip()
                    if msg:
                        print(f"[claude] {msg}", flush=True)
                elif block.get("type") == "tool_use":
                    tool = block.get("name", "?")
                    inp = block.get("input", {})
                    inp_str = str(inp)
                    if len(inp_str) > 200:
                        inp_str = inp_str[:200] + "..."
                    print(f"[claude] tool_use: {tool}  {inp_str}", flush=True)
        elif etype == "tool_result":
            content = event.get("content", "")
            if isinstance(content, list):
                # content is a list of blocks
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        txt = block.get("text", "")
                        if len(txt) > 200:
                            txt = txt[:200] + "..."
                        print(f"[tool]   {txt}", flush=True)
            elif isinstance(content, str):
                if len(content) > 200:
                    content = content[:200] + "..."
                print(f"[tool]   {content}", flush=True)
        elif etype == "result":
            print(f"[claude] === DONE: {event.get('result', '')} ===", flush=True)
        # Skip system, user, and other event types
    except (json.JSONDecodeError, KeyError):
        # Not JSON — print raw (e.g. error messages)
        print(f"[claude] {text}", flush=True)


# ---------------------------------------------------------------------------
# Claude Code launch
# ---------------------------------------------------------------------------

async def _launch_claude(
    goal: str,
    claude_workdir: Path,
    claude_path: str,
    timeout: int = 300,
) -> tuple[int, str]:
    """
    Launch Claude Code with the task goal and MCP config.

    Claude runs in a clean temp directory (claude_workdir) with no project
    files — it cannot read source code or discover how the system works.
    Its .mcp.json references only an opaque proxy script.

    Streams Claude's stdout to our stdout live so you can watch it work.

    Args:
        goal:          Natural-language task goal shown to Claude.
        claude_workdir: Clean temp dir containing .mcp.json (no project files).
        claude_path:   Path to the claude CLI binary.
        timeout:       Max seconds to wait before killing Claude.

    Returns:
        (exit_code, stdout_text)
    """
    allowed_tools = ",".join([
        "mcp__browser__navigate",
        "mcp__browser__click",
        "mcp__browser__type_text",
        "mcp__browser__select_option",
        "mcp__browser__scroll",
        "mcp__browser__screenshot",
        "mcp__browser__get_dom",
        "mcp__browser__get_url",
    ])

    cmd = [
        claude_path,
        "-p", goal,
        "--allowedTools", allowed_tools,
        "--permission-mode", "dontAsk",
        "--mcp-config", str(claude_workdir / ".mcp.json"),
        "--max-turns", "50",
        "--output-format", "stream-json",  # stream JSON events as they happen
        "--verbose",                        # include tool calls and results
    ]

    print(f"\n[task_runner] Launching Claude Code ...", flush=True)
    print(f"[task_runner] cmd: {' '.join(cmd)}", flush=True)
    print(f"[task_runner] Claude cwd (isolated): {claude_workdir}", flush=True)
    print(f"[task_runner] timeout: {timeout}s", flush=True)
    print(f"\n{'='*60}", flush=True)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(claude_workdir),           # ← Claude starts in the clean temp dir
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        preexec_fn=os.setsid,  # new process group — critical for clean shutdown
    )
    # Register Claude's process group for SIGINT cleanup
    try:
        _child_pgids.append(os.getpgid(proc.pid))
    except ProcessLookupError:
        pass

    stdout_lines = []

    async def _stream_stdout():
        """Stream Claude's stdout (stream-json events) to our stdout live.

        Uses read(n) in a loop instead of readline() to avoid the 64KB
        asyncio StreamReader limit — Claude's stream-json lines can contain
        large base64 screenshot blobs that exceed the default buffer.
        """
        buf = b""
        while True:
            chunk = await proc.stdout.read(65536)  # 64KB chunks
            if not chunk:
                # EOF — process any remaining buffered data
                if buf:
                    _process_stdout_line(buf.decode("utf-8", errors="replace").rstrip(), stdout_lines)
                break
            buf += chunk
            # Split on newlines and process complete lines
            while b"\n" in buf:
                line_bytes, buf = buf.split(b"\n", 1)
                text = line_bytes.decode("utf-8", errors="replace").rstrip()
                _process_stdout_line(text, stdout_lines)

    async def _drain_stderr():
        """Print MCP server logs (stderr) — clearly labeled."""
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            print(f"[mcp]    {text}", flush=True)

    try:
        await asyncio.wait_for(
            asyncio.gather(
                proc.wait(),
                _stream_stdout(),
                _drain_stderr(),
            ),
            timeout=timeout,
        )
        exit_code = proc.returncode
    except asyncio.TimeoutError:
        print(f"\n[task_runner] TIMEOUT after {timeout}s — killing process tree", flush=True)
        await _shutdown_tree(proc.pid)
        exit_code = -1

    print(f"\n{'='*60}", flush=True)
    print(f"[task_runner] Claude exited with code {exit_code}", flush=True)
    return exit_code, "".join(stdout_lines)


async def _shutdown_tree(pid: int, grace_seconds: int = 3) -> None:
    """
    Kill the entire process group: SIGTERM first, then SIGKILL after grace period.
    The grace period gives Playwright time to write trace.zip.
    """
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return  # already dead

    print(f"[task_runner] SIGTERM → process group {pgid}", flush=True)
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return

    await asyncio.sleep(grace_seconds)

    print(f"[task_runner] SIGKILL → process group {pgid}", flush=True)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # clean exit during grace period


# ---------------------------------------------------------------------------
# Main session runner
# ---------------------------------------------------------------------------

async def run_session(
    task_name: str,
    seed: int,
    claude_path: str,
    headed: bool,
    use_docker: bool = False,
    timeout: int = 300,
) -> dict:
    """
    Run one full evaluation session.

    Args:
        task_name:   Task identifier.
        seed:        Integer seed for deterministic DB reset.
        claude_path: Path to the claude CLI binary.
        headed:      True for headed browser (requires display), False for headless.
        use_docker:  True to start shop in Docker, False for subprocess (default).
        timeout:     Max seconds to wait for Claude.

    Returns:
        SessionResult dict written to result.json.
    """
    started_at = time.time()
    session_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    base_url = f"http://localhost:{FLASK_PORT}"

    # Session directory under _tmp/runs/ (per project conventions)
    session_dir = ROOT / "_tmp" / "runs" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = str(session_dir)

    print(f"\n[task_runner] ===== SESSION START =====", flush=True)
    print(f"[task_runner] session_id: {session_id}", flush=True)
    print(f"[task_runner] task:       {task_name}", flush=True)
    print(f"[task_runner] seed:       {seed}", flush=True)
    print(f"[task_runner] headed:     {headed}", flush=True)
    print(f"[task_runner] shop:       {'docker' if use_docker else 'subprocess'}", flush=True)
    print(f"[task_runner] artifacts:  {session_dir}", flush=True)

    # --- 1. Start shop ---
    db_path = ROOT / "_tmp" / "runs" / session_id / "shop.db"
    container_name = f"shopgym_{session_id}"

    global _flask_proc
    flask_log_path = session_dir / "flask.log"
    if use_docker:
        flask_proc = _start_flask_docker(db_path, FLASK_PORT, container_name)
    else:
        flask_proc = _start_flask_subprocess(db_path, FLASK_PORT, flask_log_path)
    _flask_proc = flask_proc  # register for SIGINT cleanup

    try:
        if not _wait_for_flask(base_url):
            raise RuntimeError("Flask did not start in time")

        # --- 2. Load task, reset DB, setup ---
        task_class = _load_task(task_name)
        task = task_class()

        body = {"seed": seed}
        body.update(task.seed_requirements())
        resp = requests.post(f"{base_url}/api/reset", json=body, timeout=10)
        resp.raise_for_status()
        print(f"[task_runner] DB reset OK (seed={seed})", flush=True)

        # Save a copy of the freshly-seeded DB before the agent touches anything.
        # Useful for post-hoc inspection or replaying the episode from scratch.
        seed_snapshot = session_dir / "shop_seed.db"
        shutil.copy2(str(db_path), str(seed_snapshot))
        print(f"[task_runner] Seed snapshot: {seed_snapshot}", flush=True)

        # Save the full DB state as JSON (same data the task verifiers use).
        # This snapshot is taken after seeding but before the agent runs.
        db_state_seed = requests.get(f"{base_url}/api/db-state", timeout=10).json()
        with open(session_dir / "db_state_seed.json", "w") as f:
            json.dump(db_state_seed, f, indent=2)
        print(f"[task_runner] DB state seed snapshot: {session_dir / 'db_state_seed.json'}", flush=True)

        goal = task.setup(base_url)
        print(f"[task_runner] goal: {goal}", flush=True)

        # Wrap the task goal with minimal context:
        # - site URL (so Claude doesn't guess the wrong port)
        # - confirmation that the browser is already open
        # - the 8 available tools (hard list — Claude sometimes tries ToolSearch
        #   or invents tools like triple_click on the first turn)
        # Tool usage details (selectors, select_option, etc.) live in the tool
        # docstrings in mcp_server.py — that is where Claude reads them.
        goal = (
            f"{goal}\n\n"
            f"The shop is running at {base_url}. "
            f"The browser is already open at {base_url}.\n\n"
            "Available tools (these 8 only — any other tool call will fail immediately):\n"
            "  mcp__browser__navigate, mcp__browser__click, mcp__browser__type_text,\n"
            "  mcp__browser__select_option, mcp__browser__scroll, mcp__browser__screenshot,\n"
            "  mcp__browser__get_dom, mcp__browser__get_url\n"
            "Read each tool's description before using it."
        )

        # --- 3. Write session config files ---
        hook_path = _write_hook(session_dir)

        # --- 4. Set up Claude's isolated working directory ---
        # Do this BEFORE starting the socket server so the /tmp/shopgym_{id}/
        # directory exists when mcp_socket_server.py tries to create the socket.
        sock_path = f"/tmp/shopgym_{session_id}/mcp.sock"
        claude_workdir = _setup_claude_workdir(
            session_id=session_id,
            sock_path=sock_path,
            hook_path=str(hook_path),
        )

        # --- 5. Start MCP socket server (hidden from Claude) ---
        # Socket lives in Claude's workdir (/tmp/shopgym_{id}/mcp.sock) so
        # its path doesn't reveal the project directory.
        mcp_sock_proc = await _start_mcp_socket_server(
            sock_path=sock_path,
            session_id=session_id,
            site_url=base_url,
            runs_dir=runs_dir,
            headed=headed,
            task_name=task_name,
            goal=goal,
        )

        # --- 6. Launch Claude Code ---
        exit_code, claude_stdout = await _launch_claude(goal, claude_workdir, claude_path, timeout)  # noqa: E501

        # Clean up socket server process
        try:
            await _shutdown_tree(mcp_sock_proc.pid)
        except Exception:
            pass

        # --- 5. End-state verification ---
        print(f"\n[task_runner] Running end-state verifier ...", flush=True)
        end_state = task.verify(base_url)
        print(f"[task_runner] end_state: {end_state}", flush=True)

        # Save the full DB state as JSON after the agent ran.
        db_state_final = requests.get(f"{base_url}/api/db-state", timeout=10).json()
        with open(session_dir / "db_state_final.json", "w") as f:
            json.dump(db_state_final, f, indent=2)
        print(f"[task_runner] DB state final snapshot: {session_dir / 'db_state_final.json'}", flush=True)

        # --- 6. Trajectory validation ---
        from agent_eval.trajectory import TrajectoryWriter
        from agent_eval.validators.deterministic import DeterministicValidator
        from agent_eval.validators.llm_judge import LLMJudgeValidator

        writer = TrajectoryWriter(runs_dir)
        trajectory = writer.load()

        # Deterministic validator: instant, rule-based, no API calls
        det_validator = DeterministicValidator()
        det_result = det_validator.validate(trajectory, task_name, goal)
        print(f"[task_runner] trajectory (deterministic): {det_result}", flush=True)

        # LLM judge: slower, catches subtler behavioral issues
        llm_validator = LLMJudgeValidator()
        llm_result = llm_validator.validate(trajectory, task_name, goal)
        print(f"[task_runner] trajectory (llm_judge): {llm_result}", flush=True)

        # --- 7. Assemble and write result ---
        ended_at = time.time()
        result = {
            "session_id": session_id,
            "task": task_name,
            "seed": seed,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_s": round(ended_at - started_at, 1),
            "exit_code": exit_code,
            "end_state": end_state,
            "trajectory_deterministic": det_result,
            "trajectory_llm": llm_result,
            "passed": end_state["passed"] and det_result["passed"] and llm_result["passed"],
        }

        result_path = session_dir / "result.json"
        with open(result_path, "w") as f:
            json.dump(result, f, indent=2)

        _print_summary(result, session_dir)
        return result

    finally:
        if use_docker:
            # For Docker: stop the named container (kills the process inside it).
            # flask_proc is the `docker run` process; killing it alone doesn't stop
            # the container because Docker may detach. Use `docker stop` to be sure.
            _stop_flask_docker(container_name)
            flask_proc.kill()
            flask_proc.wait()
        else:
            print(f"\n[task_runner] Stopping Flask subprocess (PID {flask_proc.pid}) ...", flush=True)
            flask_proc.kill()
            flask_proc.wait()
            print("[task_runner] Flask stopped.", flush=True)


def _print_summary(result: dict, session_dir: Path) -> None:
    """Print a human-readable summary of the session result."""
    passed = result["passed"]
    status = "✓ PASSED" if passed else "✗ FAILED"

    print(f"\n[task_runner] ===== SESSION RESULT =====", flush=True)
    print(f"  {status}", flush=True)
    print(f"  task:       {result['task']}", flush=True)
    print(f"  seed:       {result['seed']}", flush=True)
    print(f"  duration:   {result['duration_s']}s", flush=True)
    print(f"  exit_code:  {result['exit_code']}", flush=True)
    print(f"  end_state:  {result['end_state']}", flush=True)
    print(f"  traj (det): {result['trajectory_deterministic']}", flush=True)
    print(f"  traj (llm): {result['trajectory_llm']}", flush=True)
    print(f"\n  Artifacts in:  {session_dir}", flush=True)
    print(f"    flask.log         — web server request log", flush=True)
    print(f"    shop_seed.db      — DB snapshot before agent ran", flush=True)
    print(f"    shop.db           — DB snapshot after agent ran", flush=True)
    print(f"    db_state_seed.json — full DB state as JSON before agent ran", flush=True)
    print(f"    db_state_final.json — full DB state as JSON after agent ran", flush=True)
    print(f"    shop.jsonl        — shop event log (add-to-cart, checkout, cancel, etc.)", flush=True)
    print(f"    trajectory.jsonl  — machine-readable tool call log", flush=True)
    print(f"    trajectory.txt    — human-readable summary", flush=True)
    print(f"    screenshots/      — PNG per screenshot call", flush=True)
    print(f"    trace.zip         — playwright show-trace {session_dir}/trace.zip", flush=True)
    print(f"    video.webm        — session recording", flush=True)
    print(f"    result.json       — full validation result", flush=True)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one agent evaluation session against the ShopGym.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default: headless browser, subprocess shop
  python agent_eval/task_runner.py --task cancel_order --seed 0 --claude claude

  # Headed browser (requires display, e.g. not WSL without X server)
  python agent_eval/task_runner.py --task apply_coupon --headed

  # Docker shop (requires: docker build -t shopgym-shop:latest shop/)
  python agent_eval/task_runner.py --task buy_cheapest --docker --claude /usr/local/bin/claude
        """,
    )
    parser.add_argument(
        "--task",
        default="cancel_order",
        choices=["cancel_order", "apply_coupon", "buy_cheapest"],
        help="Task to evaluate (default: cancel_order)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Integer seed for deterministic DB reset (default: 0)",
    )
    parser.add_argument(
        "--claude",
        default="claude",
        help="Path to the claude CLI binary (default: claude, assumes it is on PATH)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        default=False,
        help="Run browser headed/visible (default: headless). Requires a display.",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        default=False,
        help=(
            "Start shop in Docker instead of a subprocess (default: subprocess). "
            "Requires: docker build -t shopgym-shop:latest shop/"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Max seconds to wait for Claude to finish (default: 300)",
    )

    args = parser.parse_args()

    result = asyncio.run(
        run_session(
            task_name=args.task,
            seed=args.seed,
            claude_path=args.claude,
            headed=args.headed,
            use_docker=args.docker,
            timeout=args.timeout,
        )
    )

    # Exit with code 0 if passed, 1 if failed — useful for CI
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
