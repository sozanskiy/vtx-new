#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ ! -d "${ROOT_DIR}/.venv" ]]; then
  echo "Creating local venv..."
  python3 -m venv --system-site-packages "${ROOT_DIR}/.venv"
fi

source "${ROOT_DIR}/.venv/bin/activate"
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r "${ROOT_DIR}/requirements.txt"

export RER_USE_HW=${RER_USE_HW:-0}
exec uvicorn app.api:app --host 0.0.0.0 --port 8080 --reload
