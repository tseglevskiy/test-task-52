"""
agent_eval/validators/deterministic.py -- DeterministicValidator

Rule-based trajectory validator. Delegates to the task's check_trajectory()
method, which is defined in tasks/<task_name>.py alongside the task's other
logic (seed_requirements, setup, verify, rubric).

No external calls, no randomness — instant, free, fully reproducible.
Same input always produces the same output.

Returns:
    {"passed": bool, "violations": list[str], "reasoning": str}
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_eval.validators.base import AbstractTrajectoryValidator

_ROOT = Path(__file__).parent.parent.parent


def _load_task_instance(task_name: str):
    """Instantiate the task class for the given task name.

    Args:
        task_name: One of "cancel_order", "apply_coupon", "buy_cheapest".

    Returns:
        An instance of the corresponding AbstractTask subclass.

    Raises:
        ValueError: if task_name is not recognized.
    """
    sys.path.insert(0, str(_ROOT))
    if task_name == "cancel_order":
        from tasks.cancel_order import CancelRecentOrderTask
        return CancelRecentOrderTask()
    elif task_name == "apply_coupon":
        from tasks.apply_coupon import ApplyCouponWithQuantityTask
        return ApplyCouponWithQuantityTask()
    elif task_name == "buy_cheapest":
        from tasks.buy_cheapest import BuyCheapestInCategoryTask
        return BuyCheapestInCategoryTask()
    else:
        raise ValueError(
            f"Unknown task {task_name!r}. "
            "Known tasks: cancel_order, apply_coupon, buy_cheapest"
        )


class DeterministicValidator(AbstractTrajectoryValidator):
    """Trajectory validator using rule-based checks defined in each task file.

    Delegates to task.check_trajectory(trajectory), which inspects the
    recorded tool calls and returns a structured verdict with no external
    calls — instant, free, fully reproducible.
    """

    def validate(
        self,
        trajectory: list[dict],
        task_name: str,
        goal: str,
    ) -> dict:
        """Validate the agent's trajectory using deterministic rule checks.

        Args:
            trajectory: List of step dicts from TrajectoryWriter.load().
            task_name:  Task identifier, e.g. "cancel_order".
            goal:       Natural-language goal string (unused — checks are
                        defined in the task class, not derived from the goal).

        Returns:
            {"passed": bool, "violations": list[str], "reasoning": str}
        """
        try:
            task = _load_task_instance(task_name)
            result = task.check_trajectory(trajectory)

            print(
                f"[DeterministicValidator] task={task_name!r} "
                f"passed={result['passed']} "
                f"violations={result['violations']}",
                flush=True,
            )
            return result

        except Exception as e:  # noqa: BLE001
            print(f"[DeterministicValidator] ERROR: {e}", flush=True)
            return {
                "passed": False,
                "violations": [f"Validator error: {e}"],
                "reasoning": str(e),
            }
