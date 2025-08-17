# Run RER‑Kilo on Raspberry Pi

This guide installs dependencies, builds the UI, starts the API/UI as system services, and launches a Chromium kiosk.

## Quick install (one command)

```bash
# On the Pi (64‑bit), as root
sudo bash scripts/setup_full_pi.sh
```

This will:
- Install OS packages: HackRF, GNU Radio, Soapy HackRF, ffmpeg, Python, Node, Chromium, etc.
- Create a Python venv and install Python deps
- Build the Nuxt (Nitro) UI
- Create and start systemd services:
  - rer-api.service → FastAPI on :8080 (RER_USE_HW=1)
  - rer-ui.service → Nuxt Nitro on :8081
- Configure LXDE autostart to launch Chromium in kiosk on :8081

## Verify services

```bash
sudo systemctl status rer-api.service | cat
sudo systemctl status rer-ui.service | cat
sudo journalctl -u rer-api.service -f
sudo journalctl -u rer-ui.service -f
```

API: http://<pi-ip>:8080
UI: http://<pi-ip>:8081

## Development tips

- Backend only (no UI):
  ```bash
  sudo bash scripts/setup_pi.sh   # if you want only the API service
  ```
- Local dev with reload:
  ```bash
  bash scripts/run_api_dev.sh
  ```
- Toggle hardware capture:
  - API service uses `RER_USE_HW=1` by default. To force synthetic mode:
    ```bash
    sudo systemctl edit rer-api.service
    # Add/override: Environment=RER_USE_HW=0
    sudo systemctl daemon-reload && sudo systemctl restart rer-api.service
    ```

## Kiosk notes

- Ensure the Pi is set to boot to Desktop and auto‑login (Raspberry Pi Configuration → System).
- Autostart file: `~/.config/lxsession/LXDE-pi/autostart` opens Chromium in kiosk to `http://localhost:8081`.
- To disable kiosk: remove or comment Chromium line in that file and reboot.

## Uninstall

```bash
sudo systemctl disable --now rer-api.service rer-ui.service
sudo rm -f /etc/systemd/system/rer-api.service /etc/systemd/system/rer-ui.service
sudo systemctl daemon-reload
# Optionally remove venv and build artifacts
rm -rf .venv frontend/.output
```

