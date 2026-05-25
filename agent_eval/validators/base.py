"""
agent_eval/validators/base.py — AbstractTrajectoryValidator interface.

A trajectory validator inspects the recorded sequence of MCP tool calls
and returns a structured verdict on whether the agent's behavior was
acceptable — independent of the end-state verifier.

This separation matters because end-state verification alone can miss
bad behavior: an agent could cancel an order by constructing
`/orders/42/cancel` directly, bypassing the UI entirely. A trajectory
validator can catch this by checking that the agent navigated through
the orders list before selecting one.

The stub implementation (validators/stub.py) always passes. A real
implementation would use an LLM judge with a task-specific rubric.
"""

from abc import ABC, abstractmethod


class AbstractTrajectoryValidator(ABC):
    """
    Inspect a recorded trajectory and return a structured verdict.

    Implementations may use rule-based checks, an LLM judge, or both.
    The stub implementation always returns passed=True.
    """

    @abstractmethod
    def validate(
        self,
        trajectory: list[dict],
        task_name: str,
        goal: str,
    ) -> dict:
        """
        Validate the agent's trajectory for a completed session.

        Args:
            trajectory: List of TrajectoryStep dicts loaded from trajectory.jsonl.
                        Each dict has keys: step, timestamp, tool, args, result, error.
            task_name:  Task identifier, e.g. "cancel_order".
            goal:       Natural-language goal string shown to the agent.

        Returns:
            dict with keys:
              "passed":     bool        — True if trajectory is acceptable
              "violations": list[str]   — empty if passed; one entry per violation
              "reasoning":  str         — human-readable explanation
        """
        ...
