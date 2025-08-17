#!/usr/bin/env bash
set -euo pipefail

# RER-Kilo full Pi setup: install OS deps, Python/Node, build UI, create services, and configure kiosk.

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root (sudo)."
  exit 1
fi

WORKDIR="${WORKDIR:-$(pwd)}"
APP_USER="${SUDO_USER:-${USER}}"
APP_GROUP="$(id -gn "${APP_USER}")"
USER_HOME="$(getent passwd "${APP_USER}" | cut -d: -f6)"

echo "[1/8] Installing OS packages (this may take a while)..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  hackrf gnuradio gr-osmosdr ffmpeg \
  python3-venv python3-pip python3-opencv python3-numpy python3-scipy python3-zmq \
  soapysdr-module-hackrf \
  curl ca-certificates \
  chromium-browser xserver-xorg xinit unclutter

if ! command -v node >/dev/null 2>&1; then
  echo "[2/8] Installing Node.js (NodeSource repo for Node 20)..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
fi

echo "[3/8] Creating Python venv and installing Python deps..."
python3 -m venv --system-site-packages "${WORKDIR}/.venv"
source "${WORKDIR}/.venv/bin/activate"
python -m pip install --upgrade pip wheel setuptools
if [[ -f "${WORKDIR}/requirements.txt" ]]; then
  python -m pip install -r "${WORKDIR}/requirements.txt"
else
  python -m pip install fastapi "uvicorn[standard]" pydantic pyzmq orjson
fi

echo "[4/8] Building frontend (Nuxt 3 Nitro)..."
pushd "${WORKDIR}/frontend" >/dev/null
npm ci
npm run build
popd >/dev/null

echo "[5/8] Creating systemd services (API + UI)..."
API_UNIT="/etc/systemd/system/rer-api.service"
cat > "${API_UNIT}" <<EOF
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

UI_UNIT="/etc/systemd/system/rer-ui.service"
cat > "${UI_UNIT}" <<EOF
[Unit]
Description=RER-Kilo UI (Nuxt 3 Nitro)
After=network-online.target rer-api.service
Wants=network-online.target rer-api.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${WORKDIR}/frontend
Environment=PORT=8081
ExecStart=/usr/bin/node .output/server/index.mjs
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now rer-api.service
systemctl enable --now rer-ui.service

echo "[6/8] Configuring LXDE autostart for kiosk (Chromium)..."
AUTOSTART_DIR="${USER_HOME}/.config/lxsession/LXDE-pi"
mkdir -p "${AUTOSTART_DIR}"
chown -R "${APP_USER}:${APP_GROUP}" "${USER_HOME}/.config"
cat > "${AUTOSTART_DIR}/autostart" <<'EOF'
@xset s off
@xset -dpms
@xset s noblank
@unclutter -idle 0.1 -root
@chromium-browser --noerrdialogs --disable-translate --disable-session-crashed-bubble --disable-infobars --kiosk http://localhost:8081 --check-for-update-interval=31536000 --incognito --start-fullscreen
EOF
chown "${APP_USER}:${APP_GROUP}" "${AUTOSTART_DIR}/autostart"

echo "[7/8] Trying to launch kiosk now if a desktop session is active..."
if pgrep -x "lxsession" >/dev/null 2>&1 || pgrep -x "Xorg" >/dev/null 2>&1; then
  sudo -u "${APP_USER}" env DISPLAY=:0 XAUTHORITY="${USER_HOME}/.Xauthority" chromium-browser --kiosk http://localhost:8081 --incognito --noerrdialogs --disable-infobars --disable-session-crashed-bubble --check-for-update-interval=31536000 >/dev/null 2>&1 &
else
  echo "No desktop session detected; kiosk will open on next login to the desktop."
fi

echo "[8/8] Done. API on :8080, UI on :8081."
echo "- To view logs: sudo journalctl -u rer-api.service -f and sudo journalctl -u rer-ui.service -f"
echo "- Ensure the Pi boots to desktop (Preferences → Raspberry Pi Configuration → System → Boot: To Desktop) and auto-login is enabled."
echo "- On next reboot or desktop login, Chromium kiosk will open to http://localhost:8081"
