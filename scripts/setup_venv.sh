#!/usr/bin/env bash
# scripts/setup_venv.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "Creating venv at $ROOT/venv if absent..."
if [ ! -d "venv" ]; then
  python3 -m venv venv
fi

# Activate venv for the rest of this script
# shellcheck source=/dev/null
source venv/bin/activate

echo "Upgrading pip and installing backend deps..."
python -m pip install --upgrade pip setuptools wheel
pip install -r backend/requirements.txt

echo "If you want to setup frontend dependencies, run:"
echo "  cd frontend && npm install"

echo "Done. Activate venv with: source venv/bin/activate"
