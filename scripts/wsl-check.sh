#!/usr/bin/env bash
# LIVE Genesis: set up WSL (or any Linux) and check that the agent engine works.
# Free: contacts no model provider and reads no API key. Run from the repo root:
#     bash scripts/wsl-check.sh
set -euo pipefail
cd "$(dirname "$0")/.."

case "$PWD" in /mnt/*)
  echo "This folder is on the Windows drive ($PWD). SQLite locking is unreliable there."
  echo "Clone the repo inside Linux instead, for example:  git clone <repo-url> ~/live-genesis"
  exit 1;;
esac

step() { printf '\n== %s\n' "$1"; }

step "1/6 Python"
if ! command -v python3 >/dev/null; then
  echo "python3 is missing. Run:  sudo apt update && sudo apt install -y python3 python3-venv python3-pip"; exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10 or newer is required"; print(sys.version.split()[0])'
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  echo "The venv module is missing. Run:  sudo apt update && sudo apt install -y python3-venv python3-pip"; exit 1
fi

step "2/6 Virtual environment and packages"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt -r requirements-anthropic.txt
echo "packages installed in .venv"

step "3/6 Full offline test suite"
python -m pytest -q

step "4/6 Engine tests (budget holds, receipts, provenance, hardening; the model is mocked)"
python -m pytest -q tests/test_engine.py tests/test_engine_hardening.py tests/test_budget.py tests/test_provenance.py

# Everything below uses a throwaway database, so your real database and keys are untouched.
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
export LIVE_DB="$SCRATCH/check.db"
unset OPENAI_API_KEY ANTHROPIC_API_KEY LIVE_ALLOW_PAID_CALLS || true

step "5/6 Budget gate on a throwaway database"
python -m live_genesis.cli reconcile

step "6/6 Control-study dry run (built-in FAKE model; the numbers mean nothing)"
python -m live_genesis.cli study-dry-run > "$SCRATCH/dry-run.txt"
head -n 12 "$SCRATCH/dry-run.txt"

cat <<'MSG'

All checks passed. The engine's gates, receipts and accounting work on this machine.
What this did NOT check: a real model call. That is the paid step and only you can run it:
    see docs/FIRST-REAL-ROUND.md
To use the environment later:   . .venv/bin/activate
MSG
