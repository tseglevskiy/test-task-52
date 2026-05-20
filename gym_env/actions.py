"""
gym_env/actions.py — JSON action string → Playwright call.

Supported action types:
  {"type": "click",           "x": float, "y": float}
  {"type": "type",            "text": str}
  {"type": "scroll",          "x": float, "y": float, "delta_y": float, "delta_x": float}
  {"type": "navigate",        "url": str}
  {"type": "click_by_role",   "role": str, "name": str}
  {"type": "press",           "key": str}
  {"type": "select_option",   "selector": str, "value": str}

Raises:
  json.JSONDecodeError  — malformed JSON
  ValueError            — unknown action type
"""

import json

from playwright.sync_api import Page


def execute_action(page: Page, action: str) -> None:
    """
    Parse a JSON action string and execute it on the Playwright page.

    Args:
        page:   Active Playwright page.
        action: JSON-encoded action string.

    Raises:
        json.JSONDecodeError: If action is not valid JSON.
        ValueError:           If action type is not recognised.
    """
    act = json.loads(action)
    t = act["type"]

    if t == "click":
        # Coordinate-based click — no actionability wait, matches screenshot pixels.
        page.mouse.click(act["x"], act["y"])

    elif t == "type":
        page.keyboard.type(act["text"])

    elif t == "scroll":
        # Move mouse to (x, y) first so the correct element is scrolled
        # (matters for pages with nested scrollable regions).
        page.mouse.move(act["x"], act["y"])
        page.mouse.wheel(act.get("delta_x", 0), act["delta_y"])

    elif t == "navigate":
        page.goto(act["url"])
        page.wait_for_load_state("networkidle")

    elif t == "click_by_role":
        # Click an element identified by its ARIA role and accessible name.
        # Maps directly to what the axtree exposes — agents can read the axtree
        # and emit this action without needing pixel coordinates.
        # Example: {"type": "click_by_role", "role": "button", "name": "Cancel Order"}
        page.get_by_role(act["role"], name=act["name"]).click()
        page.wait_for_load_state("networkidle")

    elif t == "press":
        # Press a keyboard key or chord, e.g. "Control+a", "Enter", "Tab".
        # Maps to page.keyboard.press() — use this for shortcuts that cannot
        # be sent via keyboard.type().
        page.keyboard.press(act["key"])

    elif t == "select_option":
        # Select a dropdown option by value.
        # Example: {"type": "select_option", "selector": "select[name='state']", "value": "IL"}
        page.locator(act["selector"]).select_option(act["value"])
        page.wait_for_load_state("networkidle")

    else:
        raise ValueError(f"Unknown action type: {t!r}")
