#!/usr/bin/env bash
set -euo pipefail

# RER-Kilo Pi setup script: installs OS deps, prepares Python env, and creates a systemd service.

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root (sudo)."
  exit 1
fi

WORKDIR="${WORKDIR:-$(pwd)}"
APP_USER="${SUDO_USER:-${USER}}"
APP_GROUP="$(id -gn "${APP_USER}")"

echo "[1/5] Installing OS packages..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  hackrf gnuradio gr-osmosdr ffmpeg \
  python3-venv python3-pip python3-opencv python3-numpy python3-scipy python3-zmq \
  soapysdr-module-hackrf

echo "[2/5] Creating Python venv (with system site packages)..."
python3 -m venv --system-site-packages "${WORKDIR}/.venv"
source "${WORKDIR}/.venv/bin/activate"
python -m pip install --upgrade pip wheel setuptools

echo "[3/5] Installing Python packages (pip)..."
# Use apt-provided numpy/scipy/opencv via system site; install the rest via pip
python -m pip install \
  fastapi \
  "uvicorn[standard]" \
  pydantic \
  pyzmq \
  orjson

echo "[4/5] Creating systemd service..."
UNIT_PATH="/etc/systemd/system/rer-api.service"
cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=RER-Kilo API (FastAPI + SSE + MJPEG)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${WORKDIR}
Environment=RER_USE_HW=1
ExecStart=${WORKDIR}/.venv/bin/uvicorn app.api:app --host 0.0.0.0 --port 8080
ExecStopPost=/bin/bash -lc 'python - <<PY\ntry:\n  from app.hw_capture import SoapyHackRFSampler\n  s=SoapyHackRFSampler(); s.clear()\nexcept Exception:\n  pass\nPY'
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now rer-api.service

echo "[5/5] Done. Service status:"
systemctl --no-pager status rer-api.service || true

echo "\nRER-Kilo is installing/starting. Access the API at http://<pi-ip>:8080"
echo "Use: sudo journalctl -u rer-api.service -f  to follow logs."
