## RER‑Kilo MVP Implementation Plan (ASAP)

### Guiding principles

- **Simplicity first**: choose the least complex option that meets the acceptance criteria.
- **One box, one SDR**: Raspberry Pi 64‑bit + HackRF One (RX only) at 8 MS/s.
- **Tight loop**: implement → smoke test on bench → iterate.
- **Frontend**: Nuxt 3 + Tailwind CSS, kiosk‑ready.
- **Events**: use SSE for candidate/alert push (can switch to WebSocket later if needed).
- **Focus behavior**: scanning is paused while focused (for stability).
- **Audio**: omitted in MVP.

### High‑level sequence (no dates)

1. Repo scaffold and env setup
2. Config + channel plans
3. Candidate store + events
4. Scanner service (hardware + synthetic mode)
5. API Gateway (REST + SSE)
6. Frontend (Nuxt 3 + Tailwind kiosk)
7. GNU Radio demod flowgraph (focus path)
8. Video bridge (ZMQ → MJPEG)
9. Orchestration and systemd units
10. Bench test, tuning, and acceptance pass

---

## 1) Environment and dependencies

### OS packages (Pi)

- `hackrf`, `gnuradio`, `gr-osmosdr`, `ffmpeg`, `python3-pip`, `python3-numpy`, `python3-scipy`, `python3-opencv`, `python3-zmq`, `python3-venv`, `soapysdr-module-hackrf`

### Python packages

- `fastapi`, `uvicorn[standard]`, `pydantic`, `numpy`, `scipy`, `pyzmq`, `opencv-python` (SoapySDR via system pkgs)

### Quick sanity

- `hackrf_info` should detect the device
- `hackrf_transfer -r /dev/null -s 8000000 -f 5806000000` should run for a few seconds without drops

---

## 2) Repo scaffold

```
/app
  /config/channels.json           # default plan (5.8 GHz)
  /app/scanner.py                 # scan loop + metrics + ZMQ PUB + SQLite
  /app/api.py                     # FastAPI REST + SSE + minimal HTML UI
  /app/demod_flowgraph.grc        # GNU Radio 3.10 project (and generated .py)
  /app/video_bridge.py            # ZMQ SUB → MJPEG HTTP server
  /app/storage.db                 # SQLite (runtime)
  /logs/*.jsonl                   # structured logs (runtime)
```

Checklist:

- [x] Create directories and placeholder files
- [x] Add `requirements.txt` (Python deps only)
- [ ] Add `INSTALL.md` quickstart (can link to README)

---

## 3) Config and channel plans

- Implement loader/validator for `app/config/channels.json` with schema:
  - `bands[].name`, `bands[].channels[]`
  - `dwell_ms`, `sample_rate`, `channel_bw_hz`, `min_snr_db`, `alert_persistence{hits,window}`
- Provide default 5.8 GHz plan (Raceband + A/B/E/F/FS).

Checklist:

- [x] JSON loader (backend `/config` GET/PUT)
- [x] Default file with realistic values (8e6 SR, 15 ms dwell, min_snr_db 6)
- [ ] Validation (schema) and UI error feedback

---

## 4) Candidate store and events

- SQLite table `candidates(freq_hz PK, power_dbm, snr_db, ema_power, ema_snr, first_seen, last_seen, hits, status)`
- JSONL append logs for events and state transitions
- ZMQ PUB topic `candidates` publishing deltas `{freq_hz, snr_db, power_dbm, status, last_seen}`

Checklist:

- [x] Migrations/bootstrap for SQLite
- [x] Simple repository functions (upsert/update/queries)
- [ ] ZMQ publisher helper (optional; SSE used in MVP)

---

## 5) Scanner service (Python, asyncio)

- HackRF tune per channel with `sr=8e6`, dwell 10–20 ms
- Capture N = `sr * dwell_ms / 1000` complex samples
- Metrics:
  - Mean removal
  - PSD (Hann + rFFT), in‑band integration with DC guard (±50 kHz)
  - Noise estimate from edge bins; compute SNR
  - EMA for power/SNR
- Debounce with N‑of‑M persistence; write to SQLite; publish ZMQ events
- Provide synthetic mode (no hardware) generating test IQ for development; enable hardware via `RER_USE_HW=1`

Checklist:

- [x] HackRF capture loop with stable retune cadence
- [x] Band metrics with DC mitigation and parameters (`dc_guard_hz`, etc.)
- [x] EMA + N‑of‑M logic (with immediate 'new' on current SNR ≥ threshold)
- [x] Status endpoint hooks (via API) and graceful start/stop

---

## 6) API Gateway (FastAPI + Uvicorn)

Endpoints:

- `GET /candidates?limit=10`
- `POST /scan/start {plan}` / `POST /scan/stop` / `GET /scan/status`
- `POST /focus {freq_hz}` → `{video_url:"/video.mjpeg"}`; `DELETE /focus`
- `POST /record {type:"iq|video", enable:true}`
- `GET /config` / `PUT /config`
- `GET /health`
- `GET /events` (SSE bridge from ZMQ; chosen for MVP for simple, reliable push)

Notes:

- Scanning is paused during focus: `POST /focus` will stop the scanner, start demod + video bridge; `DELETE /focus` resumes scanner.

Checklist:

- [x] REST routes and models (scan, candidates, focus, record, health, config)
- [x] SSE endpoint (broadcast scan/focus/candidates)
- [ ] Process orchestration for scanner/demod/video bridge (spawn/terminate)
- [x] Start/Pause scan control exposed for the frontend

---

## 7) Frontend (Nuxt 3 + Tailwind)

Scaffold:

```
/frontend
  /components
    CandidateList.vue
    FocusViewer.vue
    SettingsPane.vue
  /composables
    useApi.ts        # REST calls to FastAPI
    useEvents.ts     # SSE subscription for candidate alerts
  /pages/index.vue   # single-page UI (table + focus panel)
  /assets/styles.css
  nuxt.config.ts     # proxy to backend
  tailwind.config.ts
  postcss.config.js
```

Behavior:

- Table lists top candidates from `GET /api/candidates` and refreshes via SSE `/events`.
- Buttons: Start Scan, Stop Scan; Focus per row; Stop Focus.
- Focus reveals `<img src="/video.mjpeg">`.
- Settings: upload channel plan JSON; toggles for IQ/video recording.
- Kiosk: Nitro build and `systemd` unit to serve on port 8081.

Checklist:

- [x] Nuxt 3 scaffold with Tailwind CSS configured
- [x] Nitro `routeRules` proxy for `/api/**`, `/events`, `/video.mjpeg`
- [x] `useApi.ts` and `useEvents.ts` composables (SSE)
- [x] `CandidateList.vue`, `FocusViewer.vue`, `SettingsPane.vue`
- [x] Start/Pause scan controls wired to API
- [x] Basic styling and responsive layout
- [x] Auto UI updates via SSE; correct focus start/stop behavior

---

## 8) Demod flowgraph (GNU Radio 3.10)

Chain:

```
HackRF Source (8 MS/s) → DC Block → AGC → LPF (~4 MHz) → Envelope → Clamp/De‑emphasis
→ Resampler (to manageable pixel clock) → Frame rate gate (25/30 fps) → ZMQ PUB (frames)
```

Notes:

- Command‑line args: `--freq`, `--sample-rate`, `--video-topic`, `--record-iq [path]`
- Audio subcarrier demod: omitted in MVP
- macOS development strategy: provide a Python mock demod that publishes synthetic frames over ZMQ so the UI/API can be developed without GNU Radio on macOS; on Pi, run the GNU Radio flowgraph.

Checklist:

- [ ] .grc flowgraph and generated Python entrypoint
- [x] ZMQ PUB of grayscale frames (8‑bit) — mock publisher `app/demod_mock.py` for development
- [ ] IQ file sink toggled by API

---

## 9) Video bridge (Python)

- ZMQ SUB to frames → JPEG encode (OpenCV) → serve MJPEG at `/video.mjpeg` (multipart/x‑mixed‑replace)
- Parameterize JPEG quality and chunk size to control latency

Checklist:

- [x] ZMQ subscriber
- [x] MJPEG HTTP endpoint
- [ ] Performance tuning (quality, buffering)

---

## 10) Orchestration and systemd

- Units:
  - `vtx-scanner.service` (scanner)
  - `vtx-api.service` (FastAPI + UI + video bridge optional)
  - `vtx-demod@.service` (templated per focus)
- Logging: journal + JSONL

Checklist:

- [x] Unit files with WorkingDirectory and ExecStart (see `scripts/setup_pi.sh` → `rer-api.service`)
- [x] Autostart policy and restart on failure
- [ ] Optional: combine API and video bridge into one service for simplicity
  - Includes `ExecStopPost` cleanup hook to clear HackRF streams
  - Later: add a templated `vtx-demod@.service` that runs GNU Radio flowgraph per focus

---

## Parameter defaults (updated)

- 5.8 GHz channel_bw_hz: 6e6
- dc_guard_hz: 100e3–150e3
- dwell_ms: 30–40 for stable SNR; 15 for fast sweep
- ranking: SNR_mean (raw) and/or ema_snr for smoothing


---

## Immediate next steps (short list)

1. Build GNU Radio flowgraph for analog demod; wrap with a small CLI; integrate start/stop from API (replace mock demod).
2. Expose `snr_peak_db` in API candidates (optional) and consider ordering by `snr_db` (mean) with ema_snr as a secondary field.
3. Tune gains/dwell/BW on Pi and validate detection at 5658 with SNR_mean ≥ 10; lock defaults.
4. Performance pass on MJPEG bridge (JPEG quality/frame rate) and UI.

---

## 11) Bench test and tuning

- Inject known analog signal (e.g., VTX at 5806 MHz)
- Verify candidate appears with SNR ≥ threshold and stabilizes (EMA)
- Focus → MJPEG preview within ~2 s; observe latency and stability
- Adjust `dwell_ms`, `dc_guard_hz`, JPEG quality, and process priority if needed

Checklist:

- [ ] Sweep time ≤ 1.5 s across configured channels
- [ ] Preview end‑to‑end latency ≤ 250 ms (target; OK if slightly higher on first pass)
- [ ] Recording produces files and does not disrupt preview

---

## Parallelization (to go ASAP)

- Implement 5), 6), and 9) in parallel after 2)–4) are stubbed.
- Start with synthetic IQ and mock demod to build UI/API loop on macOS while hardware is idle.
- Keep the GNU Radio flowgraph minimal for the first preview; switch from mock demod to GNU Radio on the Pi.

---

## Decisions (locked and remaining)

Locked by you:

- **UI**: Nuxt 3 + Tailwind; kiosk mode later.
- **Audio**: not needed for MVP.
- **Events**: choose best UX; we will use **SSE** in MVP (simple and reliable), with an easy path to WebSocket if required.
- **Focus policy**: scanning pauses during focus.
- **Controls**: Start/Pause scanning in the app.
- **Auth**: none (LAN‑only assumed).
- **GNU Radio portability**: develop on macOS with a mock demod; run GNU Radio on the Pi.

Remaining to confirm:

- **Recording format**: preferred IQ file type (`.cfile` complex64 vs `.cs16` int16) and retention/rotation policy.
- **Hardware specifics**: exact Pi model/cooling; BPF + LNA for 5.8 GHz initially?
- **GNU Radio install on Pi**: distro packages vs build from source (we will default to distro packages if available).
- **Autostart behavior**: start scanning on boot with a default plan vs manual `/scan/start`.

---

## Risks and mitigations

- **Retune latency on HackRF**: if sweeps exceed 1.5 s, reduce channel set or adopt adaptive dwell; consider SoapySDR driver.
- **CPU contention (FFT/JPEG/GNURadio)**: use arm64 BLAS, lower JPEG quality, cap frame rate, reduce UI refresh.
- **gr‑zeromq availability**: verify package; if missing, publish frames from Python demod or switch to file/pipe.
- **Single SDR tradeoff**: suspend or throttle scanning during focus to keep preview stable.

---

## Acceptance checklist

- [ ] Device boots; services up via systemd; `/health` returns OK
- [ ] `/` shows empty table; starting scan populates candidates within 2 sweeps
- [ ] Focus on a strong analog VTX shows MJPEG preview within ~2 s
- [ ] Stopping focus returns to full scanning
- [ ] IQ recording toggles and files are produced

