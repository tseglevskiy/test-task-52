# -*- coding: utf-8 -*-
"""
agent_eval/mcp_server.py - FastMCP browser server for agent evaluation.

Launched by Claude Code as a subprocess via stdio transport. Exposes
browser tools (navigate, click, type_text, scroll, screenshot, get_dom,
get_url) and logs every call to trajectory.jsonl.

Configuration via environment variables (set by task_runner.py in .mcp.json):
  SESSION_ID  - unique session identifier (used for logging only)
  SITE_URL    - shop base URL, e.g. "http://localhost:5199"
  RUNS_DIR    - path to session directory for trajectory + Playwright artifacts
  HEADED      - "1" for headed browser, "0" for headless (default)

IMPORTANT: stdout is reserved for JSON-RPC messages (FastMCP/stdio protocol).
           All logging goes to stderr. Never print() to stdout.

Playwright artifacts written to RUNS_DIR:
  trajectory.jsonl  - one line per tool call (written live)
  trajectory.txt    - human-readable summary (written at shutdown)
  trace.zip         - Playwright trace (interactive replay)
  video.webm        - full session video
"""

from __future__ import annotations

import asyncio
import base64
import os
import signal
import sys
import time
from pathlib import Path

from fastmcp import FastMCP
from playwright.async_api import async_playwright, BrowserContext, Page

from agent_eval.trajectory import TrajectoryWriter

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

SESSION_ID = os.environ.get("SESSION_ID", "unknown")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:5199")
RUNS_DIR = os.environ.get("RUNS_DIR", "_tmp/runs/unknown")
HEADED = os.environ.get("HEADED", "0") == "1"

# ---------------------------------------------------------------------------
# Global state (set during lifespan)
# ---------------------------------------------------------------------------

_pw = None
_browser = None
_context: BrowserContext | None = None
_page: Page | None = None
_writer: TrajectoryWriter | None = None
_step_counter = 0
_started_at: float = 0.0

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("browser")


# ---------------------------------------------------------------------------
# Lifespan: start browser on startup, save artifacts on shutdown
# ---------------------------------------------------------------------------

async def _start_browser() -> None:
    """Launch Playwright, open Chromium, start video recording and tracing."""
    global _pw, _browser, _context, _page, _writer, _started_at

    _started_at = time.time()
    runs_path = Path(RUNS_DIR)
    runs_path.mkdir(parents=True, exist_ok=True)

    _writer = TrajectoryWriter(RUNS_DIR)

    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(headless=not HEADED)
    _context = await _browser.new_context(
        record_video_dir=str(runs_path) + "/",
    )
    await _context.tracing.start(screenshots=True, snapshots=True, sources=True)
    _page = await _context.new_page()

    # Navigate to the shop homepage so Claude starts with the site already open.
    # This means Claude never needs to know the URL — it just sees the shop.
    try:
        await _page.goto(SITE_URL)
        await _page.wait_for_load_state("networkidle")
        print(f"[mcp_server] navigated to {SITE_URL}", file=sys.stderr)
    except Exception as e:
        print(f"[mcp_server] initial navigation failed: {e}", file=sys.stderr)

    print(f"[mcp_server] browser started  session={SESSION_ID}  headed={HEADED}  site={SITE_URL}", file=sys.stderr)


async def _shutdown() -> None:
    """Stop tracing, save trace.zip, close browser. Called on SIGTERM and atexit."""
    global _pw, _browser, _context, _page

    print(f"[mcp_server] shutting down  session={SESSION_ID}", file=sys.stderr)

    # Write human-readable trajectory summary
    if _writer is not None:
        try:
            task_name = os.environ.get("TASK_NAME", "unknown")
            goal = os.environ.get("TASK_GOAL", "")
            _writer.write_human_readable(task_name, goal, _started_at)
        except Exception as e:
            print(f"[mcp_server] trajectory.txt write failed: {e}", file=sys.stderr)

    # Stop Playwright tracing and save trace.zip
    if _context is not None:
        try:
            trace_path = str(Path(RUNS_DIR) / "trace.zip")
            await _context.tracing.stop(path=trace_path)
            print(f"[mcp_server] trace saved: {trace_path}", file=sys.stderr)
        except Exception as e:
            print(f"[mcp_server] trace save failed: {e}", file=sys.stderr)
        try:
            await _context.close()
        except Exception:
            pass
        _context = None
        _page = None

    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None

    if _pw is not None:
        try:
            await _pw.stop()
        except Exception:
            pass
        _pw = None


def _handle_sigterm(*_) -> None:
    """SIGTERM handler: run async shutdown then exit."""
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(_shutdown())
    else:
        loop.run_until_complete(_shutdown())
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)


# ---------------------------------------------------------------------------
# Accessibility tree helper
# ---------------------------------------------------------------------------

async def _get_accessibility_tree() -> str:
    """Return Playwright's ARIA snapshot as compact YAML-like text.

    Uses page.aria_snapshot() (Playwright >= 1.49).  Returns a string like:
        - heading "Products" [level=1]
        - link "Widget A $9.99":
          - /url: /product/123
        - button "Add to Cart"
    """
    snap = await _page.aria_snapshot()
    return snap if snap else "(empty accessibility tree)"


# ---------------------------------------------------------------------------
# Internal helper: log + execute a tool call
# ---------------------------------------------------------------------------

async def _run_tool(tool_name: str, args: dict, execute_fn) -> str:
    """
    Log the tool call to trajectory.jsonl, execute it, log the result.

    Args:
        tool_name:  MCP tool name string.
        args:       Tool arguments dict.
        execute_fn: Async callable that performs the action and returns a result string.

    Returns:
        Result string from execute_fn.  On error, returns an "ERROR: ..." string
        so Claude receives the error as a tool result and can recover — the MCP
        server never crashes due to bad agent input (wrong selectors, bad URLs, etc.).
    """
    global _step_counter
    _step_counter += 1
    step = _step_counter
    t0 = time.time()

    print(f"[mcp_server] step={step}  tool={tool_name}  args={args}", file=sys.stderr)

    result = None
    error = None
    try:
        result = await execute_fn()
    except Exception as e:
        error = str(e)
        print(f"[mcp_server] step={step}  ERROR: {error}", file=sys.stderr)

    elapsed = time.time() - t0

    if _writer is not None:
        _writer.write_step(step, tool_name, args, result, error, elapsed)

    if error is not None:
        # Return the error as a string result — Claude sees it and can retry
        # with a corrected selector/URL.  Do NOT raise: that would crash FastMCP
        # and produce a confusing traceback instead of a clean error message.
        first_line = error.splitlines()[0] if error else "unknown error"
        return f"ERROR: {first_line}"

    return result


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool
async def navigate(url: str) -> str:
    """Navigate the browser to a URL.

    Returns the accessibility tree of the resulting page — the full page state
    is already in the response, so there is no need to call get_url or get_dom
    after navigate.
    """
    async def _execute():
        await _page.goto(url)
        await _page.wait_for_load_state("networkidle")
        return await _get_accessibility_tree()
    return await _run_tool("navigate", {"url": url}, _execute)


@mcp.tool
async def click(selector: str) -> str:
    """Click an element and return the accessibility tree of the resulting page.

    The full page state after the click is already in the response — there is
    no need to call get_url or get_dom afterwards.

    selector is a CSS/Playwright selector. Examples:
      text=Cancel Order           visible text match (preferred)
      button:has-text("Submit")   button containing text
      #my-id                      CSS id
      .my-class                   CSS class
      input[name="qty"]           attribute selector

    Do NOT use ARIA roles as selectors (e.g. "button[Cancel Order]" is invalid).
    Do NOT use attribute selectors with unquoted spaces.
    """
    async def _execute():
        await _page.click(selector)
        await _page.wait_for_load_state("networkidle")
        return await _get_accessibility_tree()
    return await _run_tool("click", {"selector": selector}, _execute)


@mcp.tool
async def type_text(selector: str, text: str) -> str:
    """Fill a text input with the given text.

    selector must be a CSS selector pointing to an <input> or <textarea>.
    Examples:
      #name                        input with id="name"
      input[placeholder="Search"]  input by placeholder
      input[type="number"]         number input
      input[name="code"]           input by name attribute

    IMPORTANT: use CSS selectors, NOT ARIA roles.
      Correct:   input[name="code"]
      Wrong:     textbox[name="code"]   ← ARIA role, will timeout

    For <select> dropdowns use select_option instead of type_text.
    """
    async def _execute():
        await _page.fill(selector, text)
        return "ok"
    return await _run_tool("type_text", {"selector": selector, "text": text}, _execute)


@mcp.tool
async def select_option(selector: str, value: str) -> str:
    """Select an option in a <select> dropdown by its value attribute.

    Use this for any <select> element — type_text does not work on dropdowns.

    Args:
        selector: CSS selector for the <select> element.
                  Examples: "#state", "select[name='state']"
        value:    The option's value attribute to select.
                  Examples: "IL" (Illinois), "CA" (California)

    The checkout page State field is <select id="state"> — select it with:
        select_option("#state", "IL")
    """
    async def _execute():
        await _page.select_option(selector, value=value)
        return "ok"
    return await _run_tool("select_option", {"selector": selector, "value": value}, _execute)


@mcp.tool
async def scroll(direction: str, amount: int = 300) -> str:
    """
    Scroll the page.

    Args:
        direction: "down" or "up"
        amount:    Pixels to scroll (default 300)
    """
    async def _execute():
        delta = amount if direction == "down" else -amount
        await _page.mouse.wheel(0, delta)
        return "ok"
    return await _run_tool("scroll", {"direction": direction, "amount": amount}, _execute)


@mcp.tool
async def screenshot() -> str:
    """Take a screenshot of the current page and return it as a base64-encoded PNG."""
    global _step_counter
    # Determine the next step number for the filename (peek ahead)
    next_step = _step_counter + 1

    async def _execute():
        png_bytes = await _page.screenshot()
        # Save PNG to disk so trajectory.txt can reference it by name
        screenshots_dir = Path(RUNS_DIR) / "screenshots"
        screenshots_dir.mkdir(exist_ok=True)
        png_path = screenshots_dir / f"step_{next_step:03d}.png"
        with open(png_path, "wb") as f:
            f.write(png_bytes)
        # Return base64 to Claude (it needs the image content) but log the filename
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return f"data:image/png;base64,{b64}"

    result = await _run_tool("screenshot", {}, _execute)
    return result


@mcp.tool
async def get_dom() -> str:
    """Return the full HTML content of the current page."""
    async def _execute():
        html = await _page.content()
        return html
    result = await _run_tool("get_dom", {}, _execute)
    return result


@mcp.tool
async def get_url() -> str:
    """Return the current page URL."""
    async def _execute():
        return _page.url
    return await _run_tool("get_url", {}, _execute)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main() -> None:
    await _start_browser()
    try:
        # run_stdio_async: show_banner=False is critical - any stdout output
        # other than JSON-RPC messages corrupts the stdio protocol.
        await mcp.run_stdio_async(show_banner=False)
    finally:
        await _shutdown()


def main() -> None:
    """Entry point: start browser, serve MCP tools over stdio, shutdown on exit."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
