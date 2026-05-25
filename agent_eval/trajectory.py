"""
agent_eval/trajectory.py — TrajectoryWriter

Records every MCP tool call to trajectory.jsonl (machine-readable) and
writes a human-readable trajectory.txt at session end.

trajectory.jsonl format — one JSON object per line:
  {
    "step":      int,        # 1-based counter
    "timestamp": float,      # time.time() at call start
    "tool":      str,        # tool name
    "args":      dict,       # tool arguments
    "result":    str | null, # short result summary
    "error":     str | null  # exception message, or null
  }

trajectory.txt format — human-readable numbered steps:
  Task: <goal>
  Session: <session_id>  Started: <ISO timestamp>

  Step  1  navigate        url=http://localhost:5199/
           → ok  (0.8s)
  Step  2  get_dom
           → 4821 chars  (0.1s)
  ...
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


class TrajectoryWriter:
    """
    Writes trajectory data for one agent session.

    Usage (in mcp_server.py):
        writer = TrajectoryWriter(runs_dir="/path/to/runs/session_id")
        writer.write_step(1, "navigate", {"url": "http://..."}, "ok", None)
        ...
        writer.write_human_readable(task_name="cancel_order",
                                    goal="Cancel the most recent order.",
                                    started_at=1716500000.0)
    """

    def __init__(self, runs_dir: str) -> None:
        """
        Args:
            runs_dir: Path to the session directory where files are written.
                      Created if it doesn't exist.
        """
        self._runs_dir = Path(runs_dir)
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_path = self._runs_dir / "trajectory.jsonl"
        self._txt_path = self._runs_dir / "trajectory.txt"

    def write_step(
        self,
        step: int,
        tool: str,
        args: dict,
        result: str | None,
        error: str | None,
        elapsed: float | None = None,
    ) -> None:
        """
        Append one tool call to trajectory.jsonl.

        Screenshots are stored as a filename reference (screenshots/step_NNN.png)
        rather than the full base64 blob, keeping the JSONL file human-readable.

        Args:
            step:    1-based step counter.
            tool:    MCP tool name (e.g. "navigate", "click").
            args:    Tool arguments dict as received from the agent.
            result:  Short result summary string, or None on error.
            error:   Exception message string, or None on success.
            elapsed: Wall-clock seconds the tool call took, or None.
        """
        # Replace base64 screenshot data with a filename reference
        stored_result = result
        if tool == "screenshot" and result and result.startswith("data:image/"):
            stored_result = f"screenshots/step_{step:03d}.png"

        # Truncate very long results (e.g. full HTML from get_dom)
        if stored_result and len(stored_result) > 500 and tool != "screenshot":
            stored_result = stored_result[:500] + f"... [{len(stored_result)} chars total]"

        entry: dict = {
            "step": step,
            "timestamp": time.time(),
            "tool": tool,
            "args": args,
            "result": stored_result,
            "error": error,
        }
        if elapsed is not None:
            entry["elapsed"] = round(elapsed, 3)

        with open(self._jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def load(self) -> list[dict]:
        """
        Load all recorded steps from trajectory.jsonl.

        Returns:
            List of step dicts in order. Empty list if file doesn't exist.
        """
        if not self._jsonl_path.exists():
            return []
        steps = []
        with open(self._jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        steps.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return steps

    def write_human_readable(
        self,
        task_name: str,
        goal: str,
        started_at: float,
    ) -> None:
        """
        Write trajectory.txt — a human-readable summary of the session.

        Reads from trajectory.jsonl, so call this after all steps are written.

        Args:
            task_name:  Task identifier, e.g. "cancel_order".
            goal:       Natural-language goal string shown to the agent.
            started_at: Session start time as a Unix timestamp.
        """
        steps = self.load()
        started_dt = datetime.fromtimestamp(started_at, tz=timezone.utc)
        started_str = started_dt.strftime("%Y-%m-%d %H:%M:%S UTC")

        lines = [
            f"Task:    {task_name}",
            f"Goal:    {goal}",
            f"Started: {started_str}",
            f"Steps:   {len(steps)}",
            "",
        ]

        for s in steps:
            step_num = s.get("step", "?")
            tool = s.get("tool", "?")
            args = s.get("args", {})
            result = s.get("result")
            error = s.get("error")
            elapsed = s.get("elapsed")

            # Format args as key=value pairs, truncating long values
            args_str = _format_args(args)

            # Header line: "Step  1  navigate        url=http://..."
            header = f"Step {step_num:>3}  {tool:<16}{args_str}"
            lines.append(header)

            # Result line: "         → ok  (0.8s)"
            if error:
                result_str = f"ERROR: {error}"
            elif result is not None:
                result_str = result
            else:
                result_str = "ok"

            timing = f"  ({elapsed:.2f}s)" if elapsed is not None else ""
            lines.append(f"         → {result_str}{timing}")

        lines.append("")  # trailing newline

        with open(self._txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


def _format_args(args: dict, max_value_len: int = 80) -> str:
    """Format a dict as 'key=value key=value', truncating long values."""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > max_value_len:
            v_str = v_str[:max_value_len] + "…"
        parts.append(f"{k}={v_str}")
    return "  ".join(parts)
