#!/usr/bin/env bash
# emergency-ai smoke test: tests + mock CLI demo
# One command. Green output if everything works.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -d .venv ]; then
  echo "▸ creating venv (one time, ~30 s)"
  /usr/local/bin/python3.12 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -e ".[dev]"
fi

echo
echo "════════════════════════════════════════════════════════════════"
echo "  emergency-ai smoke test"
echo "════════════════════════════════════════════════════════════════"
echo

echo "▸ pytest (39 tests)"
./.venv/bin/pytest -q --tb=short
echo

echo "▸ mock CLI: subway collapse, New York"
./.venv/bin/emergency "person collapsed on subway platform, not breathing" --city "New York" --mock
echo

echo "▸ mock CLI: kitchen fire, London"
./.venv/bin/emergency "smoke from kitchen, kids in apartment" --city "London" --mock
echo

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "▸ LIVE CLI (ANTHROPIC_API_KEY detected): real Haiku call, New York"
  ./.venv/bin/emergency "person clutching chest, sweating, pale" --city "New York"
else
  echo "▸ skipping live CLI — export ANTHROPIC_API_KEY to test the real model"
fi

echo
echo "✓ done. all systems nominal."
