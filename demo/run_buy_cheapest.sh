#!/usr/bin/env bash
# demo/run_buy_cheapest.sh — Run the buy_cheapest evaluation task
#
# Task: Find the cheapest item in the Electronics category → add it to cart →
#       complete checkout with shipping address 123 Main St, Springfield, IL 62701.
# The verifier checks the backend DB to confirm the correct item was purchased
# at the lowest price and the shipping address matches.
#
# Usage:
#   ./demo/run_buy_cheapest.sh                        # uses 'claude' from PATH
#   ./demo/run_buy_cheapest.sh /path/to/claude        # explicit claude binary
#   CLAUDE_BIN=/usr/local/bin/claude ./demo/run_buy_cheapest.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------------------------------------------------------------------------
# Resolve claude binary
# ---------------------------------------------------------------------------
CLAUDE_BIN="${1:-${CLAUDE_BIN:-claude}}"

if ! command -v "$CLAUDE_BIN" &>/dev/null 2>&1; then
    echo "ERROR: claude binary not found: '$CLAUDE_BIN'"
    echo ""
    echo "Install Claude Code CLI and make sure it is on your PATH, or pass the"
    echo "path as the first argument:"
    echo "  ./demo/run_buy_cheapest.sh /path/to/claude"
    echo ""
    echo "Find the path with:  which claude"
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve Python interpreter
# ---------------------------------------------------------------------------
VENV_PYTHON="$REPO_ROOT/agent_eval/.venv/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: agent_eval/.venv not found."
    echo ""
    echo "Run the one-time setup first:"
    echo "  cd $REPO_ROOT"
    echo "  python -m venv agent_eval/.venv"
    echo "  agent_eval/.venv/bin/pip install -r agent_eval/requirements.txt"
    echo "  agent_eval/.venv/bin/playwright install chromium"
    exit 1
fi

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
echo "=== ShopGym Demo: buy_cheapest ==="
echo "  claude:  $CLAUDE_BIN"
echo "  python:  $VENV_PYTHON"
echo ""

exec "$VENV_PYTHON" "$REPO_ROOT/agent_eval/task_runner.py" \
    --task buy_cheapest \
    --seed 0 \
    --claude "$CLAUDE_BIN"
