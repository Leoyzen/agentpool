#!/usr/bin/env bash
# git-bisect script for __aexit__ hang regression
#
# Usage:
#   git bisect start
#   git bisect bad refactor/session-debt-cleanup  # our branch (has the hang)
#   git bisect good refactor/agentwolf_v1          # base branch (no hang)
#   git bisect run scripts/bisect_aexit_hang.sh
#
# This script:
# 1. Installs the package
# 2. Starts a mock MCP server with proxy delay simulation
# 3. Runs a minimal agent turn
# 4. Exits 0 if turn completes within 30s (pass), 1 if it hangs (fail)

set -e

WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$WORKDIR"

echo "=== bisect_aexit_hang: building ==="
uv sync --quiet 2>&1 | tail -3

echo "=== bisect_aexit_hang: running test ==="
# Run the diagnostic in bisect mode with a mock MCP server
uv run python scripts/diag_aexit_hang.py --mock-proxy-delay 30 --bisect --timeout 30 2>&1 | tail -20
RESULT=$?

echo "=== bisect_aexit_hang: exit code $RESULT ==="
exit $RESULT
