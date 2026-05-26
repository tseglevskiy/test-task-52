"""
agent_eval/validators/llm_judge.py -- LLMJudgeValidator

LLM-judge trajectory validator using the OpenRouter API (OpenAI-compatible).

Reads OPENROUTER_API_KEY and OPENROUTER_MODEL from the project .env file
(or environment variables), sends the recorded trajectory to the model with
a task-specific rubric (defined in tasks/<task_name>.py), and returns a
structured verdict.

Returns:
    {"passed": bool, "violations": list[str], "reasoning": str}
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

from agent_eval.validators.base import AbstractTrajectoryValidator

# Project root is two levels up from this file
_ROOT = Path(__file__).parent.parent.parent
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _load_env() -> dict:
    """Load OPENROUTER_API_KEY and OPENROUTER_MODEL from .env or environment.

    Returns:
        {"api_key": str, "model": str}

    Raises:
        RuntimeError: if OPENROUTER_API_KEY is not set.
    """
    # Load .env from project root (does not override already-set env vars)
    env_path = _ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()

    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. "
            "Add it to .env or set it as an environment variable."
        )

    return {"api_key": api_key, "model": model}


def _get_rubric(task_name: str) -> str:
    """Load the task class and return its rubric string.

    The rubric is defined in tasks/<task_name>.py as task.rubric().
    This keeps all task-specific knowledge in the tasks/ package.

    Args:
        task_name: One of "cancel_order", "apply_coupon", "buy_cheapest".

    Returns:
        Multi-line rubric string.

    Raises:
        ValueError: if task_name is not recognized.
    """
    sys.path.insert(0, str(_ROOT))
    if task_name == "cancel_order":
        from tasks.cancel_order import CancelRecentOrderTask
        return CancelRecentOrderTask().rubric()
    elif task_name == "apply_coupon":
        from tasks.apply_coupon import ApplyCouponWithQuantityTask
        return ApplyCouponWithQuantityTask().rubric()
    elif task_name == "buy_cheapest":
        from tasks.buy_cheapest import BuyCheapestInCategoryTask
        return BuyCheapestInCategoryTask().rubric()
    else:
        raise ValueError(
            f"Unknown task {task_name!r}. "
            "Known tasks: cancel_order, apply_coupon, buy_cheapest"
        )


def _format_trajectory(trajectory: list[dict]) -> str:
    """Convert trajectory steps to a compact text block for the LLM prompt.

    Each step is formatted as:
        Step N: tool(arg=val, ...) -> result_summary

    Base64 image data is replaced with '<screenshot>'.
    Long results are truncated to 300 chars.

    Args:
        trajectory: List of step dicts from TrajectoryWriter.load().

    Returns:
        Multi-line string, one line per step.
    """
    if not trajectory:
        return "(empty trajectory — no tool calls recorded)"

    lines = []
    for step in trajectory:
        n = step.get("step", "?")
        tool = step.get("tool", "?")
        args = step.get("args", {})
        result = step.get("result") or ""
        error = step.get("error")
        elapsed = step.get("elapsed")

        # Format args compactly
        if args:
            args_parts = []
            for k, v in args.items():
                v_str = str(v)
                if len(v_str) > 60:
                    v_str = v_str[:60] + "..."
                args_parts.append(f"{k}={v_str!r}")
            args_str = ", ".join(args_parts)
        else:
            args_str = ""

        # Format result
        if error:
            result_str = f"ERROR: {error[:200]}"
        elif result.startswith("data:image/"):
            result_str = "<screenshot>"
        elif result.startswith("screenshots/"):
            result_str = "<screenshot>"
        else:
            if len(result) > 300:
                result_str = result[:300] + "..."
            else:
                result_str = result if result else "ok"

        timing = f" ({elapsed:.2f}s)" if elapsed is not None else ""
        lines.append(f"Step {n}: {tool}({args_str}) -> {result_str}{timing}")

    return "\n".join(lines)


def _call_openrouter(api_key: str, model: str, prompt: str) -> str:
    """POST to OpenRouter chat completions and return the assistant's reply.

    Args:
        api_key: OpenRouter API key.
        model:   Model identifier (e.g. "tencent/hy3-preview").
        prompt:  Full user prompt string.

    Returns:
        Raw content string from choices[0].message.content.

    Raises:
        RuntimeError: on HTTP error or unexpected response shape.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }

    try:
        resp = httpx.post(
            _OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=300.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"OpenRouter API returned HTTP {e.response.status_code}: {e.response.text[:500]}"
        ) from e
    except httpx.RequestError as e:
        raise RuntimeError(f"OpenRouter API request failed: {e}") from e

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(
            f"Unexpected OpenRouter response shape: {resp.text[:500]}"
        ) from e

    return content


def _parse_verdict(raw: str) -> dict:
    """Parse the LLM's response into a verdict dict.

    Handles:
    - Plain JSON response
    - JSON wrapped in a markdown code block (```json ... ```)

    On any parse failure, returns a safe default with passed=False.

    Args:
        raw: Raw string from the LLM.

    Returns:
        {"passed": bool, "violations": list[str], "reasoning": str}
    """
    text = raw.strip()

    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON object anywhere in the text
        brace_match = re.search(r"\{[\s\S]*\}", text)
        if brace_match:
            try:
                obj = json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                return {
                    "passed": False,
                    "violations": [f"LLM response could not be parsed as JSON: {raw[:200]}"],
                    "reasoning": raw,
                }
        else:
            return {
                "passed": False,
                "violations": [f"LLM response could not be parsed as JSON: {raw[:200]}"],
                "reasoning": raw,
            }

    # Validate and normalise fields
    passed = bool(obj.get("passed", False))
    violations = obj.get("violations", [])
    if not isinstance(violations, list):
        violations = [str(violations)]
    reasoning = str(obj.get("reasoning", ""))

    return {"passed": passed, "violations": violations, "reasoning": reasoning}


# ---------------------------------------------------------------------------
# Validator class
# ---------------------------------------------------------------------------

class LLMJudgeValidator(AbstractTrajectoryValidator):
    """Trajectory validator that uses an LLM judge via OpenRouter.

    Reads OPENROUTER_API_KEY and OPENROUTER_MODEL from .env (project root)
    or environment variables. Fetches the task rubric from the task class
    (tasks/<task_name>.py) and sends the trajectory to the model for judgment.
    """

    def __init__(self) -> None:
        env = _load_env()
        self._api_key: str = env["api_key"]
        self._model: str = env["model"]

    def validate(
        self,
        trajectory: list[dict],
        task_name: str,
        goal: str,
    ) -> dict:
        """Validate the agent's trajectory using an LLM judge.

        Args:
            trajectory: List of step dicts from TrajectoryWriter.load().
            task_name:  Task identifier, e.g. "cancel_order".
            goal:       Natural-language goal string shown to the agent.

        Returns:
            {"passed": bool, "violations": list[str], "reasoning": str}
        """
        try:
            traj_text = _format_trajectory(trajectory)
            rubric = _get_rubric(task_name)

            prompt = (
                "You are an impartial judge evaluating whether an AI agent completed "
                "a web task correctly.\n\n"
                f"TASK: {task_name}\n"
                f"GOAL: {goal}\n\n"
                "RUBRIC (criteria the agent must satisfy):\n"
                f"{rubric}\n\n"
                "TRAJECTORY (sequence of browser tool calls the agent made):\n"
                f"{traj_text}\n\n"
                "Based on the rubric, evaluate the trajectory. "
                "Respond with ONLY a JSON object — no explanation outside the JSON:\n"
                "{\n"
                '  "passed": true or false,\n'
                '  "violations": ["violation 1", "violation 2"],\n'
                '  "reasoning": "brief explanation of your verdict"\n'
                "}"
            )

            print(
                f"[LLMJudgeValidator] Calling {self._model} to judge trajectory "
                f"({len(trajectory)} steps, task={task_name!r}) ...",
                flush=True,
            )

            raw = _call_openrouter(self._api_key, self._model, prompt)
            verdict = _parse_verdict(raw)

            print(
                f"[LLMJudgeValidator] verdict: passed={verdict['passed']}, "
                f"violations={verdict['violations']}",
                flush=True,
            )
            return verdict

        except Exception as e:  # noqa: BLE001
            print(f"[LLMJudgeValidator] ERROR: {e}", flush=True)
            return {
                "passed": False,
                "violations": [f"Validator error: {e}"],
                "reasoning": str(e),
            }
