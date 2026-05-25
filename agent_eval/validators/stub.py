"""
agent_eval/validators/stub.py — StubValidator.

Always returns passed=True. Used as a placeholder until a real
LLM-judge-based validator is implemented.

To implement a real validator:
  1. Subclass AbstractTrajectoryValidator in a new file.
  2. Implement validate() with your rubric logic.
  3. Pass an instance of your class to run_session() in task_runner.py.
"""

from agent_eval.validators.base import AbstractTrajectoryValidator


class StubValidator(AbstractTrajectoryValidator):
    """Trajectory validator that always passes. Stub — not implemented."""

    def validate(
        self,
        trajectory: list[dict],
        task_name: str,
        goal: str,
    ) -> dict:
        return {
            "passed": True,
            "violations": [],
            "reasoning": "stub — trajectory validation not implemented",
        }
