"""
gym_env/observation.py — Build an observation dict from a Playwright page.

build_observation(page, goal) -> dict with keys:
  "url":        str   — page.url
  "axtree":     str   — page.aria_snapshot() (ARIA/YAML tree string)
  "screenshot": ndarray — shape (H, W, 3), dtype uint8
  "goal":       str   — task goal string, constant within episode
"""

import io
import json
import string

import numpy as np
from PIL import Image
from playwright.sync_api import Page

_PRINTABLE = frozenset(string.printable)


def _sanitize(s: str) -> str:
    """Strip characters outside string.printable so obs fits Text spaces."""
    return "".join(c for c in s if c in _PRINTABLE)


def build_observation(page: Page, goal: str) -> dict:
    """
    Snapshot the current page state and return an observation dict.

    Args:
        page: Active Playwright page.
        goal: Task goal string (constant within an episode).

    Returns:
        dict with keys "url", "axtree", "screenshot", "goal".
    """
    url = page.url

    # Accessibility tree as ARIA snapshot string.
    # page.aria_snapshot() (Playwright >= 1.47) returns a YAML-like string
    # representing the full ARIA tree. Falls back to empty string on error.
    try:
        axtree = page.aria_snapshot() or ""
    except Exception:
        axtree = ""

    # Screenshot as numpy array (H, W, 3) uint8.
    png_bytes = page.screenshot()
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    screenshot = np.array(img, dtype=np.uint8)

    return {
        "url":        _sanitize(url),
        "axtree":     _sanitize(axtree),
        "screenshot": screenshot,
        "goal":       _sanitize(goal),
    }
